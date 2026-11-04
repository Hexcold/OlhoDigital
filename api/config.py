"""Configuração da API, toda por variável de ambiente.

Nada de constante de calibração hardcodada aqui: θ, δ, α e a métrica do nome
vivem em `fuzzy` e são importados de lá. Foi hardcodar esses valores em três
módulos que manteve θ=0,85 rodando três sessões depois de o pipeline real medir
0,60 — e é o mesmo defeito que este arquivo existe para não repetir.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings

RAIZ = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    # Postgres por decisão do autor em 30/08. A alternativa discutida era
    # SQLite (um arquivo, sem serviço) — a escolha custa um serviço a mais no
    # caminho de reprodução, e o README diz isso em vez de esconder.
    database_url: str = "postgresql+psycopg://olhodigital@localhost/olhodigital"

    # Onde as imagens enviadas ficam. NÃO vão para o banco: são grandes e são
    # dado pessoal (matrícula e nome manuscritos), e arquivo em disco é mais
    # fácil de apagar e de manter fora do git que linha de tabela.
    pasta_folhas: Path = RAIZ / "experimento-imagem" / "dados" / "enviadas"

    # O pipeline resolve caminhos relativos ao próprio diretório: os modelos
    # estão em `modelo/`, a base em `../experimento-fuzzy/dados/`, e o cache de
    # células em `PASTA_CELULAS`. Enquanto ele não virar pacote — refatoração
    # adiada de propósito, ver o Backlog —, a API roda com o cwd apontado para
    # cá e usa caminho ABSOLUTO em todo o resto.
    pasta_pipeline: Path = RAIZ / "experimento-imagem"

    # Modelo de recorte padrão das folhas novas. A v4 é a folha da coleta (D11),
    # e v2/v3/v4 dividem a mesma geometria, congelada como `gerada_v2`.
    modelo_folha_padrao: str = "gerada_v2"

    # Com quantos dpi um PDF vira imagem. NÃO é a geometria — o warp normaliza
    # isso —, é informação e domínio: abaixo de ~166 dpi o warp da v4 passa a
    # ampliar em vez de reduzir, e todos os números publicados foram medidos a
    # 200. Ver `documentos.py`, que traz a conta inteira.
    dpi_pdf: int = 200

    # D12: o afinamento fica DESLIGADO por padrão. O modelo afinado final viu
    # todas as células de dev, então ligá-lo aqui sem querer tornaria qualquer
    # medição nas folhas de desenvolvimento uma medição no treino, em silêncio.
    # Na coleta ele entra — a folha é inédita por construção e não há o que
    # contaminar. É uma variável de ambiente para que ligar seja um ato
    # explícito e registrado, não um padrão esquecido.
    modelo_letras: str = ""

    # As contas do sistema. Fora do git, com a senha em hash. Ver `api/usuarios.py`.
    arquivo_usuarios: Path = RAIZ / "experimento-imagem" / "dados" / "usuarios.json"

    # Assina o cookie de sessão. Vazio, nasce um ao lado do arquivo de usuários.
    segredo_sessao: str = ""

    class Config:
        env_prefix = "OLHODIGITAL_"


config = Config()
