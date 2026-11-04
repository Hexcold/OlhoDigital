"""Só a impressão, para rodar no computador de quem imprime. É o que vai no `.exe`.

Sem login, porque não há o que proteger nem de onde chegar: o processo escuta só em
127.0.0.1 e aqui não existe banco, lista de alunos nem leitura de folha (D19). A
lista de rotas é a do módulo de impressão e nada mais, o que deixa essa garantia na
estrutura, e não numa regra de acesso que alguém possa esquecer.

A checagem de `Host` fecha o único caminho que sobra: uma página maliciosa aberta no
mesmo navegador apontar um domínio próprio para 127.0.0.1 e falar com este servidor.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import RAIZ
from .esquemas import SessaoSaida
from .estaticos import Aplicacao
from .rotas import impressao

HOSTS_LOCAIS = ("127.0.0.1", "localhost")

app = FastAPI(title="OlhoDigital: impressão", docs_url=None, redoc_url=None,
              openapi_url=None)
app.include_router(impressao.rotas)


@app.get("/eu", response_model=SessaoSaida)
def eu() -> SessaoSaida:
    """A tela usa este papel para mostrar só a impressão, sem abas e sem `sair`."""
    return SessaoSaida(usuario="local", papel="impressao", login=False)


@app.get("/saude")
def saude() -> dict:
    return {"api": "ok"}


def criar(cliente: Path = RAIZ / "web" / "dist") -> TrustedHostMiddleware:
    return TrustedHostMiddleware(Aplicacao(app, cliente),
                                 allowed_hosts=list(HOSTS_LOCAIS))
