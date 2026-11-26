"""Registrar as chaves de um lote como eventos.

Separado de `impressao.py` porque é a única etapa da impressão que toca no banco:
assim a impressão continua funcionando com o Postgres desligado.
"""
from __future__ import annotations

from datetime import datetime, time

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
        # D33: só os eventos ABERTOS seguram uma chave; a de um encerrado está livre
        existentes = set(db.scalars(select(Evento.chave)
                                    .where(Evento.chave.in_(chaves),
                                           Evento.situacao == "em andamento")))
        # D35: todo evento pertence a um grupo
        from .grupos import garantir_grupo
        garantir_grupo(db, pedido.grupo)
        # D24: os eventos criados entram no grupo pedido; os que já existiam não mudam
        novos = [Evento(chave=chave, tipo="evento", nome=i.nome or chave,
                        local=i.local, responsavel=i.responsavel, grupo=pedido.grupo,
                        folhas_previstas=i.folhas,        # D35: o pacote precisa saber
                        # o dia da planilha é o do carimbo (D20); sem ele, o pacote não
                        # teria como imprimir a folha com o dia
                        inicio=datetime.combine(i.dia, time()) if i.dia else None)
                 for chave, i in zip(chaves, pedido.itens)
                 if chave not in existentes]
        db.add_all(novos)
        db.commit()
    except OperationalError as erro:
        raise HTTPException(503, "banco de dados indisponível: inicie o "
                                 "Postgres para cadastrar") from erro
    return CadastroSaida(criados=len(novos), ja_existiam=len(existentes))
