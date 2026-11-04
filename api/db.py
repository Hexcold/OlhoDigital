"""Conexão com o banco, criada sob demanda.

O engine é preguiçoso de propósito: a API tem que **subir sem banco**. Sem isso,
`uvicorn` morre no import quando o Postgres não está no ar, e a mensagem que
aparece é um traceback de conexão em vez de "o serviço não está rodando" — que é
o que a pessoa precisa ler. Também é o que permite conferir o esquema e as rotas
antes de existir servidor, que é a situação em que este arquivo foi escrito.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import config


@lru_cache(maxsize=1)
def _fabrica() -> sessionmaker[Session]:
    engine = create_engine(config.database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


def sessao() -> Session:
    """Uma sessão nova. Quem abre, fecha."""
    return _fabrica()()


def dependencia_sessao():
    """Injeção do FastAPI: `db: Session = Depends(dependencia_sessao)`."""
    s = sessao()
    try:
        yield s
    finally:
        s.close()


def criar_tabelas() -> None:
    """`create_all` basta enquanto o esquema estiver mudando toda sessão.

    Alembic entra quando ele estabilizar — migração de esquema que ninguém vai
    aplicar ainda é cerimônia, e cerimônia cedo demais vira migração falsa que
    ninguém confere.
    """
    from .tabelas import Base
    engine = _fabrica().kw["bind"]
    Base.metadata.create_all(engine)
    # `create_all` não altera tabela que já existe, e um banco com folhas lidas
    # não pode ser recriado para ganhar uma coluna.
    with engine.begin() as conexao:
        for coluna, tipo in (("chave_lida", "VARCHAR(16)"),
                             ("origem_vinculo", "VARCHAR(20)")):
            conexao.execute(text(f"ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                                 f"{coluna} {tipo}"))
