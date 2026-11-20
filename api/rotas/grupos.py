"""Grupos de eventos (D24): baixar a frequência e apagar as folhas de vários eventos de uma vez.

Um grupo junta as ocorrências de uma mesma edição de algo maior — todos os eventos dos Encontros
Universitários no grupo "EU". Ele é um campo do evento (`Evento.grupo`), e a lista de grupos é a
dos nomes em uso: não há cadastro de grupo à parte, e um grupo some quando o último evento sai.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from ..db import dependencia_sessao
from ..esquemas import (GrupoEntrada, GrupoSaida, ModeloGrupoEntrada, ModeloGrupoSaida, LinhaDoGrupo, PedidoApagarFolhas, PedidoGrupo,
                        PreviaApagar, ultima_revisao)
from ..tabelas import Evento, Folha, Grupo, Linha, Participante, Revisao
from .folhas import (apagar_em_massa, para_revisao, registro_da_folha,
                     selecionar_para_apagar)

rotas = APIRouter(prefix="/grupos", tags=["grupos"])

# Uma linha por assinatura (decisão do autor em 18/11): (chave no JSON, título na planilha)
COLUNAS = (("grupo", "grupo"), ("chave", "chave"), ("evento", "evento"), ("local", "local"),
           ("inicio", "início"), ("folha", "folha"), ("pagina", "página"), ("linha", "linha"),
           ("situacao", "situação"), ("matricula", "matrícula"), ("nome", "nome"),
           ("curso", "curso"), ("folha_concluida", "folha concluída"),
           ("duplicada_na_folha", "duplicada na folha"))


def situacao_da_linha(decisao: str, sugerida: str | None, revisao: Revisao | None
                      ) -> tuple[str, str | None]:
    """(situação, matrícula) de uma linha lida, para a frequência.

    A última revisão humana vence a decisão do sistema, inclusive a anulação, como no resumo
    da folha (`folhas._resumir`); sem revisão, vale o gate. Linha em quarentena e não revisada
    fica pendente: ainda não é presença nem ausência. "Não identificado" é a revisão que não
    achou a pessoa na base (`matricula_escolhida` nula).
    """
    if revisao is not None:
        if revisao.matricula_escolhida:
            return "presente (revisado)", revisao.matricula_escolhida
        if revisao.matricula_informada:                    # D32
            return "presente (fora da base)", revisao.matricula_informada
        return "não identificado", None
    if decisao == "anulada":
        return "anulada", None
    if decisao == "aceito":
        return "presente (automático)", sugerida
    return "pendente de revisão", None


def _eventos_do_grupo(db: Session, nome: str) -> list[Evento]:
    eventos = list(db.scalars(select(Evento).where(Evento.grupo == nome)
                              .options(selectinload(Evento.folhas))
                              .order_by(Evento.inicio.asc().nullslast(), Evento.chave)))
    if not eventos:
        raise HTTPException(404, f"nenhum evento no grupo {nome!r}")
    return eventos


def _folhas_em_ordem(evento: Evento) -> list[Folha]:
    """As folhas que CONTAM, em ordem. Outro escaneamento do mesmo papel (D30) fica de fora:
    a frequência, o JSON e a quarentena do grupo contam o papel uma vez só."""
    return sorted((f for f in evento.folhas if f.copia_de is None),
                  key=lambda f: (f.nome_original, f.pagina, f.id))


def linhas_da_frequencia(db: Session, nome: str) -> list[dict]:
    """A frequência do grupo, uma linha por assinatura lida, na ordem evento → folha → linha."""
    pessoas = {p.matricula: p for p in db.scalars(select(Participante))}
    saida = []
    for ev in _eventos_do_grupo(db, nome):
        for folha in _folhas_em_ordem(ev):
            linhas = db.scalars(select(Linha).where(Linha.folha_id == folha.id)
                                .options(selectinload(Linha.revisoes)).order_by(Linha.numero))
            for linha in linhas:
                ultima = ultima_revisao(linha)
                situacao, mat = situacao_da_linha(linha.decisao, linha.matricula_sugerida, ultima)
                pessoa = pessoas.get(mat) if mat else None
                # D32: quem não está na base vem com o nome que a revisão conferiu
                nome_fora = (ultima.nome_informado
                             if ultima is not None and ultima.matricula_informada else None)
                saida.append({
                    "grupo": nome, "chave": ev.chave, "evento": ev.nome, "local": ev.local,
                    "inicio": ev.inicio.isoformat(sep=" ", timespec="minutes") if ev.inicio else None,
                    "folha": folha.nome_original, "pagina": folha.pagina, "linha": linha.numero,
                    "situacao": situacao, "matricula": mat,
                    "nome": pessoa.nome if pessoa else nome_fora,
                    "curso": pessoa.curso if pessoa else None,
                    "folha_concluida": folha.revisada_em is not None,
                    "duplicada_na_folha": linha.duplicada_na_folha,
                })
    return saida


def _anexo(nome: str, extensao: str) -> str:
    """Content-Disposition com o nome do grupo: ASCII de reserva e o nome real em UTF-8."""
    reserva = re.sub(r"[^A-Za-z0-9_-]+", "_", nome).strip("_") or "grupo"
    return (f'attachment; filename="frequencia_{reserva}.{extensao}"; '
            f"filename*=UTF-8''{quote(f'frequencia_{nome}.{extensao}')}")


def garantir_grupo(db: Session, nome: str | None) -> str:
    """O grupo pelo nome, criado se ainda não existir (D35). Todo evento pertence a um grupo.

    Criar sob demanda mantém o cadastro em lote e o formulário de evento funcionando com um
    nome novo; a tela de grupos é o caminho principal, não o único.
    """
    if not nome:
        raise HTTPException(422, "todo evento pertence a um grupo: escolha ou crie um")
    if db.scalar(select(Grupo.id).where(Grupo.nome == nome)) is None:
        db.add(Grupo(nome=nome))
        db.flush()
    return nome


def _resumos(db: Session, nomes: list[str] | None = None) -> list[dict]:
    """Os grupos com as contagens e a etapa. Grupo sem evento aparece também (D35)."""
    grupos = list(db.scalars(select(Grupo).order_by(Grupo.nome) if nomes is None else
                             select(Grupo).where(Grupo.nome.in_(nomes)).order_by(Grupo.nome)))
    revisadas = func.count(Folha.id).filter(Folha.revisada_em.is_not(None))
    abertos = func.count(func.distinct(Evento.id)).filter(Evento.situacao == "em andamento")
    contas = {g: (e, f, r, a) for g, e, f, r, a in db.execute(
        select(Evento.grupo, func.count(func.distinct(Evento.id)), func.count(Folha.id),
               revisadas, abertos)
        .outerjoin(Folha, Folha.evento_id == Evento.id)
        .where(Evento.grupo.is_not(None)).group_by(Evento.grupo))}
    # D30: cópias e leituras únicas; D31: o que está na fila — numa passada só
    por_grupo: dict[str, list[tuple[int, int | None, str]]] = {}
    for grupo, fid, copia_de, estado in db.execute(
            select(Evento.grupo, Folha.id, Folha.copia_de, Folha.estado)
            .join(Folha, Folha.evento_id == Evento.id).where(Evento.grupo.is_not(None))):
        por_grupo.setdefault(grupo, []).append((fid, copia_de, estado))
    decididas = select(Revisao.linha_id).distinct().scalar_subquery()
    pendentes = dict(db.execute(
        select(Evento.grupo, func.count(Linha.id))
        .join(Folha, Folha.evento_id == Evento.id).join(Linha, Linha.folha_id == Folha.id)
        .where(Folha.copia_de.is_(None), Linha.decisao == "quarentena",
               Linha.id.not_in(decididas))
        .group_by(Evento.grupo)).all())

    saida = []
    for g in grupos:
        e, f, r, a = contas.get(g.nome, (0, 0, 0, 0))
        folhas = por_grupo.get(g.nome, [])
        alvos = {c for _, c, _ in folhas if c is not None}
        contam = [fid for fid, c, _ in folhas if c is None]
        pend = pendentes.get(g.nome, 0)
        if e and not a:
            etapa = "encerrado"
        elif not f:
            etapa = "preparando"
        elif any(est in ("recebida", "processando") for _, _, est in folhas):
            etapa = "recebendo"
        elif pend:
            etapa = "revisando"
        else:
            etapa = "pronto"
        saida.append({"nome": g.nome, "descricao": g.descricao, "eventos": e, "folhas": f,
                      "folhas_revisadas": r, "eventos_abertos": a, "pendentes": pend,
                      "etapa": etapa,
                      "copias": sum(1 for _, c, _ in folhas if c is not None),
                      "com_copia": sum(1 for fid in contam if fid in alvos),
                      "leitura_unica": sum(1 for fid in contam if fid not in alvos)})
    return saida


@rotas.get("", response_model=list[GrupoSaida])
def listar(db: Session = Depends(dependencia_sessao)) -> list[dict]:
    return _resumos(db)


@rotas.post("", response_model=GrupoSaida, status_code=201)
def criar(dados: GrupoEntrada, db: Session = Depends(dependencia_sessao)) -> dict:
    """Cria um grupo vazio (D35): o primeiro passo do fluxo, antes dos eventos."""
    if db.scalar(select(Grupo.id).where(Grupo.nome == dados.nome)) is not None:
        raise HTTPException(409, f"já existe um grupo chamado {dados.nome!r}")
    db.add(Grupo(nome=dados.nome, descricao=dados.descricao))
    db.commit()
    return _resumos(db, [dados.nome])[0]


@rotas.get("/{nome}", response_model=GrupoSaida)
def obter(nome: str, db: Session = Depends(dependencia_sessao)) -> dict:
    achado = _resumos(db, [nome])
    if not achado:
        raise HTTPException(404, f"grupo {nome!r} não existe")
    return achado[0]


@rotas.patch("/{nome}", response_model=GrupoSaida)
def renomear(nome: str, dados: GrupoEntrada, db: Session = Depends(dependencia_sessao)) -> dict:
    """Renomeia (os eventos acompanham, pela cascata) e troca a descrição."""
    grupo = db.scalar(select(Grupo).where(Grupo.nome == nome))
    if grupo is None:
        raise HTTPException(404, f"grupo {nome!r} não existe")
    if dados.nome != nome:
        if db.scalar(select(Grupo.id).where(Grupo.nome == dados.nome)) is not None:
            raise HTTPException(409, f"já existe um grupo chamado {dados.nome!r}")
        # a cascata é do Postgres; o UPDATE explícito cobre o SQLite dos testes
        grupo.nome = dados.nome
        db.flush()
        db.execute(update(Evento).where(Evento.grupo == nome).values(grupo=dados.nome))
        db.execute(update(Folha).where(Folha.grupo_envio == nome)
                   .values(grupo_envio=dados.nome))
    grupo.descricao = dados.descricao
    db.commit()
    return _resumos(db, [dados.nome])[0]


@rotas.delete("/{nome}", status_code=204)
def apagar(nome: str, db: Session = Depends(dependencia_sessao)) -> None:
    """Só grupo sem eventos: com eventos, o caminho é encerrar (D33)."""
    grupo = db.scalar(select(Grupo).where(Grupo.nome == nome))
    if grupo is None:
        raise HTTPException(404, f"grupo {nome!r} não existe")
    n = db.scalar(select(func.count()).select_from(Evento).where(Evento.grupo == nome))
    if n:
        raise HTTPException(409, f"o grupo tem {n} evento(s); encerre em vez de apagar")
    db.delete(grupo)
    db.commit()


@rotas.post("/{nome}/situacao")
def mudar_situacao(nome: str, situacao: str = Query(...),
                   confirmacao: str | None = Query(None),
                   db: Session = Depends(dependencia_sessao)) -> dict:
    """Encerra ou reabre todos os eventos do grupo, todos ou nenhum (D33).

    Encerrar libera as chaves para outro grupo ou evento usar. Revisar e baixar a frequência
    continuam funcionando: encerrar só tira as chaves do casamento da leitura.
    """
    from .eventos import SITUACOES, mudar_situacoes
    if situacao not in SITUACOES:
        raise HTTPException(422, f"situação tem que ser uma de {SITUACOES}")
    # D34: encerrar o grupo inteiro pede o nome dele digitado, como o apagar em massa pede o
    # número. É o que separa a decisão do clique solto. Reabrir não pede: é o desfazer.
    if situacao == "encerrado" and (confirmacao or "").strip() != nome:
        raise HTTPException(422, f"para encerrar o grupo, digite o nome dele: {nome}")
    eventos = _eventos_do_grupo(db, nome)
    religadas = mudar_situacoes(db, eventos, situacao)
    db.commit()
    return {"grupo": nome, "situacao": situacao, "eventos": len(eventos),
            "folhas_religadas": religadas}


@rotas.post("/eventos")
def por_no_grupo(pedido: PedidoGrupo, db: Session = Depends(dependencia_sessao)) -> dict:
    """Põe (ou move) os eventos no grupo. Todos ou nenhum."""
    achados = set(db.scalars(select(Evento.id).where(Evento.id.in_(pedido.evento_ids))))
    faltam = sorted(set(pedido.evento_ids) - achados)
    if faltam:
        raise HTTPException(404, f"evento(s) inexistente(s): {faltam}")
    # D35: todo evento pertence a um grupo; "tirar do grupo" virou "mover para outro"
    grupo = garantir_grupo(db, pedido.grupo)
    db.execute(update(Evento).where(Evento.id.in_(achados)).values(grupo=grupo))
    db.commit()
    return {"alterados": len(achados), "grupo": pedido.grupo}


@rotas.get("/{nome}/frequencia.json")
def frequencia_json(nome: str, db: Session = Depends(dependencia_sessao)) -> JSONResponse:
    """A frequência do grupo, e o registro completo de cada folha com a procedência."""
    eventos = _eventos_do_grupo(db, nome)
    dados = {
        "grupo": nome,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frequencia": linhas_da_frequencia(db, nome),
        "eventos": [{
            "chave": ev.chave, "nome": ev.nome, "local": ev.local, "situacao": ev.situacao,
            "inicio": ev.inicio.isoformat() if ev.inicio else None,
            "fim": ev.fim.isoformat() if ev.fim else None,
            "folhas": [registro_da_folha(db, f) for f in _folhas_em_ordem(ev)],
        } for ev in eventos],
    }
    return JSONResponse(dados, headers={"Content-Disposition": _anexo(nome, "json")})


@rotas.get("/{nome}/frequencia.xlsx")
def frequencia_xlsx(nome: str, db: Session = Depends(dependencia_sessao)) -> Response:
    """A frequência do grupo numa planilha só: uma aba, uma linha por assinatura."""
    linhas = linhas_da_frequencia(db, nome)
    # import tardio: o openpyxl só pesa a quem baixa planilha
    from openpyxl import Workbook
    from openpyxl.styles import Font

    livro = Workbook()
    aba = livro.active
    aba.title = "frequência"
    aba.append([titulo for _, titulo in COLUNAS])
    for celula in aba[1]:
        celula.font = Font(bold=True)
    for linha in linhas:
        aba.append(["sim" if linha[c] is True else "não" if linha[c] is False else linha[c]
                    for c, _ in COLUNAS])
    aba.freeze_panes = "A2"
    aba.auto_filter.ref = aba.dimensions
    for coluna, largura in zip("ABCDEFGHIJKLMN", (10, 12, 30, 20, 17, 30, 7, 6, 22, 11, 40, 30,
                                                   15, 17)):
        aba.column_dimensions[coluna].width = largura
    corpo = io.BytesIO()
    livro.save(corpo)
    return Response(corpo.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": _anexo(nome, "xlsx")})


@rotas.get("/{nome}/quarentena", response_model=list[LinhaDoGrupo])
def quarentena(nome: str, db: Session = Depends(dependencia_sessao)) -> list[dict]:
    """Toda linha em quarentena das folhas do grupo, numa lista só, na ordem evento → folha →
    linha. Vêm também as já decididas: a tela escolhe o que mostrar, e quem quer conferir
    uma decisão tomada folha a folha precisa vê-la aqui.

    Concluir continua sendo por folha: o carimbo diz que AQUELA folha foi conferida inteira,
    e a quarentena é só uma parte dela.
    """
    pares = []
    for ev in _eventos_do_grupo(db, nome):
        for folha in _folhas_em_ordem(ev):
            linhas = list(db.scalars(
                select(Linha).where(Linha.folha_id == folha.id,
                                    Linha.decisao == "quarentena")
                .options(selectinload(Linha.revisoes)).order_by(Linha.numero)))
            if linhas:
                pares.append((ev, folha, linhas))
    montadas = para_revisao(db, [(f, ls) for _, f, ls in pares])
    origens = [(ev, folha) for ev, folha, linhas in pares for _ in linhas]
    return [{**linha, "evento_chave": ev.chave, "evento_nome": ev.nome,
             "folha_nome": folha.nome_original, "folha_pagina": folha.pagina,
             "folha_concluida": folha.revisada_em is not None}
            for linha, (ev, folha) in zip(montadas, origens, strict=True)]


@rotas.get("/{nome}/apagar-folhas", response_model=PreviaApagar)
def previa_apagar(nome: str, incluir_revisadas: bool = False,
                  db: Session = Depends(dependencia_sessao)) -> dict:
    """Quantas folhas do grupo sairiam, e quantas ficam. Não apaga nada."""
    folhas = [f for ev in _eventos_do_grupo(db, nome) for f in ev.folhas]
    saem, revisadas, na_fila = selecionar_para_apagar(folhas, incluir_revisadas)
    return {"apagar": len(saem), "revisadas_mantidas": revisadas, "na_fila_mantidas": na_fila}


@rotas.post("/{nome}/apagar-folhas", response_model=PreviaApagar)
def apagar_do_grupo(nome: str, pedido: PedidoApagarFolhas,
                    db: Session = Depends(dependencia_sessao)) -> dict:
    """Apaga as folhas dos eventos do grupo, pela mesma regra do apagar-todas. Os eventos e o
    grupo ficam. Na resposta, `apagar` é quantas saíram."""
    folhas = [f for ev in _eventos_do_grupo(db, nome) for f in ev.folhas]
    return apagar_em_massa(db, folhas, pedido)


# --- D35, fase 3: o modelo da folha do grupo ----------------------------------------------

FORMATO_PACOTE = "olhodigital-pacote"


def _grupo_ou_404(db: Session, nome: str) -> Grupo:
    grupo = db.scalar(select(Grupo).where(Grupo.nome == nome))
    if grupo is None:
        raise HTTPException(404, f"grupo {nome!r} não existe")
    return grupo


def modelo_do_grupo(lf, grupo: Grupo):
    """O cabeçalho que vale no grupo: o dele ou, sem modelo próprio, o padrão salvo."""
    return lf.gf.Cabecalho(**grupo.cabecalho) if grupo.cabecalho else lf.carregar_cabecalho()


def _ajustes_abertos(db: Session, nome: str) -> dict[str, dict]:
    return {e.chave: e.cabecalho_ajuste for e in db.scalars(
        select(Evento).where(Evento.grupo == nome, Evento.situacao == "em andamento",
                             Evento.cabecalho_ajuste.is_not(None)).order_by(Evento.chave))}


@rotas.get("/{nome}/modelo", response_model=ModeloGrupoSaida)
def obter_modelo(nome: str, db: Session = Depends(dependencia_sessao)) -> dict:
    from dataclasses import asdict
    from .impressao import _lote
    lf = _lote()
    grupo = _grupo_ou_404(db, nome)
    return {"cabecalho": asdict(modelo_do_grupo(lf, grupo)), "proprio": bool(grupo.cabecalho),
            "limite_caracteres": lf.gf.LIMITE_CARACTERES_CABECALHO,
            "travado": list(lf.PARTES_TRAVADAS), "ajustes": _ajustes_abertos(db, nome)}


@rotas.put("/{nome}/modelo", response_model=ModeloGrupoSaida)
def salvar_modelo(nome: str, dados: ModeloGrupoEntrada,
                  db: Session = Depends(dependencia_sessao)) -> dict:
    """Salva o modelo do grupo, ou volta ao padrão (nulo). Passa pelo portão da folha (D16), e
    os ajustes que os eventos já têm têm que continuar cabendo com o texto novo."""
    from .impressao import _lote
    lf = _lote()
    grupo = _grupo_ou_404(db, nome)
    if dados.cabecalho is None:
        grupo.cabecalho = None
    else:
        base = lf.gf.Cabecalho(**dados.cabecalho.model_dump())
        try:
            lf.conferir_cabecalho(base)
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        for chave, ajuste in _ajustes_abertos(db, nome).items():
            try:
                lf.conferir_cabecalho(lf.combinar(base, ajuste))
            except ValueError as erro:
                raise HTTPException(
                    422, f"com este modelo, o ajuste do evento {chave} deixa de caber: "
                         f"{erro}") from erro
        grupo.cabecalho = dados.cabecalho.model_dump()
    db.commit()
    return obter_modelo(nome, db)


@rotas.get("/{nome}/pacote")
def baixar_pacote(nome: str, db: Session = Depends(dependencia_sessao)) -> JSONResponse:
    """O pacote de impressão do grupo, para o PC de quem imprime (D19): o modelo, os ajustes
    por evento e as chaves dos eventos abertos, com as folhas previstas e o dia do carimbo.
    Não leva dado pessoal: só o que vai impresso na folha."""
    from dataclasses import asdict
    from .impressao import _lote
    lf = _lote()
    grupo = _grupo_ou_404(db, nome)
    dados = {
        "formato": FORMATO_PACOTE, "versao": 1, "grupo": nome,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cabecalho": asdict(modelo_do_grupo(lf, grupo)),
        "ajustes": _ajustes_abertos(db, nome),
        "itens": [{**i, "dia": i["dia"].isoformat() if i["dia"] else None}
                  for i in _itens_para_imprimir(db, nome)],
    }
    return JSONResponse(dados, headers={"Content-Disposition": _anexo(nome, "json").replace(
        "frequencia_", "pacote_")})



def _itens_para_imprimir(db: Session, nome: str) -> list[dict]:
    """As folhas que o grupo imprime: um item por evento ABERTO, com as folhas previstas (1 sem
    previsão) e o dia do carimbo (D20). Encerrado não se imprime: a chave dele está livre."""
    eventos = list(db.scalars(select(Evento).where(Evento.grupo == nome,
                                                   Evento.situacao == "em andamento")
                              .order_by(Evento.inicio.asc().nullslast(), Evento.chave)))
    if not eventos:
        raise HTTPException(409, "o grupo não tem eventos abertos para imprimir")
    return [{"linha": i, "chave": e.chave, "folhas": e.folhas_previstas or 1,
             "nome": e.nome, "local": e.local, "responsavel": e.responsavel,
             "dia": e.inicio.date() if e.inicio else None}
            for i, e in enumerate(eventos, start=1)]


@rotas.get("/{nome}/pdf")
def pdf_do_grupo(nome: str, db: Session = Depends(dependencia_sessao)) -> Response:
    """Todas as folhas do grupo num PDF só, para imprimir (pedido do autor, 20/11).

    Um item por evento aberto, com as folhas previstas, o modelo do grupo e o texto de cada
    evento (D35, fase 3). Passa pelos mesmos portões da impressão (D16, D10). O alerta de chaves
    parecidas não trava: as chaves já estão cadastradas, e aqui a intenção é só imprimir.

    Rota síncrona de propósito: roda numa thread do servidor, e um grupo de 150 eventos leva
    alguns minutos sem parar o resto do sistema (a lição da conversão de PDF, D31).
    """
    import tempfile
    from pathlib import Path
    from .impressao import _lote
    lf = _lote()
    grupo = _grupo_ou_404(db, nome)
    itens = [lf.ItemLote(**i) for i in _itens_para_imprimir(db, nome)]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "grupo.pdf"
        try:
            resumo = lf.gerar_pdf(itens, modelo_do_grupo(lf, grupo), str(caminho),
                                  aceitar_alertas=True, ajustes=_ajustes_abertos(db, nome))
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        corpo = caminho.read_bytes()
    reserva = re.sub(r"[^A-Za-z0-9_-]+", "_", nome).strip("_") or "grupo"
    arquivo = f"folhas_{nome}_{resumo['paginas']}paginas.pdf"
    return Response(corpo, media_type="application/pdf", headers={
        "Content-Disposition": (f'attachment; filename="folhas_{reserva}.pdf"; '
                                f"filename*=UTF-8''{quote(arquivo)}"),
        "X-Paginas": str(resumo["paginas"])})


@rotas.get("/{nome}/chaves-sugeridas")
def chaves_sugeridas(nome: str, n: int = Query(8, ge=1, le=40),
                     db: Session = Depends(dependencia_sessao)) -> list[dict]:
    """Chaves livres e seguras para os eventos do grupo (D35, fase 4): sem V, nunca usadas, a
    4 de toda chave aberta e entre si. Ver `experimento-imagem/sugerir_chaves.py`."""
    from .. import pipeline
    _grupo_ou_404(db, nome)
    pipeline.preparar()
    from sugerir_chaves import sugerir                             # noqa: E402
    usadas = set(db.scalars(select(Evento.chave)))
    abertas = set(db.scalars(select(Evento.chave).where(Evento.situacao == "em andamento")))
    return sugerir(usadas, abertas, n)
