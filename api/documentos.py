"""Arquivo enviado -> uma imagem PNG por folha.

O escâner entrega **PDF**, e um PDF traz várias folhas — uma por página. É esse
o formato real das 500 folhas dos Encontros: alguns PDFs grandes, não 500
arquivos soltos.

## Por que 200 dpi, e por que NÃO é pela geometria

A geometria não depende da resolução de entrada: `Align.aruco` faz warp de
perspectiva dos 4 marcadores para um tamanho **fixo** — 1273 × 1705 px na v4 —,
e `sheet_cutter.py` registra que "trocar a resolução do warp não muda fração
nenhuma". Um scan a 150 ou a 400 dpi produz a mesma grade.

O que depende são duas outras coisas:

**Informação.** O warp de 1273 px sobre os 195,30 mm do retângulo dos
marcadores equivale a **166 dpi**. A 200 dpi o scan entrega 1538 px nessa faixa,
então o warp *reduz* em 1,21× — benigno. Abaixo de ~166 dpi ele passa a
*ampliar*: não inventa detalhe, só borra, e o classificador recebe traço pior
sem nada reclamar.

**Domínio, que pesa mais.** [[A28 - A lacuna é de domínio, e ela não é de escritor]]
e [[A35 - Afinar paga no nome e nao paga na matricula]] mostraram que a lacuna do
reconhecedor é de domínio — papel, caneta, binarização —, e nitidez faz parte
disso. Todos os números publicados (0,8963 na matrícula, 0,8285 no nome) foram
medidos a 200 dpi, que é também o mínimo que [[D11 - A folha congela na v4]]
fixou. Ler em outra resolução provavelmente funciona e **ninguém mediu**.

Por isso 200 é padrão configurável, não constante: mudar é decisão de domínio,
e decisão de domínio tem que ser ato explícito de quem sabe o que está fazendo.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import config

_ASSINATURA_PDF = b"%PDF-"


def e_pdf(conteudo: bytes) -> bool:
    """Pelo conteúdo, não pela extensão nem pelo content-type do navegador —
    os dois são palpite de quem enviou."""
    return conteudo[:5] == _ASSINATURA_PDF


def paginas(conteudo: bytes, destino: Path, origem_sha: str) -> list[Path]:
    """Uma imagem por folha. PDF vira N páginas; imagem vira ela mesma.

    Devolve os caminhos em ordem de página. O nome carrega origem e página,
    então dá para chegar do arquivo de volta ao envio sem consultar o banco.
    """
    destino.mkdir(parents=True, exist_ok=True)

    if not e_pdf(conteudo):
        caminho = destino / f"{origem_sha}_p1.png"
        caminho.write_bytes(conteudo)
        return [caminho]

    if shutil.which("pdftoppm") is None:
        raise RuntimeError(
            "pdftoppm não encontrado — instale o poppler para aceitar PDF "
            "(no Arch: pacman -S poppler)")

    with tempfile.TemporaryDirectory() as tmp:
        entrada = Path(tmp) / "entrada.pdf"
        entrada.write_bytes(conteudo)
        # -gray porque o pipeline binariza logo em seguida; cor triplicaria o
        # arquivo sem nunca chegar ao classificador.
        proc = subprocess.run(
            ["pdftoppm", "-png", "-gray", "-r", str(config.dpi_pdf),
             str(entrada), str(Path(tmp) / "pag")],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"pdftoppm falhou: {proc.stderr.strip()[:300]}")

        saidas = []
        for i, gerada in enumerate(sorted(Path(tmp).glob("pag-*.png")), start=1):
            caminho = destino / f"{origem_sha}_p{i}.png"
            caminho.write_bytes(gerada.read_bytes())
            saidas.append(caminho)

    if not saidas:
        raise RuntimeError("o PDF não produziu nenhuma página")
    return saidas


def sha(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()
