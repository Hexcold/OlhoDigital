"""Cadastro de ocorrências — o contradomínio contra o qual a chave casa (D15).

O evento é cadastrado **antes**, e a chave lida na folha casa contra os
cadastrados; ela não cria grupo sozinha. É o que impede o erro que D14 registra
como o pior desta parte do sistema: um caractere lido errado criando um grupo
novo em silêncio, com as pessoas certas identificadas na pasta do evento errado
— invisível para precisão e para cobertura.

Por isso o cadastro é uma tela, e não um `INSERT` na mão: até 30/08 a tabela
`evento` existia com a chave estrangeira e o filtro de listagem prontos, e
**nada que a populasse**.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import config
from ..db import dependencia_sessao
from ..esquemas import CabecalhoAjuste, EventoEntrada, EventoSaida
from ..tabelas import Evento, Folha

rotas = APIRouter(prefix="/eventos", tags=["eventos"])

SITUACOES = ("em andamento", "encerrado")


def _validar_chave(chave: str) -> str:
    """Aplica a regra de D14, importando-a de onde ela já mora.

    A regra vive em `gerar_folha.validar_chave` porque é ela que decide o que
    vai ao PAPEL. Reescrevê-la aqui criaria duas fontes para a mesma decisão —
    o defeito que manteve θ=0,85 rodando três sessões depois de medido 0,60.

    Import tardio de propósito: `gerar_folha` puxa cv2 e numpy, e import pesado
    no topo de um módulo de rota já derrubou este servidor uma vez.
    """
    if str(config.pasta_pipeline) not in sys.path:
        sys.path.insert(0, str(config.pasta_pipeline))
    from gerar_folha import validar_chave                          # noqa: E402

    try:
        limpa = validar_chave(chave)
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    if not limpa:
        raise HTTPException(422, "o evento precisa de uma chave: é ela que "
                                 "liga a folha ao evento")
    return limpa


def _com_contagem(db: Session, consulta) -> list[EventoSaida]:
    """Anexa a contagem de folhas a cada evento, numa consulta só."""
    linhas = db.execute(
        consulta.add_columns(func.count(Folha.id))
        .outerjoin(Folha, Folha.evento_id == Evento.id)
        .group_by(Evento.id)).all()
    return [EventoSaida.model_validate(ev).model_copy(update={"folhas_enviadas": n})
            for ev, n in linhas]


@rotas.post("", response_model=EventoSaida, status_code=201)
def criar(dados: EventoEntrada,
          db: Session = Depends(dependencia_sessao)) -> EventoSaida:
    from .grupos import garantir_grupo
    evento = Evento(**dados.model_dump())
    evento.chave = _validar_chave(dados.chave)
    evento.grupo = garantir_grupo(db, dados.grupo)                 # D35
    db.add(evento)
    try:
        db.commit()
    except IntegrityError as erro:
        db.rollback()
        # Chave repetida não é conflito de banco a explicar: são duas
        # ocorrências que o sistema não teria como distinguir, porque a folha
        # traz só a chave. Recusar é a única saída correta.
        raise HTTPException(409, f"a chave {evento.chave} já é de outro evento em "
                                 "andamento; encerre-o para liberar a chave") from erro
    db.refresh(evento)
    return EventoSaida.model_validate(evento).model_copy(update={"folhas_enviadas": 0})


@rotas.get("", response_model=list[EventoSaida])
def listar(situacao: str | None = Query(None),
           tipo: str | None = Query(None),
           grupo: str | None = Query(None),
           db: Session = Depends(dependencia_sessao)) -> list[EventoSaida]:
    consulta = select(Evento).order_by(Evento.inicio.desc().nullslast(),
                                       Evento.id.desc())
    if situacao:
        consulta = consulta.where(Evento.situacao == situacao)
    if tipo:
        consulta = consulta.where(Evento.tipo == tipo)
    if grupo:
        consulta = consulta.where(Evento.grupo == grupo)
    return _com_contagem(db, consulta)


@rotas.get("/{evento_id}", response_model=EventoSaida)
def obter(evento_id: int,
          db: Session = Depends(dependencia_sessao)) -> EventoSaida:
    achados = _com_contagem(db, select(Evento).where(Evento.id == evento_id))
    if not achados:
        raise HTTPException(404, "evento não encontrado")
    return achados[0]


@rotas.patch("/{evento_id}", response_model=EventoSaida)
def editar(evento_id: int, dados: EventoEntrada,
           db: Session = Depends(dependencia_sessao)) -> EventoSaida:
    evento = db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(404, "evento não encontrado")

    nova = _validar_chave(dados.chave)
    if nova != evento.chave and evento.folhas:
        # A chave já foi ao papel e as folhas já apontam para ela. Trocá-la
        # agora deixaria o que está impresso divergindo do que o banco espera,
        # e a folha seguinte da mesma pilha não casaria mais.
        raise HTTPException(409, f"o evento já tem {len(evento.folhas)} "
                                 f"folha(s); a chave não pode mudar")
    # D35: o grupo é resolvido ANTES de mexer nos campos. `garantir_grupo` consulta o banco, e
    # o autoflush gravaria antes a chave nova: um conflito de chave estouraria fora do `try`
    # abaixo e viraria 500 em vez de 409.
    grupo = evento.grupo
    # D24: o grupo só muda se vier no pedido — a tela manda o evento inteiro, e uma versão
    # dela que ainda não conheça o campo apagaria o grupo a cada edição
    if "grupo" in dados.model_fields_set:
        from .grupos import garantir_grupo
        grupo = garantir_grupo(db, dados.grupo)                    # D35: não fica sem grupo
    for campo, valor in dados.model_dump(exclude={"grupo"}).items():
        setattr(evento, campo, valor)
    evento.grupo = grupo
    evento.chave = nova
    try:
        db.commit()
    except IntegrityError as erro:
        db.rollback()
        raise HTTPException(409, f"a chave {nova} já é de outro evento em andamento; "
                                 "encerre-o para liberar a chave") from erro
    return obter(evento_id, db)


@rotas.post("/{evento_id}/situacao", response_model=EventoSaida)
def mudar_situacao(evento_id: int, situacao: str = Query(...),
                   db: Session = Depends(dependencia_sessao)) -> EventoSaida:
    """Encerrar tira a chave do contradomínio; reabrir devolve.

    Não apaga nada. Encerrado é o estado normal de um evento que já aconteceu,
    e ele existe para o conjunto de chaves candidatas ficar pequeno — que é o
    que torna o casamento fácil (D07 aplicado a outro conjunto).
    """
    if situacao not in SITUACOES:
        raise HTTPException(422, f"situação tem que ser uma de {SITUACOES}")
    evento = db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(404, "evento não encontrado")
    mudar_situacoes(db, [evento], situacao)
    db.commit()
    return obter(evento_id, db)


def mudar_situacoes(db: Session, eventos: list[Evento], situacao: str) -> int:
    """Encerra ou reabre, todos ou nenhum (D33).

    Encerrar libera a chave para outro evento usar. Reabrir é recusado se outro evento
    aberto já estiver com a chave: os dois abertos seriam indistinguíveis pela folha.
    Devolve quantas folhas da bandeja foram religadas ao reabrir (D34).
    """
    if situacao == "em andamento":
        ids = {e.id for e in eventos}
        chaves = {e.chave for e in eventos if e.situacao != "em andamento"}
        donos = {e.chave: e for e in db.scalars(
            select(Evento).where(Evento.chave.in_(chaves), Evento.situacao == "em andamento",
                                 Evento.id.not_in(ids)))} if chaves else {}
        if donos:
            exemplos = "; ".join(f"{c} (agora de \"{e.nome}\")"
                                 for c, e in sorted(donos.items())[:5])
            raise HTTPException(
                409, f"{len(donos)} chave(s) já estão com outro evento em andamento: "
                     f"{exemplos}. Encerre aquele evento antes de reabrir este.")
    agora = datetime.now(timezone.utc)
    reabertas = set()
    for e in eventos:
        if e.situacao != situacao:
            e.situacao = situacao
            e.encerrado_em = agora if situacao == "encerrado" else None
            if situacao == "em andamento":
                reabertas.add(e.chave)
    if not reabertas:
        return 0
    # D34: reabrir desfaz o encerramento, inclusive para o que chegou nesse meio-tempo
    from .folhas import religar_bandeja
    db.flush()
    return religar_bandeja(db, reabertas)


@rotas.delete("/{evento_id}", status_code=204)
def apagar(evento_id: int, db: Session = Depends(dependencia_sessao)) -> None:
    evento = db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(404, "evento não encontrado")
    if evento.folhas:
        # Apagar deixaria as folhas órfãs, com a chave impressa apontando para
        # um evento que não existe mais. Para tirar de circulação sem perder o
        # vínculo existe `situacao=encerrado`.
        raise HTTPException(409, f"o evento tem {len(evento.folhas)} folha(s); "
                                 f"encerre em vez de apagar")
    db.delete(evento)
    db.commit()



@rotas.put("/{evento_id}/ajuste", response_model=EventoSaida)
def ajustar_cabecalho(evento_id: int, dados: CabecalhoAjuste,
                      db: Session = Depends(dependencia_sessao)) -> EventoSaida:
    """O que este evento troca no modelo da folha do grupo (D35, fase 3). Tudo nulo tira o
    ajuste. Passa pelo portão da folha (D16) já combinado com o modelo do grupo."""
    from .grupos import modelo_do_grupo
    from .impressao import _lote
    from ..tabelas import Grupo
    evento = db.get(Evento, evento_id)
    if evento is None:
        raise HTTPException(404, "evento não encontrado")
    ajuste = dados.model_dump(exclude_none=True)
    if ajuste:
        lf = _lote()
        grupo = db.scalar(select(Grupo).where(Grupo.nome == evento.grupo))
        base = modelo_do_grupo(lf, grupo) if grupo else lf.carregar_cabecalho()
        try:
            lf.conferir_cabecalho(lf.combinar(base, ajuste))
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
    evento.cabecalho_ajuste = ajuste or None
    db.commit()
    return obter(evento_id, db)
