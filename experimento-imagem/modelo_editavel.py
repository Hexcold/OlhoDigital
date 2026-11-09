"""Modelo da folha para o Illustrator, e o portão do PDF que volta dele.

D10 tirou a folha do editor porque lá a geometria se perdia em silêncio, e isso
continua verdade. O que este módulo faz é separar as duas metades da folha pela
regra de D16: o que o sistema lê vira um fundo que o editor não tem por que tocar,
e só o que está em `gerar_folha.areas_editaveis` fica editável.

A separação não confia em quem edita. Qualquer PDF que volte do editor passa por
`conferir_pdf` antes da impressora, porque foi assim que a folha de 24/08 passou:
parecia certa e detectava em 1 de 4 resoluções.

    modelo_editavel.py                 gera os modelos e confere o gabarito
    modelo_editavel.py conferir X.pdf  confere um PDF que voltou do editor

A chave não entra no modelo, de propósito: A49 mediu a leitura com a letra impressa
pelo gerador, naquela fonte e naquele corpo. Quem usa o modelo escreve a chave à mão.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

import gerar_folha as gf

SVG_NS = "{http://www.w3.org/2000/svg}"
MM_PT = 72 / 25.4
LARGURA_PT = gf.A4_LARGURA_MM * MM_PT
ALTURA_PT = gf.A4_ALTURA_MM * MM_PT

SAIDA = Path(__file__).resolve().parent.parent / "modelo-folha"

# Os nomes viram o que a pessoa lê no painel de camadas. Em ASCII e sem espaço porque
# `id` de SVG é só isso, e o Illustrator reescreve o que não for.
CAMADA_TRAVADA = "TRAVADO_nao_mexer"
CAMADA_EDITAVEL = "EDITAVEL_textos"

# Arial está em todo Windows e Mac, e estes são os nomes que o próprio Illustrator
# escreve ao exportar. A fonte da folha é outra, mas nada lê estes textos.
FONTE_NORMAL = "ArialMT, Arial, Helvetica, sans-serif"
FONTE_NEGRITO = "Arial-BoldMT, Arial, Helvetica, sans-serif"

# Abaixo de `cut_borders(2)`: 0,25 mm dá 1,6 px no warp de 1273 px, e A33 mediu que
# cortar de 0 a 2 px não muda a leitura. Até aqui o desvio cai no que já é jogado fora.
TOLERANCIA_MM = 0.25
DPI_CONFERENCIA = 300
LIMIAR_TINTA = 200       # claro o bastante para a grade cinza #8c8c8c contar como tinta
FOLGA_PAGINA_MM = 0.5


def _pt(mm: float) -> str:
    # 4 casas, e não 3: o erro de arredondamento do tracejado soma a cada período, e
    # com 3 a rubrica saía com a fase desviada de um lado a outro da caixa
    return f"{mm * MM_PT:.4f}"


def _elementos(svg: str) -> list[ET.Element]:
    return list(ET.fromstring(svg))


def _dentro(x: float, y: float) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1
               for x0, y0, x1, y1 in gf.areas_editaveis())


def separar(cabecalho: gf.Cabecalho) -> tuple[list[ET.Element], list[ET.Element]]:
    """(travados, editáveis): o texto na área de D16 é editável, o resto não.

    O critério é a posição, e não uma lista de textos: `areas_editaveis` é quem manda
    em D16, e uma segunda definição aqui poderia discordar dela.
    """
    travados, editaveis = [], []
    # o primeiro <rect> é o papel, e papel não é tinta: no editor ele só atrapalha
    # a seleção, e no Docs cobriria o que vem por baixo
    for elemento in _elementos(gf.desenhar_svg(cabecalho=cabecalho))[1:]:
        texto = elemento.tag == f"{SVG_NS}text"
        if texto and _dentro(float(elemento.get("x")), float(elemento.get("y"))):
            editaveis.append(elemento)
        else:
            travados.append(elemento)
    return travados, editaveis


def _svg_mm(elementos: list[ET.Element]) -> str:
    """Elementos de `desenhar_svg` de volta num SVG em milímetros."""
    corpo = "".join(ET.tostring(e, encoding="unicode") for e in elementos)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{gf.A4_LARGURA_MM}mm" '
            f'height="{gf.A4_ALTURA_MM}mm" viewBox="0 0 {gf.A4_LARGURA_MM} '
            f'{gf.A4_ALTURA_MM}">{corpo}</svg>')


def _rsvg(svg: str, formato: str, *args: str) -> bytes:
    return subprocess.run(["rsvg-convert", f"--format={formato}", *args],
                          input=svg.encode("utf-8"), capture_output=True,
                          check=True).stdout


def _cor_hex(rgb_percentual: str) -> str:
    canais = re.findall(r"[\d.]+", rgb_percentual)
    return "#" + "".join(f"{round(float(c) * 2.55):02x}" for c in canais)


def _contornos(textos: list[ET.Element]) -> list[str]:
    """Os textos travados como curvas, no renderizador da impressão.

    Curva e não fonte para o editor não trocar a letra: quem abre sem Noto Sans veria
    outra largura em rótulo e numeração. Vem do `rsvg-convert` para sair com a mesma
    fonte e o mesmo kerning da folha impressa.

    O SVG do cairo sai em `rgb(35,29%, ...)`, e há relato de o Illustrator não ler cor
    fora do básico. Por isso a saída é refeita em hexadecimal, sem transformação.
    """
    saida = ET.fromstring(_rsvg(_svg_mm(textos), "svg"))
    escala = LARGURA_PT / float(saida.get("viewBox").split()[2])
    caminhos = []
    for caminho in saida.iter(f"{SVG_NS}path"):
        d = caminho.get("d")
        desconhecidos = set(re.findall(r"[A-Za-z]", d)) - set("MLCZ")
        if desconhecidos:
            raise ValueError(f"comando de caminho sem escala absoluta: {desconhecidos}")
        d = re.sub(r"-?\d+(?:\.\d+)?", lambda m: f"{float(m[0]) * escala:.4f}", d)
        caminhos.append(f'<path fill="{_cor_hex(caminho.get("fill"))}" d="{d}"/>')
    return caminhos


def _travado_em_pontos(elementos: list[ET.Element]) -> list[str]:
    """Só `rect`, `line` e `path`, em pontos: é o que qualquer importador entende."""
    saida, textos = [], []
    for e in elementos:
        tipo = e.tag.removeprefix(SVG_NS)
        if tipo == "text":
            textos.append(e)
        elif tipo == "rect":
            traco = ""
            if e.get("stroke"):
                traco = (f' stroke="{e.get("stroke")}" '
                         f'stroke-width="{_pt(float(e.get("stroke-width")))}"')
            saida.append(f'<rect x="{_pt(float(e.get("x")))}" y="{_pt(float(e.get("y")))}" '
                         f'width="{_pt(float(e.get("width")))}" '
                         f'height="{_pt(float(e.get("height")))}" '
                         f'fill="{e.get("fill")}"{traco}/>')
        elif tipo == "line":
            pontos = " ".join(f'{a}="{_pt(float(e.get(a)))}"'
                              for a in ("x1", "y1", "x2", "y2"))
            tracos = " ".join(_pt(float(v)) for v in e.get("stroke-dasharray").split())
            saida.append(f'<line {pontos} stroke="{e.get("stroke")}" '
                         f'stroke-width="{_pt(float(e.get("stroke-width")))}" '
                         f'stroke-dasharray="{tracos}"/>')
        else:
            raise ValueError(f"elemento inesperado na folha: {tipo}")
    return saida + _contornos(textos)


def _texto_em_pontos(e: ET.Element) -> str:
    negrito = e.get("font-weight") == "bold"
    fonte = FONTE_NEGRITO if negrito else FONTE_NORMAL
    peso = ' font-weight="700"' if negrito else ""
    escapado = (e.text or "").replace("&", "&amp;").replace("<", "&lt;")
    return (f'<text x="{_pt(float(e.get("x")))}" y="{_pt(float(e.get("y")))}" '
            f'font-family="{fonte}"{peso} font-size="{_pt(float(e.get("font-size")))}" '
            f'fill="{e.get("fill")}" text-anchor="{e.get("text-anchor")}">{escapado}</text>')


def _svg_pontos(corpo: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{LARGURA_PT:.3f}pt" height="{ALTURA_PT:.3f}pt" '
            f'viewBox="0 0 {LARGURA_PT:.3f} {ALTURA_PT:.3f}">\n{corpo}\n</svg>\n')


def svg_illustrator(cabecalho: gf.Cabecalho) -> str:
    """O modelo em dois grupos de topo, nomeados.

    `width` e `height` em pt, iguais ao viewBox: o Illustrator mede em pontos, e assim
    a página dá A4 lendo qualquer um dos dois. Não há fonte de que esses grupos virem
    camadas ao abrir, então nada aqui depende disso: se vierem como grupos, os nomes
    continuam lá, e quem protege a folha é `conferir_pdf`.
    """
    travados, editaveis = separar(cabecalho)
    camadas = ((CAMADA_TRAVADA, _travado_em_pontos(travados)),
               (CAMADA_EDITAVEL, [_texto_em_pontos(e) for e in editaveis]))
    return _svg_pontos("\n".join(f'<g id="{nome}">\n' + "\n".join(itens) + "\n</g>"
                                 for nome, itens in camadas))


# --- o portão ---------------------------------------------------------------

def _raster_pdf(caminho: Path | str, dpi: int) -> np.ndarray:
    pasta, nome = os.path.split(os.path.abspath(caminho))
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "p")
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-singlefile", nome, base],
                       cwd=pasta, check=True)
        return cv2.imread(base + ".pgm", cv2.IMREAD_GRAYSCALE)


@lru_cache(maxsize=2)
def _referencia(dpi: int) -> np.ndarray:
    """A parte travada, com os textos em curva, como no modelo.

    Com fonte de verdade aqui, o `pdftoppm` desenha o rótulo pequeno de um jeito e a
    curva do modelo de outro: as letras não saem do lugar (0,04 mm a 1200 dpi), mas o
    traço fino muda de lado do limiar, e isso gastava a tolerância do deslocamento.
    """
    travados, _ = separar(gf.Cabecalho(titulo="X"))
    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "fundo.pdf")
        corpo = "\n".join(_travado_em_pontos(travados))
        Path(pdf).write_bytes(_rsvg(_svg_pontos(corpo), "pdf"))
        return _raster_pdf(pdf, dpi)


def _paginas(caminho: Path | str) -> tuple[int, float, float]:
    """(páginas, largura mm, altura mm) da primeira página."""
    saida = subprocess.run(["pdfinfo", str(caminho)], capture_output=True, text=True,
                           check=True).stdout
    paginas = int(re.search(r"Pages:\s+(\d+)", saida)[1])
    largura, altura = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", saida).groups()
    return paginas, float(largura) / MM_PT, float(altura) / MM_PT


def _cantos_nominais() -> dict[int, tuple[float, float]]:
    L = gf.LAYOUT
    x1, y1 = L.ref_x0 + L.ref_larg, L.ref_y0 + L.ref_alt
    return {0: (L.ref_x0, L.ref_y0), 1: (x1, L.ref_y0), 2: (L.ref_x0, y1), 3: (x1, y1)}


def _desvio_marcadores(cinza: np.ndarray, dpi: int) -> dict[int, float]:
    """Quanto o canto externo de cada marcador saiu do lugar, em mm.

    Os mesmos cantos que `Align.aruco` usa: é por eles que o warp decide tudo.
    """
    dicionario = cv2.aruco.getPredefinedDictionary(gf.ARUCO_DICIONARIO)
    detector = cv2.aruco.ArucoDetector(dicionario, cv2.aruco.DetectorParameters())
    cantos, ids, _ = detector.detectMarkers(cinza)
    achados = {} if ids is None else dict(zip(ids.flatten().tolist(), cantos))
    externo = {0: 0, 1: 1, 2: 3, 3: 2}     # índice do canto externo, como em `align.py`
    desvios = {}
    for id_, (x, y) in _cantos_nominais().items():
        if id_ in achados:
            canto = achados[id_][0][externo[id_]] * 25.4 / dpi
            desvios[id_] = float(np.hypot(canto[0] - x, canto[1] - y))
    return desvios


def _caixa_mm(mascara: np.ndarray, dpi: int) -> str:
    ys, xs = np.nonzero(mascara)
    mm = 25.4 / dpi
    return (f"x {xs.min() * mm:.1f}–{xs.max() * mm:.1f} mm, "
            f"y {ys.min() * mm:.1f}–{ys.max() * mm:.1f} mm")


def conferir_pdf(caminho: Path | str, dpi: int = DPI_CONFERENCIA) -> dict:
    """Decide se um PDF que voltou do editor pode ir para a impressora.

    O pixel exato de `lote_folhas._conferir_svg` não serve aqui: outro renderizador
    arredonda diferente, e reprovaria toda folha. O teste é de tinta, com folga de
    TOLERANCIA_MM. Fora das áreas de D16 não pode surgir tinta longe da referência,
    nem sumir tinta que a referência tem.
    """
    problemas: list[str] = []
    paginas, largura, altura = _paginas(caminho)
    if paginas != 1:
        problemas.append(f"{paginas} páginas, o modelo tem 1")
    if (abs(largura - gf.A4_LARGURA_MM) > FOLGA_PAGINA_MM
            or abs(altura - gf.A4_ALTURA_MM) > FOLGA_PAGINA_MM):
        problemas.append(f"página de {largura:.1f} x {altura:.1f} mm, tem que ser A4")
        return {"ok": False, "problemas": problemas, "paginas": paginas}

    deteccao = gf.conferir_deteccao(str(caminho))
    for dpi_teste, ids in deteccao.items():
        faltam = {0, 1, 2, 3} - set(ids)
        if faltam:
            problemas.append(f"a {dpi_teste} dpi não detecta os marcadores {sorted(faltam)}")

    cinza = _raster_pdf(caminho, dpi)
    desvios = _desvio_marcadores(cinza, dpi)
    for id_, desvio in desvios.items():
        if desvio > TOLERANCIA_MM:
            problemas.append(f"marcador {id_} a {desvio:.2f} mm do lugar "
                             f"(tolerância {TOLERANCIA_MM} mm)")

    referencia = _referencia(dpi)
    if cinza.shape != referencia.shape:
        cinza = cv2.resize(cinza, referencia.shape[::-1], interpolation=cv2.INTER_AREA)
    tinta = (cinza < LIMIAR_TINTA).astype(np.uint8)
    esperada = (referencia < LIMIAR_TINTA).astype(np.uint8)
    raio = math.ceil(TOLERANCIA_MM * dpi / 25.4)
    nucleo = np.ones((2 * raio + 1, 2 * raio + 1), np.uint8)
    conferir = np.ones(tinta.shape, bool)
    for x0, y0, x1, y1 in gf.areas_editaveis():
        px = [round(v * dpi / 25.4) for v in (x0, y0, x1, y1)]
        conferir[px[1]:px[3], px[0]:px[2]] = False
    sobrando = (tinta == 1) & (cv2.dilate(esperada, nucleo) == 0) & conferir
    faltando = (esperada == 1) & (cv2.dilate(tinta, nucleo) == 0) & conferir
    if sobrando.any():
        problemas.append(f"{int(sobrando.sum())} pixels de tinta fora das áreas "
                         f"editáveis ({_caixa_mm(sobrando, dpi)})")
    if faltando.any():
        problemas.append(f"{int(faltando.sum())} pixels de tinta sumiram da parte "
                         f"travada ({_caixa_mm(faltando, dpi)})")
    return {"ok": not problemas, "problemas": problemas, "paginas": paginas,
            "desvio_marcadores_mm": desvios, "pixels_sobrando": int(sobrando.sum()),
            "pixels_faltando": int(faltando.sum())}


# --- arquivos ----------------------------------------------------------------

def gerar_modelos(cabecalho: gf.Cabecalho, pasta: Path = SAIDA) -> dict[str, Path]:
    """O `.svg` é o modelo. O `.pdf` é a mesma arte, para o caso de o SVG não abrir em
    A4: PDF abre no tamanho certo em qualquer versão, mas perde os grupos nomeados."""
    pasta.mkdir(parents=True, exist_ok=True)
    svg = svg_illustrator(cabecalho)
    caminhos = {"svg": pasta / "folha-presenca-illustrator.svg",
                "pdf": pasta / "folha-presenca-illustrator.pdf"}
    caminhos["svg"].write_text(svg, encoding="utf-8")
    caminhos["pdf"].write_bytes(_rsvg(svg, "pdf"))
    return caminhos


def _mostrar(resultado: dict) -> None:
    print("APROVADA" if resultado["ok"] else "REPROVADA: não mande para a impressora")
    for problema in resultado["problemas"]:
        print(f"   - {problema}")
    if "desvio_marcadores_mm" in resultado:
        maior = max(resultado["desvio_marcadores_mm"].values(), default=float("nan"))
        print(f"   maior desvio de marcador: {maior:.3f} mm · pixels sobrando: "
              f"{resultado['pixels_sobrando']} · faltando: {resultado['pixels_faltando']}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "conferir":
        resultado = conferir_pdf(sys.argv[2])
        _mostrar(resultado)
        sys.exit(0 if resultado["ok"] else 1)

    import lote_folhas as lf

    cabecalho = lf.carregar_cabecalho(lf.CABECALHO_PADRAO)
    arquivos = gerar_modelos(cabecalho)
    for arquivo in arquivos.values():
        print(f"gerado: {arquivo} ({arquivo.stat().st_size / 1024:.0f} KB)")
    resultado = conferir_pdf(arquivos["pdf"])
    _mostrar(resultado)
    if not resultado["ok"]:
        sys.exit("ABORTADO: o modelo não passa no próprio portão.")
