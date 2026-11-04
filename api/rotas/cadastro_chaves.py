"""Registrar as chaves de um lote como eventos.

Separado de `impressao.py` porque é a única etapa da impressão que toca no banco:
assim a impressão continua funcionando com o Postgres desligado, e o `.exe` (D19)
não leva banco nenhum.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..db import dependencia_sessao
from ..esquemas import CadastroSaida, PedidoCadastro
from ..tabelas import Evento
from .impressao import _itens, _lote

rotas = APIRouter(prefix="/impressao", tags=["impressão"])


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
