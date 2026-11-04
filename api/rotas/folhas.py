"""Upload e leitura de folhas.

O upload **não processa e responde**: a leitura leva ~9 s por folha (medido em
30/08: 26,5 s para três), o que não cabe numa requisição HTTP e muito menos em
500. Então `POST /folhas` grava, enfileira e devolve as folhas criadas; o
cliente acompanha por `GET /folhas/{id}` até o estado virar `processada`.

Um envio pode criar **várias** folhas: o escâner entrega PDF, e cada página é
uma folha. Por isso a rota devolve uma lista mesmo quando o arquivo é uma
imagem só — o contrato não muda conforme o que foi enviado.

`BackgroundTasks` do FastAPI basta aqui, e a escolha é deliberada: Celery e
Redis resolveriam o mesmo problema somando dois serviços ao caminho de
reprodução, contra a promessa de execução local da pergunta de pesquisa. O
paralelismo que eles trariam seria inútil de qualquer jeito — o pipeline não é
reentrante e já roda serializado (ver `pipeline._TRAVA`).
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, UploadFile)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import documentos, pipeline
from ..config import config
from ..db import dependencia_sessao, sessao
from ..esquemas import (EventoSugerido, FolhaSaida, LinhaSaida, ResumoRevisao,
                        SugestaoEventos, VinculoEntrada, montar_folha,
                        montar_linha)
from ..tabelas import Evento, Folha, Linha, Participante, Revisao

rotas = APIRouter(prefix="/folhas", tags=["folhas"])
log = logging.getLogger("olhodigital")

# Decisão de gente nunca é desfeita por uma releitura.
VINCULO_HUMANO = ("informado", "confirmado", "corrigido")


def _eventos_abertos(db: Session) -> list[Evento]:
    """O contradomínio da chave: encerrar um evento o tira daqui (D15)."""
    return list(db.scalars(select(Evento)
                           .where(Evento.situacao == "em andamento")))


def _vincular_evento(db: Session, folha: Folha) -> None:
    """Lê a chave e liga a folha ao evento, se o gate deixar (D17).

    Falhar aqui não derruba a folha: as linhas já foram lidas, e sem evento ela
    vai para a bandeja, que é onde folha sem vínculo deve estar.
    """
    try:
        folha.chave_lida = pipeline.ler_chave(Path(folha.arquivo),
                                              folha.modelo_folha)
    except Exception as e:                                  # noqa: BLE001
        log.warning("folha %s: chave não lida (%s: %s)", folha.id,
                    type(e).__name__, e)
        folha.chave_lida = None
    if folha.origem_vinculo in VINCULO_HUMANO:
        return
    abertos = {e.chave: e.id for e in _eventos_abertos(db)}
    achado = pipeline.casar_chave(folha.chave_lida, list(abertos))
    folha.evento_id = abertos.get(achado.chave)
    folha.origem_vinculo = "automatico" if achado.chave else None


def _ler_e_gravar(folha_id: int) -> None:
    """Roda o pipeline e grava as linhas. Executa fora da requisição.

    Abre a PRÓPRIA sessão: a da requisição já foi fechada quando isto roda.
    """
    db = sessao()
    try:
        folha = db.get(Folha, folha_id)
        if folha is None:
            return
        folha.estado = "processando"
        db.commit()

        caminho = Path(folha.arquivo)
        # Antes de qualquer coisa: isto é uma folha de presença? Quem envia 500
        # arquivos precisa distinguir "mandei o arquivo errado" de "a leitura
        # falhou", e as duas coisas viravam o mesmo traceback do OpenCV.
        encontrados = pipeline.conferir_marcadores(caminho)
        if encontrados < 4:
            folha.estado = "nao_e_folha"
            folha.erro = (
                f"{encontrados} de 4 marcadores ArUco encontrados — não parece "
                "uma folha de presença. Se for uma, a digitalização pode ter "
                "cortado os cantos ou ficado clara demais.")
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return

        try:
            recursos = pipeline.carregar_recursos()
            linhas = pipeline.processar(caminho, folha.modelo_folha)
            pipeline.recortar_para_revisao(caminho, folha.modelo_folha)
        except Exception as e:                      # noqa: BLE001
            # A folha não some e o erro fica no registro: com 500 folhas, uma
            # que falha em silêncio é uma presença perdida sem rastro.
            folha.estado = "erro"
            folha.erro = f"{type(e).__name__}: {e}"
            db.commit()
            return

        for dados in linhas:
            db.add(Linha(folha_id=folha.id, **dados))

        folha.linhas_preenchidas = len(linhas)
        folha.linhas_anuladas = sum(1 for l in linhas if l["decisao"] == "anulada")

        # PORTÃO CONTRA GEOMETRIA ERRADA.
        #
        # Recortar com o modelo errado não falha: produz a grade inteira cheia
        # de lixo e diz "processada". Medido em 30/08, subindo folhas antigas
        # como `gerada_v2`: m3_2 deu 33 linhas (tem 22) com 0 aceitos, m4_01
        # deu 33 (tem 7) com 0 aceitos, m1_1 deu 33 com 0. Uma folha lida com a
        # geometria certa aceita a grande maioria — a m7_02 fez 14 de 16.
        #
        # Zero aceitos com várias linhas é a assinatura. Não é prova (uma folha
        # pode legitimamente ser toda ambígua), por isso o estado é `suspeita` e
        # não `erro`: os dados ficam, a revisão continua possível, e ninguém
        # confunde isto com leitura boa.
        aceitos = sum(1 for l in linhas if l["decisao"] == "aceito")
        total_grade = pipeline.linhas_da_grade(folha.modelo_folha)
        # DOIS sinais, porque um só não bastava. "Zero aceites" pega o
        # desencontro grosseiro (folha antiga lida como v4: 33 linhas, 0
        # aceites). Não pega o quase-acerto — a m7_02 lida como `gerada_v1` deu
        # 39 linhas e ainda assim teve aceites, porque as duas geometrias são
        # parecidas. O segundo sinal é a grade INTEIRA vir preenchida: folha
        # real tem linha em branco no fim (a m7_02 usa 16 de 33), e ler todas
        # as linhas da grade como preenchidas é o que acontece quando o
        # recorte pega traço onde não há campo.
        motivo = ""
        if len(linhas) >= 5 and aceitos == 0:
            motivo = f"{len(linhas)} linhas e nenhum aceite"
        elif total_grade and len(linhas) == total_grade:
            motivo = (f"todas as {total_grade} linhas da grade vieram "
                      "preenchidas, sem nenhuma em branco no fim")
        if motivo:
            folha.estado = "suspeita"
            folha.erro = (
                f"{motivo} — assinatura de recorte com a geometria errada. "
                f"Esta folha foi lida como '{folha.modelo_folha}'; confira se é "
                "essa a versão dela e releia com a certa se não for.")
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return
        ponto = recursos["ponto"]
        folha.alpha = ponto["alpha"]
        folha.theta = ponto["theta"]
        folha.delta = ponto["delta"]
        folha.metrica_nome = ponto["metrica_nome"]
        folha.modelo_matricula = recursos["modelo_matricula"]
        folha.modelo_letras = recursos["modelo_letras"]
        _vincular_evento(db, folha)
        folha.estado = "processada"
        folha.processada_em = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


@rotas.post("", response_model=list[FolhaSaida], status_code=201)
async def enviar(tarefas: BackgroundTasks,
                 arquivo: UploadFile = File(...),
                 evento_id: int | None = Form(None),
                 modelo_folha: str = Form(config.modelo_folha_padrao),
                 db: Session = Depends(dependencia_sessao)) -> list[Folha]:
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "arquivo vazio")

    origem = documentos.sha(conteudo)
    # Reenviar o mesmo arquivo dobraria a frequência de todo mundo nele.
    # Devolve o que já existe em vez de criar — e o banco tem a mesma
    # restrição, para dois uploads simultâneos não passarem os dois.
    ja = list(db.scalars(select(Folha).where(Folha.origem_sha256 == origem)
                         .order_by(Folha.pagina)))
    if ja:
        return ja

    try:
        imagens = documentos.paginas(conteudo, config.pasta_folhas, origem)
    except RuntimeError as e:
        raise HTTPException(422, str(e)) from e

    criadas = []
    for numero, imagem in enumerate(imagens, start=1):
        folha = Folha(
            evento_id=evento_id,
            origem_vinculo="informado" if evento_id is not None else None,
            nome_original=arquivo.filename or "sem nome",
            arquivo=str(imagem),
            origem_sha256=origem,
            pagina=numero,
            sha256=documentos.sha(imagem.read_bytes()),
            modelo_folha=modelo_folha,
            estado="recebida",
        )
        db.add(folha)
        criadas.append(folha)
    db.commit()

    for folha in criadas:
        tarefas.add_task(_ler_e_gravar, folha.id)
    return criadas


def _contadores(db: Session, ids: list[int]) -> dict[int, tuple[int, int]]:
    """Pendências e repetidas por folha, em UMA consulta.

    Sem isto a lista faria uma consulta por folha, e com 500 folhas a tela de
    abertura viraria 500 idas ao banco para desenhar uma etiqueta.

    Pendente é linha de quarentena **sem nenhuma revisão**. A tabela de revisão
    é append-only, então existir qualquer registro já significa que alguém
    decidiu — o que vale é o mais recente, e isso é problema de quem lê a linha.
    """
    if not ids:
        return {}
    decididas = select(Revisao.linha_id).distinct().scalar_subquery()
    linhas = db.execute(
        select(
            Linha.folha_id,
            func.count().filter((Linha.decisao == "quarentena")
                                & Linha.id.not_in(decididas)),
            func.count().filter(Linha.duplicada_na_folha.is_(True)),
        ).where(Linha.folha_id.in_(ids)).group_by(Linha.folha_id)
    ).all()
    return {fid: (pend, dup) for fid, pend, dup in linhas}


@rotas.get("", response_model=list[FolhaSaida])
def listar(evento_id: int | None = None,
           db: Session = Depends(dependencia_sessao)) -> list[dict]:
    # o evento vem junto: com 500 folhas, buscá-lo por folha viraria 500 consultas
    q = (select(Folha).options(selectinload(Folha.evento))
         .order_by(Folha.criado_em.desc(), Folha.pagina))
    if evento_id is not None:
        q = q.where(Folha.evento_id == evento_id)
    folhas = list(db.scalars(q))
    contagem = _contadores(db, [f.id for f in folhas])
    return [montar_folha(f, *contagem.get(f.id, (0, 0))) for f in folhas]


@rotas.get("/{folha_id}", response_model=FolhaSaida)
def obter(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    return montar_folha(folha, *_contadores(db, [folha_id]).get(folha_id, (0, 0)))


@rotas.delete("/{folha_id}", status_code=204)
def apagar(folha_id: int, forcar: bool = False,
           db: Session = Depends(dependencia_sessao)) -> None:
    """Remove a folha, suas linhas, suas revisões e os arquivos em disco.

    Apagar de verdade, e não marcar como removida, é o que a LGPD pede de um
    sistema que guarda matrícula e nome manuscritos: se a pessoa pedir a
    exclusão, o dado tem que sair do disco, não virar linha oculta.

    **Folha já revisada exige `forcar`.** Ela carrega a frequência apurada de
    quem assinou, e apagá-la por engano destrói registro de presença — que é o
    produto do sistema. O clique a mais é barato perto disso.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.revisada_em is not None and not forcar:
        raise HTTPException(
            409, "esta folha já foi revisada e carrega frequência apurada; "
                 "repita com forcar=true se for mesmo para apagar")

    imagem = Path(folha.arquivo)
    db.delete(folha)          # linhas e revisões saem por cascade
    db.commit()

    # Os arquivos saem DEPOIS do commit: se o banco falhar, o dado continua
    # íntegro. Na ordem inversa, um erro deixaria registro apontando para
    # imagem que não existe mais.
    shutil.rmtree(pipeline.pasta_recortes(imagem), ignore_errors=True)
    imagem.unlink(missing_ok=True)


