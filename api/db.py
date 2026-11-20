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
                             ("origem_vinculo", "VARCHAR(20)"),
                             ("tau_nome", "DOUBLE PRECISION"),
                             ("recorte", "VARCHAR(10)")):
            conexao.execute(text(f"ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                                 f"{coluna} {tipo}"))
        # D24: o grupo de eventos, com o índice que `index=True` só cria em tabela nova
        conexao.execute(text("ALTER TABLE evento ADD COLUMN IF NOT EXISTS grupo VARCHAR(60)"))
        conexao.execute(text("CREATE INDEX IF NOT EXISTS ix_evento_grupo ON evento (grupo)"))
        # D25: a base de pessoas com cargas versionadas
        conexao.execute(text("ALTER TABLE participante ADD COLUMN IF NOT EXISTS "
                             "ativo BOOLEAN NOT NULL DEFAULT TRUE"))
        conexao.execute(text("ALTER TABLE participante ADD COLUMN IF NOT EXISTS "
                             "saiu_na_carga INTEGER REFERENCES carga_base (id)"))
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "carga_base_id INTEGER REFERENCES carga_base (id)"))
        # A61: a folha reescaneada
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "reescaneada_em TIMESTAMP"))
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "arquivo_reescaneado VARCHAR(300)"))
        # D30: o mesmo papel escaneado de novo
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "copia_de INTEGER REFERENCES folha (id)"))
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "semelhanca_copia DOUBLE PRECISION"))
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "nao_e_copia BOOLEAN NOT NULL DEFAULT FALSE"))
        # D35, fase 5: o grupo de onde a folha foi enviada
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS grupo_envio "
                             "VARCHAR(60) REFERENCES grupo (nome) ON UPDATE CASCADE "
                             "ON DELETE SET NULL"))
        # D35, fase 3: o modelo da folha por grupo, o ajuste por evento e as folhas previstas
        conexao.execute(text("ALTER TABLE grupo ADD COLUMN IF NOT EXISTS cabecalho JSONB"))
        conexao.execute(text("ALTER TABLE evento ADD COLUMN IF NOT EXISTS cabecalho_ajuste JSONB"))
        conexao.execute(text("ALTER TABLE evento ADD COLUMN IF NOT EXISTS folhas_previstas "
                             "INTEGER"))
        # D35: o grupo vira entidade. Cria os grupos a partir dos nomes em uso, põe os eventos
        # sem grupo num grupo próprio e liga o evento ao grupo pelo nome, com cascata.
        if conexao.dialect.name == "postgresql":
            conexao.execute(text("INSERT INTO grupo (nome) SELECT DISTINCT grupo FROM evento "
                                 "WHERE grupo IS NOT NULL ON CONFLICT (nome) DO NOTHING"))
            if conexao.execute(text("SELECT count(*) FROM evento WHERE grupo IS NULL")).scalar():
                conexao.execute(text("INSERT INTO grupo (nome, descricao) VALUES "
                                     "('Eventos avulsos', 'eventos que não tinham grupo quando "
                                     "o grupo passou a ser obrigatório (D35)') "
                                     "ON CONFLICT (nome) DO NOTHING"))
                conexao.execute(text("UPDATE evento SET grupo = 'Eventos avulsos' "
                                     "WHERE grupo IS NULL"))
            if not conexao.execute(text("SELECT 1 FROM pg_constraint "
                                        "WHERE conname = 'fk_evento_grupo'")).scalar():
                conexao.execute(text("ALTER TABLE evento ADD CONSTRAINT fk_evento_grupo "
                                     "FOREIGN KEY (grupo) REFERENCES grupo (nome) "
                                     "ON UPDATE CASCADE"))
        # D33: a chave só é única entre os eventos abertos. O índice antigo era único para
        # todos; vira um índice comum, e a unicidade passa para um índice parcial.
        conexao.execute(text("ALTER TABLE evento ADD COLUMN IF NOT EXISTS "
                             "encerrado_em TIMESTAMP"))
        if conexao.dialect.name == "postgresql":
            unico = conexao.execute(text(
                "SELECT indexdef LIKE 'CREATE UNIQUE%' FROM pg_indexes "
                "WHERE tablename = 'evento' AND indexname = 'ix_evento_chave'")).scalar()
            if unico:
                conexao.execute(text("DROP INDEX ix_evento_chave"))
                conexao.execute(text("CREATE INDEX ix_evento_chave ON evento (chave)"))
            conexao.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_evento_chave_aberta ON evento (chave) "
                "WHERE situacao = 'em andamento'"))
        # D32: a presença de quem não está na base
        conexao.execute(text("ALTER TABLE revisao ADD COLUMN IF NOT EXISTS "
                             "matricula_informada VARCHAR(20)"))
        conexao.execute(text("ALTER TABLE revisao ADD COLUMN IF NOT EXISTS "
                             "nome_informado VARCHAR(200)"))
        # o começo do lote no progresso da fila
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "na_fila_desde TIMESTAMP"))
        # D29: o canto estimado na folha lida com 3 marcadores
        conexao.execute(text("ALTER TABLE folha ADD COLUMN IF NOT EXISTS "
                             "marcador_estimado INTEGER"))
