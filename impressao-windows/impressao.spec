# Receita do PyInstaller. Gere com:  pyinstaller --noconfirm impressao-windows/impressao.spec
#
# Só entra o que está listado em `dados` e o que `abrir.py` importa. `excludes` é a
# segunda trava: se algo da leitura ou do banco voltar a ser importado, o programa
# quebra no ensaio, em vez de crescer em silêncio.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

AQUI = Path(SPECPATH)
RAIZ = AQUI.parent

dados = [
    (str(RAIZ / "web" / "dist"), "web/dist"),
    # `lote_folhas` procura o padrão ao lado dele, e no pacote ele fica na raiz
    (str(RAIZ / "experimento-imagem" / "cabecalho.json"), "."),
    (str(AQUI / "fontes"), "fontes"),
]
# No Linux os programas vêm do sistema, e a pasta não existe.
if (AQUI / "programas").is_dir():
    dados.append((str(AQUI / "programas"), "programas"))

analise = Analysis(
    [str(AQUI / "abrir.py")],
    pathex=[str(RAIZ), str(RAIZ / "experimento-imagem"), str(RAIZ / "experimento-fuzzy")],
    datas=dados,
    # o uvicorn e o anyio escolhem o que usar pelo nome, em tempo de execução
    hiddenimports=collect_submodules("uvicorn") + ["anyio._backends._asyncio"],
    excludes=["torch", "keras", "tensorflow", "sklearn", "scipy", "matplotlib", "pandas",
              "joblib", "h5py", "transformers", "sqlalchemy", "psycopg", "tkinter",
              "api.pipeline", "api.db", "api.tabelas", "api.usuarios",
              "identificar_folha", "ocr", "base_alunos"],
)
pacote = PYZ(analise.pure)
EXE(
    pacote,
    analise.scripts,
    analise.binaries,
    analise.datas,
    name="OlhoDigital-Impressao",
    # A janela de console é o jeito de encerrar, e é onde um erro aparece.
    console=True,
    upx=False,
)
