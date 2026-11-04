"""Preparação da impressão: o cabeçalho da folha e o lote por planilha.

Casca fina sobre `lote_folhas`, onde as regras moram. O PDF sai sempre do
cabeçalho SALVO, nunca do que veio no pedido: assim o que foi conferido na tela
é o que vai para a impressora, e o arquivo guarda qual cabeçalho foi usado.

Só `/cadastrar` toca no banco. Gerar folha funciona com o Postgres desligado,
porque nenhuma etapa da impressão depende dele.
"""
from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import pipeline
from ..db import dependencia_sessao
from ..esquemas import (CabecalhoFolha, CabecalhoSaida, CadastroSaida, ItemLote,
                        LoteSaida, PedidoCadastro, PedidoFolhas, PedidoPdf,
                        PedidoPrevia)
from ..tabelas import Evento

rotas = APIRouter(prefix="/impressao", tags=["impressão"])

LIMITE_PLANILHA_BYTES = 5 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _lote():
    """Import tardio: `lote_folhas` puxa cv2, e import pesado no topo de módulo de
    rota já derrubou este servidor uma vez."""
    pipeline.preparar()
    import lote_folhas                                             # noqa: E402
    return lote_folhas


def _para_saida(lf) -> CabecalhoSaida:
    return CabecalhoSaida(
        cabecalho=CabecalhoFolha(**asdict(lf.carregar_cabecalho())),
        limite_caracteres=lf.gf.LIMITE_CARACTERES_CABECALHO,
        travado=list(lf.PARTES_TRAVADAS))


def _anexo(corpo: bytes, tipo: str, nome: str) -> Response:
    return Response(corpo, media_type=tipo, headers={
        "Content-Disposition": f'attachment; filename="{nome}"'})


def _itens(lf, itens: list[ItemLote]) -> list:
    return [lf.ItemLote(**i.model_dump()) for i in itens]


@rotas.get("/cabecalho", response_model=CabecalhoSaida)
def obter_cabecalho() -> CabecalhoSaida:
    try:
        return _para_saida(_lote())
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro


@rotas.put("/cabecalho", response_model=CabecalhoSaida)
def salvar_cabecalho(dados: CabecalhoFolha) -> CabecalhoSaida:
    lf = _lote()
    try:
        lf.salvar_cabecalho(lf.gf.Cabecalho(**dados.model_dump()))
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    return _para_saida(lf)


@rotas.post("/cabecalho/previa")
def previa_cabecalho(pedido: PedidoPrevia) -> Response:
    """O topo da folha com os textos propostos, sem salvar nada."""
    lf = _lote()
    cabecalho = lf.gf.Cabecalho(**pedido.cabecalho.model_dump())
    try:
        lf.conferir_cabecalho(cabecalho)
        return Response(lf.previa_png(cabecalho, pedido.chave),
                        media_type="image/png")
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro


@rotas.post("/folhas")
def gerar_folhas(pedido: PedidoFolhas) -> Response:
    """Uma ou várias folhas de uma chave, sem planilha."""
    lf = _lote()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "folhas.pdf"
        try:
            resumo = lf.gerar_avulsas(pedido.chave, pedido.folhas,
                                      lf.carregar_cabecalho(), str(caminho))
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        rotulo = pedido.chave.strip().upper() or "sem_chave"
        nome = f"folhas_{rotulo}_{resumo['paginas']}paginas.pdf"
        return _anexo(caminho.read_bytes(), "application/pdf", nome)


@rotas.get("/planilha/modelo")
def planilha_modelo() -> Response:
    return _anexo(_lote().planilha_modelo(), XLSX, "modelo_chaves.xlsx")


@rotas.post("/planilha", response_model=LoteSaida)
def enviar_planilha(arquivo: UploadFile = File(...)) -> LoteSaida:
    lf = _lote()
    conteudo = arquivo.file.read(LIMITE_PLANILHA_BYTES + 1)
    if len(conteudo) > LIMITE_PLANILHA_BYTES:
        raise HTTPException(413, "a planilha passa de 5 MB")
    try:
        lote = lf.ler_planilha(arquivo.filename or "", conteudo)
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    return LoteSaida(itens=[ItemLote(**asdict(i)) for i in lote.itens],
                     erros=lote.erros, alertas=lote.alertas,
                     total_chaves=len(lote.itens),
                     total_folhas=lote.total_folhas)


@rotas.post("/pdf")
def gerar_pdf(pedido: PedidoPdf) -> Response:
    """O PDF do lote. 409 quando há alerta que ninguém confirmou."""
    lf = _lote()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "lote.pdf"
        try:
            resumo = lf.gerar_pdf(_itens(lf, pedido.itens), lf.carregar_cabecalho(),
                                  str(caminho), pedido.aceitar_alertas)
        except lf.AlertasPendentes as pendentes:
            raise HTTPException(409, str(pendentes)) from pendentes
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        nome = f"folhas_{resumo['chaves']}chaves_{resumo['paginas']}paginas.pdf"
        return _anexo(caminho.read_bytes(), "application/pdf", nome)


@rotas.post("/cadastrar", response_model=CadastroSaida)
def cadastrar(pedido: PedidoCadastro,
              db: Session = Depends(dependencia_sessao)) -> CadastroSaida:
    """Registra as chaves do lote como eventos, para a leitura ter contra o que
    casar (D15). Chave que já existe fica como está: repetir o envio é seguro.
    """
    lf = _lote()
    erros, _ = lf.validar_lote(_itens(lf, pedido.itens))
    if erros:
        raise HTTPException(422, "; ".join(erros))
    chaves = [i.chave.strip().upper() for i in pedido.itens]
    try:
        existentes = set(db.scalars(select(Evento.chave)
                                    .where(Evento.chave.in_(chaves))))
        novos = [Evento(chave=chave, tipo="evento", nome=i.nome or chave,
                        local=i.local, responsavel=i.responsavel)
                 for chave, i in zip(chaves, pedido.itens)
                 if chave not in existentes]
        db.add_all(novos)
        db.commit()
    except OperationalError as erro:
        raise HTTPException(503, "banco de dados indisponível: inicie o "
                                 "Postgres para cadastrar") from erro
    return CadastroSaida(criados=len(novos), ja_existiam=len(existentes))