@rotas.get("/{folha_id}/imagem")
def imagem(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """A folha inteira, para quando o recorte da linha não basta."""
    folha = db.get(Folha, folha_id)
    if folha is None or not Path(folha.arquivo).exists():
        raise HTTPException(404, "imagem não encontrada")
    return FileResponse(folha.arquivo, media_type="image/png")


@rotas.get("/{folha_id}/chave.jpg")
def recorte_chave(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """A banda da chave como foi escaneada: é o que a bandeja mostra."""
    folha = db.get(Folha, folha_id)
    caminho = folha and pipeline.caminho_tira_chave(Path(folha.arquivo))
    if not caminho or not caminho.exists():
        raise HTTPException(404, "recorte da chave não gerado para esta folha")
    return FileResponse(caminho, media_type="image/jpeg")


def _sugestao(db: Session, folha: Folha) -> SugestaoEventos:
    abertos = {e.chave: e for e in _eventos_abertos(db)}
    achado = pipeline.casar_chave(folha.chave_lida, list(abertos))
    return SugestaoEventos(
        motivo=achado.explicacao,
        candidatos=[EventoSugerido(id=abertos[c].id, chave=c,
                                   nome=abertos[c].nome, distancia=d)
                    for c, d in achado.candidatas])


@rotas.get("/{folha_id}/eventos-sugeridos", response_model=SugestaoEventos)
def eventos_sugeridos(folha_id: int,
                      db: Session = Depends(dependencia_sessao)) -> SugestaoEventos:
    """Os eventos abertos, do mais provável ao menos provável, para a bandeja."""
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    return _sugestao(db, folha)


@rotas.post("/{folha_id}/evento", response_model=FolhaSaida)
def vincular_evento(folha_id: int, dados: VinculoEntrada,
                    db: Session = Depends(dependencia_sessao)) -> dict:
    """A decisão humana sobre o evento da folha.

    `confirmado` quando concorda com o que a máquina apontava, `corrigido`
    quando discorda. É essa diferença que permite medir a chave depois.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if dados.evento_id is None:
        folha.evento_id = folha.origem_vinculo = None
    else:
        evento = db.get(Evento, dados.evento_id)
        if evento is None:
            raise HTTPException(404, "evento não encontrado")
        candidatos = _sugestao(db, folha).candidatos
        apontado = folha.evento_id or (candidatos[0].id if candidatos else None)
        folha.origem_vinculo = ("confirmado" if evento.id == apontado
                                else "corrigido")
        folha.evento_id = evento.id
    db.commit()
    return obter(folha_id, db)


@rotas.get("/{folha_id}/linhas", response_model=list[LinhaSaida])
def linhas(folha_id: int,
           db: Session = Depends(dependencia_sessao)) -> list[dict]:
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    achadas = list(db.scalars(select(Linha).where(Linha.folha_id == folha_id)
                              .order_by(Linha.numero)))

    # Vizinhos a um dígito, em UMA consulta para todas as linhas. É a medida de
    # perigo de A15: um erro de leitura transforma a matrícula de alguém na de
    # outra pessoa real, o score sai alto, a margem sai folgada, e nem θ nem δ
    # percebem. 62,6% da base tem ao menos um vizinho assim.
    candidatas: set[str] = set()
    for linha in achadas:
        candidatas |= _variacoes(linha.matricula_sugerida)
    reais = set(db.scalars(select(Participante.matricula)
                           .where(Participante.matricula.in_(candidatas)))) \
        if candidatas else set()

    # θ e δ vêm da FOLHA, não do `fuzzy`: são os que produziram estas decisões.
    # Explicar a rejeição com o limiar de hoje quando a leitura foi feita com o
    # de ontem daria uma explicação que não corresponde ao que aconteceu. O
    # `fuzzy` só entra como reserva, e importado aqui dentro — `experimento-fuzzy`
    # só existe no `sys.path` depois que o pipeline foi preparado.
    if folha.theta is None or folha.delta is None:
        pipeline.preparar()
        from fuzzy import THETA, DELTA                            # noqa: E402
    theta = folha.theta if folha.theta is not None else THETA
    delta = folha.delta if folha.delta is not None else DELTA
    return [montar_linha(l, sorted(_variacoes(l.matricula_sugerida) & reais),
                         theta, delta)
            for l in achadas]


def _resumir(linhas: list[Linha]) -> ResumoRevisao:
    """O que a folha virou. Uma passada, sem consulta nova."""
    identificados = nao_ident = anuladas = pendentes = duplicadas = 0
    for linha in linhas:
        ultima = max(linha.revisoes, key=lambda r: r.criado_em, default=None)
        if linha.decisao == "anulada":
            anuladas += 1
        if linha.duplicada_na_folha:
            duplicadas += 1

        if ultima is not None:
            # A revisão humana MANDA sobre o gate: é para isso que ela existe.
            if ultima.matricula_escolhida:
                identificados += 1
            else:
                nao_ident += 1
        elif linha.decisao == "aceito":
            # Aceite não auditado conta como identificado pelo sistema — a
            # auditoria é por amostragem por decisão de método (A21), não por
            # esquecimento.
            identificados += 1
        elif linha.decisao == "quarentena":
            pendentes += 1

    return ResumoRevisao(linhas=len(linhas), identificados=identificados,
                         nao_identificados=nao_ident, anuladas=anuladas,
                         pendentes=pendentes, duplicadas=duplicadas)


@rotas.get("/{folha_id}/resumo", response_model=ResumoRevisao)
def resumo(folha_id: int,
           db: Session = Depends(dependencia_sessao)) -> ResumoRevisao:
    if db.get(Folha, folha_id) is None:
        raise HTTPException(404, "folha não encontrada")
    return _resumir(list(db.scalars(select(Linha)
                                    .where(Linha.folha_id == folha_id))))


@rotas.post("/{folha_id}/concluir", response_model=ResumoRevisao)
def concluir(folha_id: int, revisor: str | None = None,
             db: Session = Depends(dependencia_sessao)) -> ResumoRevisao:
    """Fecha a revisão da folha. Ato explícito, e não consequência.

    É este carimbo que separa "ninguém abriu esta folha ainda" de "esta folha
    foi conferida" — as duas coisas têm zero pendências e significam o oposto.
    A partir dele a frequência da folha vale.

    **Quarentena sem decisão impede concluir.** Aceite sem auditar não impede:
    a auditoria é por amostragem por decisão de método, mas a quarentena existe
    justamente porque o sistema não soube — deixá-la em aberto e carimbar a
    folha como revisada seria perder a linha em silêncio.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.estado != "processada":
        raise HTTPException(409, f"a folha está '{folha.estado}', não processada")

    linhas = list(db.scalars(select(Linha).where(Linha.folha_id == folha_id)))
    resultado = _resumir(linhas)
    if resultado.pendentes:
        raise HTTPException(
            409, f"{resultado.pendentes} linha(s) em quarentena sem decisão")

    folha.revisada_em = datetime.now(timezone.utc)
    folha.revisada_por = revisor
    db.commit()
    return resultado


@rotas.post("/{folha_id}/reabrir", response_model=FolhaSaida)
def reabrir(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    """Tira o carimbo de concluída para a folha voltar a ser editável.

    **Nada é apagado.** A tabela de revisão é append-only, então as decisões
    anteriores continuam lá e a nova decisão empilha por cima. É isso que
    permite responder depois "quem mudou esta presença, e quando" — a pergunta
    do primeiro contencioso, e a razão de a rubrica existir na folha.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    folha.revisada_em = None
    folha.revisada_por = None
    db.commit()
    return montar_folha(folha, *_contadores(db, [folha_id]).get(folha_id, (0, 0)))


@rotas.post("/{folha_id}/reprocessar", response_model=FolhaSaida)
def reprocessar(folha_id: int, tarefas: BackgroundTasks,
                modelo_folha: str | None = None, forcar: bool = False,
                db: Session = Depends(dependencia_sessao)) -> dict:
    """Lê a folha de novo, opcionalmente com outra geometria.

    Existe por causa de um modo de falha real: recortar com o modelo errado não
    dá erro, dá lixo com cara de leitura (ver o portão em `_ler_e_gravar`).
    Sem reprocessar, a única saída seria apagar e reenviar — perdendo o que
    já foi revisado nas folhas que estavam certas.

    **Descarta as linhas, e com elas as revisões.** Por isso `forcar` é exigido
    quando já existe decisão humana: reler é jogar fora trabalho de gente, e
    isso não pode acontecer por engano. A folha em si e a imagem ficam.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.estado == "processando":
        raise HTTPException(409, "esta folha está sendo lida agora")

    revisadas = db.scalar(
        select(func.count()).select_from(Revisao)
        .join(Linha, Revisao.linha_id == Linha.id)
        .where(Linha.folha_id == folha_id)) or 0
    if revisadas and not forcar:
        raise HTTPException(
            409, f"{revisadas} decisão(ões) humana(s) seriam descartadas; "
                 "repita com forcar=true se for mesmo para reler")

    if modelo_folha:
        folha.modelo_folha = modelo_folha
    for linha in list(folha.linhas):
        db.delete(linha)                      # revisões saem por cascade
    folha.estado = "recebida"
    folha.erro = None
    folha.linhas_preenchidas = folha.linhas_anuladas = 0
    folha.processada_em = folha.revisada_em = None
    folha.revisada_por = None
    db.commit()

    tarefas.add_task(_ler_e_gravar, folha.id)
    return montar_folha(folha)


@rotas.get("/{folha_id}/json")
def baixar_json(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """O registro completo da folha, para baixar.

    É o entregável do sistema (objetivo específico i): a frequência apurada com
    **como ela foi apurada junto** — ponto de operação, modelos que leram cada
    campo, leitura crua, candidatos ranqueados e a decisão humana de cada linha.

    Sem a procedência, o arquivo seria uma lista de presença sem como conferir;
    com ela, qualquer número dele é reproduzível.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")

    linhas = list(db.scalars(select(Linha).where(Linha.folha_id == folha_id)
                             .order_by(Linha.numero)))
    dados = {
        "folha": {
            "id": folha.id, "arquivo": folha.nome_original,
            "pagina": folha.pagina, "modelo_folha": folha.modelo_folha,
            "estado": folha.estado,
            "processada_em": folha.processada_em.isoformat()
            if folha.processada_em else None,
            "revisada_em": folha.revisada_em.isoformat()
            if folha.revisada_em else None,
            "revisada_por": folha.revisada_por,
        },
        "ponto_de_operacao": {
            "alpha": folha.alpha, "theta": folha.theta, "delta": folha.delta,
            "metrica_nome": folha.metrica_nome,
            "modelo_matricula": folha.modelo_matricula,
            "modelo_letras": folha.modelo_letras,
        },
        "linhas": [],
    }
    for linha in linhas:
        ultima = max(linha.revisoes, key=lambda r: r.criado_em, default=None)
        dados["linhas"].append({
            "numero": linha.numero,
            "leitura_matricula": linha.leitura_matricula,
            "leitura_nome": linha.leitura_nome,
            "score": linha.score, "margem": linha.margem,
            "decisao": linha.decisao,
            "matricula_sugerida": linha.matricula_sugerida,
            "candidatos": linha.candidatos,
            "duplicada_na_folha": linha.duplicada_na_folha,
            "revisao": None if ultima is None else {
                "matricula_escolhida": ultima.matricula_escolhida,
                "revisor": ultima.revisor, "ms": ultima.ms,
                "em": ultima.criado_em.isoformat(),
            },
        })

    nome = f"folha_{folha.id}_{Path(folha.nome_original).stem}.json"
    return JSONResponse(
        dados,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'})


def _variacoes(matricula: str | None) -> set[str]:
    """Toda matrícula a UMA substituição de dígito da dada."""
    if not matricula:
        return set()
    return {matricula[:i] + d + matricula[i + 1:]
            for i in range(len(matricula)) for d in "0123456789"
            if d != matricula[i]}
