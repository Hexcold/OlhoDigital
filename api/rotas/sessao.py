"""Entrar, sair e saber quem está dentro. A regra de acesso mora em `api/sessao.py`."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from .. import usuarios
from ..esquemas import LoginEntrada, SessaoSaida
from ..sessao import COOKIE, DURACAO_S, criar_token, limitador

rotas = APIRouter(tags=["sessão"])


def _https(pedido: Request) -> bool:
    # atrás do túnel a conexão chega em http, e quem diz a verdade é o cabeçalho
    return (pedido.url.scheme == "https"
            or pedido.headers.get("x-forwarded-proto") == "https")


@rotas.post("/login", response_model=SessaoSaida)
def entrar(dados: LoginEntrada, pedido: Request, resposta: Response) -> SessaoSaida:
    nome = dados.usuario.strip().lower()
    if limitador.bloqueado(nome):
        raise HTTPException(429, "muitas tentativas: espere alguns minutos")
    papel = usuarios.conferir(nome, dados.senha)
    if papel is None:
        limitador.falhou(nome)
        raise HTTPException(401, "usuário ou senha incorretos")
    limitador.limpar(nome)
    resposta.set_cookie(COOKIE, criar_token(nome), max_age=DURACAO_S, path="/",
                        httponly=True, samesite="lax", secure=_https(pedido))
    return SessaoSaida(usuario=nome, papel=papel, login=True)


@rotas.post("/sair", status_code=204)
def sair(resposta: Response) -> None:
    resposta.delete_cookie(COOKIE, path="/")


@rotas.get("/eu", response_model=SessaoSaida)
def eu(pedido: Request) -> SessaoSaida:
    """Sem a porta de login na frente, é o desenvolvimento local."""
    nome = getattr(pedido.state, "usuario", None)
    if nome is None:
        return SessaoSaida(usuario="local", papel="admin", login=False)
    return SessaoSaida(usuario=nome, papel=usuarios.papel_de(nome) or "impressao",
                       login=True)
