"""O que os testes dividem: caminhos, raster e alinhamento.

Importar este módulo isola a bateria do que é real: o cabeçalho salvo vai para uma
pasta temporária e o banco aponta para uma porta fechada. Nenhum teste toca em
`cabecalho.json`, no Postgres ou em `dados/`.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PIPELINE = RAIZ / "experimento-imagem"
ESTADO = Path(tempfile.mkdtemp(prefix="olhodigital_testes_"))

os.environ["OLHODIGITAL_CABECALHO"] = str(ESTADO / "cabecalho.json")
os.environ["OLHODIGITAL_DATABASE_URL"] = "postgresql+psycopg://x@127.0.0.1:1/x"
os.environ["OLHODIGITAL_ARQUIVO_USUARIOS"] = str(ESTADO / "usuarios.json")
os.environ.setdefault("KERAS_BACKEND", "torch")
for caminho in (RAIZ, PIPELINE):
    sys.path.insert(0, str(caminho))
os.chdir(PIPELINE)    # o pipeline resolve `modelo/` relativo a este diretório

import cv2                                                     # noqa: E402
import numpy as np                                             # noqa: E402

import gerar_folha as gf                                       # noqa: E402
import lote_folhas as lf                                       # noqa: E402
from align import Align                                        # noqa: E402
from sheet_cutter import MODELOS, SheetCutter                  # noqa: E402

EU = gf.Cabecalho("ENCONTROS UNIVERSITÁRIOS 2026", "FOLHA DE PRESENÇA")
V4 = MODELOS["gerada_v2"]
LENTOS = bool(os.environ.get("OLHODIGITAL_TESTES_LENTOS"))


def px(mm: float, dpi: int) -> int:
    return round(mm * dpi / 25.4)


def alinhar(cinza: np.ndarray) -> np.ndarray:
    return Align(*V4.warp).aruco(cinza)


def cortador() -> SheetCutter:
    return SheetCutter(str(ESTADO / "celulas"))


def texto_pdf(caminho: str, pagina: int | None = None) -> str:
    args = ["pdftotext", "-layout"]
    if pagina:
        args += ["-f", str(pagina), "-l", str(pagina)]
    return subprocess.run([*args, caminho, "-"], capture_output=True, text=True,
                          check=True).stdout


def info_pdf(caminho: str) -> dict:
    saida = subprocess.run(["pdfinfo", "-l", "-1", caminho], capture_output=True,
                           text=True, check=True).stdout
    tamanhos = [l.split(":", 1)[1].split("(")[0].split() for l in saida.splitlines()
                if l.startswith("Page") and "size" in l]
    paginas = int(next(l for l in saida.splitlines()
                       if l.startswith("Pages:")).split()[1])
    return {"paginas": paginas,
            "tamanhos": {(float(t[0]), float(t[2])) for t in tamanhos}}


def degradar(cinza: np.ndarray, angulo: float = 0, escala: float = 1.0,
             ruido: float = 0, borrar: int = 0, jpeg: int = 0,
             semente: int = 0) -> np.ndarray:
    """Imita impressão e escâner: giro, redução, ruído, borrão e compressão."""
    altura, largura = cinza.shape
    matriz = cv2.getRotationMatrix2D((largura / 2, altura / 2), angulo, escala)
    saida = cv2.warpAffine(cinza, matriz, (largura, altura), borderValue=255,
                           flags=cv2.INTER_CUBIC)
    if borrar:
        saida = cv2.GaussianBlur(saida, (borrar, borrar), 0)
    if ruido:
        rng = np.random.default_rng(semente)
        saida = np.clip(saida + rng.normal(0, ruido, saida.shape), 0, 255
                        ).astype(np.uint8)
    if jpeg:
        ok, bytes_ = cv2.imencode(".jpg", saida, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
        saida = cv2.imdecode(bytes_, cv2.IMREAD_GRAYSCALE)
    return saida
