"""A API do OlhoDigital.

    ./.venv/bin/uvicorn api.main:app --reload

Documentação interativa em /docs — ela é o objetivo específico (i) saindo de
graça, em vez de um exemplo de JSON colado no texto.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import pipeline
from .db import criar_tabelas
from .rotas import (cadastro_chaves, eventos, folhas, impressao, linhas, participantes,
                    sessao)

log = logging.getLogger("olhodigital")


@asynccontextmanager
async def ciclo(app: FastAPI):
    # O pipeline resolve caminho relativo ao próprio diretório, então o cwd do
    # processo tem que apontar para lá. Feito UMA vez, aqui, e não dentro do
    # processamento: `os.chdir` é global ao processo, e chamá-lo com
    # requisições em voo mudaria o caminho debaixo delas.
    pipeline.preparar()

    # Nenhuma das duas falhas abaixo derruba o serviço, e a razão é a mesma nas
    # duas: um erro claro no /docs e no log ajuda mais do que um traceback no
    # boot. Quem sobe isto sem Postgres no ar ou sem os modelos treinados
    # precisa LER o que falta, não decifrar um stack trace de conexão.
    try:
        criar_tabelas()
    except Exception as e:                                  # noqa: BLE001
        log.warning("banco indisponível (%s: %s) — a API sobe, mas toda rota "
                    "que toca o banco vai falhar. Ver api/README.md",
                    type(e).__name__, e)
    try:
        pipeline.carregar_recursos()
        log.info("base e classificadores carregados")
    except Exception as e:                                  # noqa: BLE001
        log.warning("pipeline indisponível (%s: %s) — upload aceita, "
                    "processamento falha", type(e).__name__, e)
    yield


app = FastAPI(
    title="OlhoDigital",
    summary="Extração de frequência a partir de listas de presença manuscritas",
    version="0.1.0",
    lifespan=ciclo,
)

# O cliente roda em outra porta no desenvolvimento (Vite em 5173). Em produção
# self-hosted os dois ficam na mesma origem e isto não faz nada.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(eventos.rotas)
app.include_router(folhas.rotas)
app.include_router(impressao.rotas)
app.include_router(cadastro_chaves.rotas)
app.include_router(sessao.rotas)
app.include_router(linhas.rotas)
app.include_router(participantes.rotas)


@app.get("/modelos-folha", tags=["serviço"])
def modelos_folha() -> list[str]:
    """As geometrias de recorte que o sistema conhece.

    A tela precisa delas para oferecer a releitura com outro modelo: uma folha
    lida com a geometria errada produz lixo sem dar erro, e escolher a certa é
    o conserto.
    """
    pipeline.preparar()
    from sheet_cutter import MODELOS                              # noqa: E402
    return sorted(MODELOS)


@app.get("/saude", tags=["serviço"])
def saude() -> dict:
    """Diz o que está de pé e o que não está.

    Existe porque as duas dependências pesadas — Postgres e os modelos — falham
    de formas diferentes e em momentos diferentes, e "a API respondeu" não quer
    dizer que uma folha vá ser lida.
    """
    from .db import sessao
    estado = {"api": "ok"}
    try:
        with sessao() as s:
            s.execute(__import__("sqlalchemy").text("select 1"))
        estado["banco"] = "ok"
    except Exception as e:                                  # noqa: BLE001
        estado["banco"] = f"indisponível: {type(e).__name__}"
    estado["pipeline"] = "ok" if pipeline._RECURSOS else "não carregado"
    return estado
