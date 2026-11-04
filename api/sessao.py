"""A porta do sistema: sessão por cookie assinado e o que cada papel alcança.

`ExigeLogin` fica na frente de tudo na versão compilada (`api/servir.py`). O sistema
guarda nomes e matrículas, então a regra é negar: conta de impressão só alcança o que
está em `ROTAS_IMPRESSAO`, e uma rota nova nasce bloqueada para ela.

`api.main:app` direto, sem esta porta, é só para desenvolvimento em 127.0.0.1.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import usuarios
from .config import config

COOKIE = "olhodigital_sessao"
DURACAO_S = 12 * 3600

ROTAS_ABERTAS = {("POST", "/api/login"), ("GET", "/api/saude")}
ROTAS_IMPRESSAO = {
    ("GET", "/api/eu"), ("POST", "/api/sair"),
    ("GET", "/api/impressao/cabecalho"), ("PUT", "/api/impressao/cabecalho"),
    ("POST", "/api/impressao/cabecalho/previa"), ("POST", "/api/impressao/folhas"),
    ("GET", "/api/impressao/planilha/modelo"), ("POST", "/api/impressao/planilha"),
    ("POST", "/api/impressao/pdf"),
}
DOCUMENTACAO = ("/docs", "/redoc", "/openapi.json")


def _segredo() -> bytes:
    """Assina o cookie. Sem configuração, nasce uma vez e fica ao lado das contas."""
    if config.segredo_sessao:
        return config.segredo_sessao.encode()
    arquivo = config.arquivo_usuarios.with_suffix(".segredo")
    try:
        return arquivo.read_bytes()
    except OSError:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        valor = secrets.token_hex(32).encode()
        descritor = os.open(arquivo, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descritor, "wb") as saida:
            saida.write(valor)
        return valor


def _assinar(carga: str) -> str:
    return hmac.new(_segredo(), carga.encode(), hashlib.sha256).hexdigest()


def criar_token(nome: str, agora: float | None = None) -> str:
    expira = int((time.time() if agora is None else agora) + DURACAO_S)
    # Hexadecimal, não base64: o `=` do base64 faz o cookie voltar entre aspas.
    carga = f"{nome}|{expira}".encode().hex()
    return f"{carga}.{_assinar(carga)}"


def ler_token(token: str, agora: float | None = None) -> str | None:
    """O nome dentro de um token íntegro e dentro do prazo."""
    carga, _, assinatura = token.partition(".")
    if not hmac.compare_digest(assinatura.encode(), _assinar(carga).encode()):
        return None
    try:
        nome, _, expira = bytes.fromhex(carga).decode().partition("|")
        if int(expira) < (time.time() if agora is None else agora):
            return None
    except ValueError:
        return None
    return nome


class Limitador:
    """Depois de 10 senhas erradas seguidas, a conta espera 5 minutos."""
    MAXIMO, JANELA_S = 10, 300

    def __init__(self) -> None:
        self._falhas: dict[str, tuple[int, float]] = {}
        self._trava = threading.Lock()

    def bloqueado(self, nome: str) -> bool:
        with self._trava:
            falhas, desde = self._falhas.get(nome, (0, 0.0))
            return falhas >= self.MAXIMO and time.time() - desde < self.JANELA_S

    def falhou(self, nome: str) -> None:
        with self._trava:
            falhas, desde = self._falhas.get(nome, (0, 0.0))
            if time.time() - desde >= self.JANELA_S:
                falhas = 0
            if len(self._falhas) > 1000:       # nome inventado não pode crescer sem fim
                self._falhas.clear()
            self._falhas[nome] = (falhas + 1, time.time())

    def limpar(self, nome: str) -> None:
        with self._trava:
            self._falhas.pop(nome, None)


limitador = Limitador()


def _ler_cookie(scope) -> str:
    for chave, valor in scope.get("headers", []):
        if chave == b"cookie":
            for parte in valor.decode("latin-1").split(";"):
                nome, _, conteudo = parte.strip().partition("=")
                if nome == COOKIE:
                    return conteudo
    return ""


async def _recusar(send, codigo: int, mensagem: str) -> None:
    corpo = json.dumps({"detail": mensagem}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": codigo,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": corpo})


class ExigeLogin:
    def __init__(self, aplicacao) -> None:
        self.aplicacao = aplicacao

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self.aplicacao(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        caminho, metodo = scope["path"], scope["method"]

        # O cliente compilado é só tela: sem sessão, a API responde 401 e ele vai
        # para o login. Dado nenhum passa por aqui.
        protegido = (caminho == "/api" or caminho.startswith("/api/")
                     or caminho in DOCUMENTACAO)
        if not protegido or (metodo, caminho) in ROTAS_ABERTAS:
            await self.aplicacao(scope, receive, send)
            return
        if ".." in caminho or "//" in caminho:
            await _recusar(send, 404, "não encontrado")
            return

        nome = ler_token(_ler_cookie(scope))
        papel = usuarios.papel_de(nome) if nome else None
        if papel is None:
            await _recusar(send, 401, "entre com seu usuário e senha")
        elif papel == "admin" or (metodo, caminho) in ROTAS_IMPRESSAO:
            scope.setdefault("state", {})["usuario"] = nome
            await self.aplicacao(scope, receive, send)
        else:
            await _recusar(send, 403, "seu usuário não tem acesso a isto")
