"""Revisão humana das linhas.

Isto substitui o `localStorage` da página estática, e a diferença não é de
conforto: hoje a decisão do revisor mora no navegador dele, some se limpar os
dados do site, e não dá para duas pessoas revisarem o mesmo evento. Em 30/08
foram exportados dois JSON à mão por causa disso.

A tabela é **append-only**: revisar de novo não sobrescreve, cria registro novo.
O histórico é o que permite responder "quem mudou esta presença, e quando" —
pergunta que vai aparecer no primeiro contencioso e que a rubrica da folha (item
1b do Backlog) existe para apoiar.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import dependencia_sessao
from ..esquemas import RevisaoEntrada, RevisaoSaida
from ..pipeline import pasta_recortes
from ..tabelas import Linha, Participante, Revisao

rotas = APIRouter(prefix="/linhas", tags=["revisão"])


@rotas.get("/{linha_id}/recorte")
def recorte(linha_id: int, db: Session = Depends(dependencia_sessao)):
    """A faixa da folha correspondente a esta linha.

    É o que o revisor olha para decidir — a leitura do OCR é evidência de
    segunda mão. Gravado no processamento; aqui é só ler o arquivo.
    """
    linha = db.get(Linha, linha_id)
    if linha is None:
        raise HTTPException(404, "linha não encontrada")
    caminho = pasta_recortes(Path(linha.folha.arquivo)) / f"{linha.numero:02d}.jpg"
    if not caminho.exists():
        raise HTTPException(404, "recorte não gerado para esta linha")
    # A tira não muda depois de gravada: a imagem é imutável e o nome carrega
    # a folha, então o navegador pode guardar à vontade.
    return FileResponse(caminho, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


@rotas.post("/{linha_id}/revisao", response_model=RevisaoSaida, status_code=201)
def revisar(linha_id: int, entrada: RevisaoEntrada,
            db: Session = Depends(dependencia_sessao)) -> Revisao:
    linha = db.get(Linha, linha_id)
    if linha is None:
        raise HTTPException(404, "linha não encontrada")

    # Folha concluída não aceita decisão nova. O carimbo de revisada é o que
    # torna a frequência daquela folha final; deixá-la mudar depois faria o
    # carimbo não significar nada. Reabrir é ato explícito e deixa rastro.
    if linha.folha.revisada_em is not None:
        raise HTTPException(
            409, "esta folha já foi concluída; reabra a revisão para alterar")

    # Matrícula que não está no contradomínio não é decisão válida: o sistema
    # estaria afirmando uma identidade que a base não conhece. Participante
    # externo é caso real (a margem colapsa e δ o barra) e tem saída própria —
    # "não identificado", que é `matricula_escolhida = null`.
    if entrada.matricula_escolhida is not None:
        existe = db.get(Participante, entrada.matricula_escolhida)
        if existe is None:
            raise HTTPException(
                422, f"matrícula {entrada.matricula_escolhida} não está na base "
                     "de participantes; use 'não está na base' ou 'não identificado'")

    dados = entrada.model_dump()
    # D32: "não está na base" exige a matrícula. Sem ela, é "não identificado" (decisão do
    # autor em 20/11): presença sem matrícula não identifica ninguém.
    informada = (entrada.matricula_informada or "").strip()
    if entrada.matricula_informada is not None and not informada:
        dados["matricula_informada"] = dados["nome_informado"] = None
    elif informada:
        if entrada.matricula_escolhida is not None:
            raise HTTPException(422, "escolha uma pessoa da base OU diga que não está na base")
        if not informada.isdigit() or not 5 <= len(informada) <= 6:
            raise HTTPException(
                422, f"matrícula {informada!r}: são 5 ou 6 dígitos (uma matrícula da base "
                     "tem 5)")
        na_base = db.get(Participante, informada)
        if na_base is not None:
            raise HTTPException(
                422, f"a matrícula {informada} está na base ({na_base.nome}); escolha a "
                     "pessoa em vez de 'não está na base'")
        dados["matricula_informada"] = informada
        dados["nome_informado"] = " ".join((entrada.nome_informado or "").split()) or None

    revisao = Revisao(linha_id=linha.id, **dados)
    db.add(revisao)
    db.commit()
    return revisao


@rotas.get("/{linha_id}/revisoes", response_model=list[RevisaoSaida])
def historico(linha_id: int,
              db: Session = Depends(dependencia_sessao)) -> list[Revisao]:
    if db.get(Linha, linha_id) is None:
        raise HTTPException(404, "linha não encontrada")
    return list(db.scalars(select(Revisao).where(Revisao.linha_id == linha_id)
                           .order_by(Revisao.criado_em)))
