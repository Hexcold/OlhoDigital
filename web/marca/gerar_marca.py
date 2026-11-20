"""Gera a marca do OlhoDigital (D35): o símbolo, o nome em contornos e os ícones.

    ./.venv/bin/python web/marca/gerar_marca.py      # escreve em web/public/marca/

O símbolo é um olho sólido com a íris quadrada vazada e a pupila quadrada: o "quadrado dentro do
quadrado" é o marcador ArUco da folha e o pixel do "Digital". Escolhido entre quatro candidatos
(incluindo o olho genérico de interface, o ícone de "mostrar senha") por ser o único nítido a
16 px e o mais distinto. É UM caminho com `fill-rule="evenodd"`: olho, íris (furo) e pupila
(cheia de novo). Fica de uma cor só e funciona sobre qualquer fundo.

O nome sai em CONTORNOS, na Noto Sans que a folha usa (`impressao-windows/fontes`, OFL): um SVG
com texto dependeria das fontes de quem abre o arquivo, e a marca sairia diferente em cada lugar.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

RAIZ = Path(__file__).resolve().parents[2]
SAIDA = RAIZ / "web" / "public" / "marca"
FONTES = RAIZ / "impressao-windows" / "fontes"

AZUL = "#1e40af"        # marca-700, a barra do topo
MARINHO = "#172554"     # marca-900
BRANCO = "#ffffff"


# --- símbolo -----------------------------------------------------------------------------

def _olho(cx: float, cy: float, a: float, s: float) -> str:
    """O olho em vesica: dois arcos de círculo com a mesma corda. a = meia largura,
    s = meia altura. Arco é mais limpo e simétrico que bézier desenhado à mão."""
    r = (a * a + s * s) / (2 * s)
    return (f"M{cx - a:g},{cy:g} A{r:.3f},{r:.3f} 0 0 1 {cx + a:g},{cy:g} "
            f"A{r:.3f},{r:.3f} 0 0 1 {cx - a:g},{cy:g} Z")


def _quadrado(x: float, y: float, lado: float, r: float) -> str:
    """Quadrado de cantos arredondados como caminho (para entrar no mesmo `evenodd`)."""
    return (f"M{x + r:g},{y:g} H{x + lado - r:g} A{r:g},{r:g} 0 0 1 {x + lado:g},{y + r:g} "
            f"V{y + lado - r:g} A{r:g},{r:g} 0 0 1 {x + lado - r:g},{y + lado:g} "
            f"H{x + r:g} A{r:g},{r:g} 0 0 1 {x:g},{y + lado - r:g} "
            f"V{y + r:g} A{r:g},{r:g} 0 0 1 {x + r:g},{y:g} Z")


def simbolo_caminho() -> str:
    """No quadro 64×64: olho de 54×28, íris de 20 e pupila de 8, centrados."""
    return " ".join((_olho(32, 32, 27, 14), _quadrado(22, 22, 20, 3), _quadrado(28, 28, 8, 1)))


def svg_simbolo(cor: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="5 18 54 28" role="img" '
            f'aria-label="OlhoDigital"><path fill="{cor}" fill-rule="evenodd" '
            f'd="{simbolo_caminho()}"/></svg>\n')


def svg_ladrilho() -> str:
    """O ícone do aplicativo e o favicon: o símbolo branco num ladrilho azul."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" '
            f'aria-label="OlhoDigital"><rect width="64" height="64" rx="14" fill="{AZUL}"/>'
            f'<path fill="{BRANCO}" fill-rule="evenodd" d="{simbolo_caminho()}"/></svg>\n')


# --- nome em contornos -------------------------------------------------------------------

def _texto(fonte: TTFont, texto: str, x: float, base: float, tamanho: float,
           espacamento: float = 0.0) -> tuple[str, float]:
    """(caminho, x final) do texto em contornos. `espacamento` em fração do tamanho."""
    escala = tamanho / fonte["head"].unitsPerEm
    glifos, cmap, larguras = fonte.getGlyphSet(), fonte.getBestCmap(), fonte["hmtx"]
    partes = []
    for ch in texto:
        nome = cmap[ord(ch)]
        caneta = SVGPathPen(glifos)
        glifos[nome].draw(TransformPen(caneta, (escala, 0, 0, -escala, x, base)))
        partes.append(caneta.getCommands())
        x += larguras[nome][0] * escala + espacamento * tamanho
    return " ".join(partes), x


def svg_horizontal(cor_olho: str, cor_nome: str, cor_digital: str) -> str:
    """Símbolo + "Olho" (Bold) + "Digital" (Regular). Altura de 40 unidades."""
    negrito = TTFont(FONTES / "NotoSans-Bold.ttf")
    regular = TTFont(FONTES / "NotoSans-Regular.ttf")
    # o símbolo ocupa 54×28 no quadro original; aqui, escalado para 40 de altura útil
    k = 40 / 28
    simbolo = (f'<path fill="{cor_olho}" fill-rule="evenodd" transform="translate(0 0) '
               f'scale({k:.4f}) translate(-5 -18)" d="{simbolo_caminho()}"/>')
    x0 = 54 * k + 14
    olho, x = _texto(negrito, "Olho", x0, 34, 36, -0.01)
    digital, x = _texto(regular, "Digital", x + 1, 34, 36, -0.01)
    largura = round(x + 2)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} 44" role="img" '
            f'aria-label="OlhoDigital">{simbolo}'
            f'<path fill="{cor_nome}" d="{olho}"/><path fill="{cor_digital}" d="{digital}"/>'
            f'</svg>\n')


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    arquivos = {
        "olhodigital.svg": svg_horizontal(AZUL, MARINHO, AZUL),          # fundo claro
        "olhodigital-branco.svg": svg_horizontal(BRANCO, BRANCO, "#bfdbfe"),  # fundo azul
        "olhodigital-simbolo.svg": svg_simbolo(AZUL),
        "olhodigital-simbolo-branco.svg": svg_simbolo(BRANCO),
        "olhodigital-app.svg": svg_ladrilho(),
    }
    for nome, conteudo in arquivos.items():
        (SAIDA / nome).write_text(conteudo, encoding="utf-8")
    # o favicon é o ladrilho: o único que lê a 16 px
    (RAIZ / "web" / "public" / "favicon.svg").write_text(svg_ladrilho(), encoding="utf-8")
    for lado in (180, 192, 512):
        subprocess.run(["rsvg-convert", "-w", str(lado), "-h", str(lado),
                        str(SAIDA / "olhodigital-app.svg"), "-o",
                        str(SAIDA / f"olhodigital-{lado}.png")], check=True)
    print("\n".join(sorted(p.name for p in SAIDA.iterdir())))


if __name__ == "__main__":
    main()
