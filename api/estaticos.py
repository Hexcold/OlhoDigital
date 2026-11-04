"""Servir o cliente compilado junto da API, numa porta só.

`/eventos` existe nos dois lados: é tela no cliente e rota na API. Por isso a
separação é pelo prefixo, e ele sai antes de a requisição chegar à API, que é o
mesmo que o proxy do Vite faz no desenvolvimento.
"""
from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class _Cliente(StaticFiles):
    """Rota do cliente não é arquivo: `/folhas/3` devolve o index.html e quem
    resolve é o React. Sem isso, recarregar qualquer tela daria 404."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as erro:
            if erro.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


class Aplicacao:
    def __init__(self, api, pasta: Path, diretas: tuple[str, ...] = ()) -> None:
        if not (pasta / "index.html").exists():
            raise RuntimeError(f"{pasta} não tem o cliente compilado: rode o build "
                               f"em web/")
        self.api = api
        self.cliente = _Cliente(directory=pasta, html=True)
        self.diretas = diretas        # caminhos que vão à API sem o prefixo

    async def __call__(self, scope, receive, send) -> None:
        caminho = scope.get("path", "")
        if scope["type"] == "lifespan" or caminho in self.diretas:
            await self.api(scope, receive, send)
        elif caminho.startswith("/api/"):
            await self.api(dict(scope, path=caminho[4:], raw_path=caminho[4:].encode()),
                           receive, send)
        else:
            await self.cliente(scope, receive, send)
