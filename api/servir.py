"""O sistema inteiro numa porta só, com login: a API em /api e o cliente compilado
no resto.

    cd web && npm ci && npm run build
    ./.venv/bin/uvicorn api.servir:aplicacao --port 8000

É a forma de usar o sistema sem Node em execução, e a única que pode sair desta
máquina. No desenvolvimento quem faz esse papel é o proxy do Vite, com `api.main:app`
sem login, só em 127.0.0.1.
"""
from __future__ import annotations

from .config import RAIZ
from .estaticos import Aplicacao
from .main import app
from .sessao import DOCUMENTACAO, ExigeLogin

aplicacao = ExigeLogin(Aplicacao(app, RAIZ / "web" / "dist", diretas=DOCUMENTACAO))
