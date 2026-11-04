"""Quem pode entrar no sistema, e com qual papel.

Arquivo JSON, e não tabela: o login tem que funcionar com o Postgres desligado,
porque a impressão não depende dele. São poucas pessoas, e a conta nasce no terminal:

    ./.venv/bin/python -m api.usuarios adicionar henrique --papel admin
    ./.venv/bin/python -m api.usuarios adicionar maria --papel impressao
    ./.venv/bin/python -m api.usuarios listar
    ./.venv/bin/python -m api.usuarios remover maria

O arquivo fica fora do git e guarda só o hash da senha.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .config import config

PAPEIS = ("admin", "impressao")
SENHA_MINIMA = 8
_NOME = re.compile(r"^[a-z0-9._-]{1,40}$")
_N, _R, _P = 2 ** 14, 8, 1        # custo do scrypt: cerca de 50 ms por tentativa


def _derivar(senha: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(senha.encode(), salt=sal, n=n, r=r, p=p, dklen=32)


def gerar_hash(senha: str) -> str:
    sal = secrets.token_bytes(16)
    chave = _derivar(senha, sal, _N, _R, _P)
    return (f"scrypt${_N}${_R}${_P}${base64.b64encode(sal).decode()}"
            f"${base64.b64encode(chave).decode()}")


def conferir_hash(senha: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, chave = guardado.split("$")
        calculada = _derivar(senha, base64.b64decode(sal), int(n), int(r), int(p))
        return hmac.compare_digest(calculada, base64.b64decode(chave))
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return False


# Conta inexistente gasta o mesmo tempo que senha errada: o tempo de resposta não
# pode dizer quais nomes existem.
_HASH_VAZIO = gerar_hash(secrets.token_hex(16))


def carregar() -> dict:
    """As contas. Arquivo quebrado não deixa ninguém entrar, em vez de abrir a porta."""
    try:
        dados = json.loads(config.arquivo_usuarios.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _salvar(contas: dict) -> None:
    destino: Path = config.arquivo_usuarios
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destino.parent,
                                     suffix=".tmp", delete=False) as temporario:
        json.dump(contas, temporario, ensure_ascii=False, indent=2)
    os.chmod(temporario.name, 0o600)
    Path(temporario.name).replace(destino)


def adicionar(nome: str, senha: str, papel: str) -> None:
    if not _NOME.match(nome):
        raise ValueError("nome: use só letras minúsculas, números, ponto, hífen ou _")
    if papel not in PAPEIS:
        raise ValueError(f"papel: use um de {', '.join(PAPEIS)}")
    if len(senha) < SENHA_MINIMA:
        raise ValueError(f"senha: use pelo menos {SENHA_MINIMA} caracteres")
    contas = carregar()
    contas[nome] = {"senha": gerar_hash(senha), "papel": papel}
    _salvar(contas)


def remover(nome: str) -> bool:
    contas = carregar()
    if nome not in contas:
        return False
    del contas[nome]
    _salvar(contas)
    return True


def papel_de(nome: str) -> str | None:
    """Lido a cada requisição: remover a conta derruba a sessão na hora."""
    conta = carregar().get(nome)
    papel = conta.get("papel") if isinstance(conta, dict) else None
    return papel if papel in PAPEIS else None


def conferir(nome: str, senha: str) -> str | None:
    """O papel, se a senha estiver certa."""
    conta = carregar().get(nome)
    guardado = conta.get("senha", "") if isinstance(conta, dict) else ""
    certa = conferir_hash(senha, guardado or _HASH_VAZIO)
    return papel_de(nome) if guardado and certa else None


def _principal() -> None:
    linha = argparse.ArgumentParser(description="Contas do sistema")
    ordens = linha.add_subparsers(dest="ordem", required=True)
    novo = ordens.add_parser("adicionar", help="cria a conta, ou troca a senha dela")
    novo.add_argument("nome")
    novo.add_argument("--papel", choices=PAPEIS, default="impressao")
    ordens.add_parser("remover").add_argument("nome")
    ordens.add_parser("listar")
    args = linha.parse_args()

    if args.ordem == "listar":
        for nome, conta in sorted(carregar().items()):
            print(f"{nome}  {conta.get('papel')}")
    elif args.ordem == "remover":
        raise SystemExit(0 if remover(args.nome) else f"{args.nome}: conta não existe")
    else:
        senha = getpass.getpass("Senha: ")
        if senha != getpass.getpass("Repita a senha: "):
            raise SystemExit("as senhas não conferem")
        try:
            adicionar(args.nome, senha, args.papel)
        except ValueError as erro:
            raise SystemExit(str(erro)) from erro
        print(f"{args.nome} pode entrar, com papel {args.papel}")


if __name__ == "__main__":
    _principal()
