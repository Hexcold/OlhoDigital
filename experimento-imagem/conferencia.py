"""Gera a página de conferência das folhas já preenchidas.

O problema que ela resolve: para medir as regras de normalização de nome é preciso
saber QUEM é cada linha, e transcrever 64 linhas à mão é caro. Mas o cenário de
cada linha (contração, nome social, omissão, truncamento) sai do padrão de
comprimentos das palavras, que o pipeline já extrai. Então o que falta por linha
é só a matrícula — e dessa o OCR já propõe uma.

A página põe lado a lado, por linha: o recorte da imagem, a matrícula lida com a
confiança de cada dígito, e os candidatos ranqueados da base. O trabalho humano
vira conferir, não transcrever.

É também o primeiro protótipo da interface de revisão de quarentena da Fase 3 —
mesmo problema de fundo: mostrar a leitura ao lado de candidatos ranqueados para
um humano decidir rápido.

Saída em dados/, que é ignorada pelo git: contém nome e matrícula reais (LGPD).

Uso:
    python conferencia.py
"""

from __future__ import annotations
import base64
import html
import os
import sys

import cv2
import numpy as np

from image import Image
from align import Align
from ocr import OCR
from region import Decomposer
from sheet_cutter import SheetCutter
from process import folha, carregar_gabarito

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base          # noqa: E402
from normalizacao import normalizar            # noqa: E402
# só a matrícula: esta página ranqueia por matrícula para conferir a leitura,
# não faz identificação. Quem identifica é o `identificar_folha`.
from fuzzy import sim_levenshtein               # noqa: E402

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
SAIDA = "dados/conferencia.html"
FOLHAS_DEV = [("m1_1", 1), ("m1_2", 1), ("m1_3", 1),
              ("m2-1", 2), ("m2-2", 2), ("m3_1", 3), ("m3_2", 3)]


def tem_tinta(celula: Image) -> bool:
    """Teste barato de célula escrita, sem rodar componentes conexos.

    A decomposição em regiões é O(pixels) em Python puro e custa ~0,12 s por
    célula; rodá-la nas 3.600 células das seis folhas levaria minutos. Para
    apenas decidir se a linha foi preenchida, a fração de tinta basta — o
    critério fino de `is_empty` só é necessário onde vamos classificar.
    """
    b = celula.binarize(gaussian=False).inversion().cut_borders(3)
    return b.written_ratio() > 0.02


def ler_matricula(celulas: list[Image], ocr: OCR) -> tuple[str, list[float]]:
    """Lê as 6 células da matrícula. Devolve a string e a confiança por dígito."""
    digitos, confs = [], []
    for cel in celulas:
        img = cel.binarize(gaussian=False).inversion().cut_borders(2)
        filtrada = Decomposer(img).erase_small_regions(0.2).erase_regions_outside_bounds()
        if filtrada.get_image().is_empty():
            digitos.append("."); confs.append(0.0)
            continue
        pred = ocr.prever_de_imagem_filtrada(filtrada)
        if pred is None:
            digitos.append("_"); confs.append(0.0)
            continue
        digitos.append(pred.caractere); confs.append(pred.confianca)
    return "".join(digitos), confs


def padrao_palavras(celulas: list[Image]) -> str:
    """Estrutura de palavras da linha de nome, por ocupação das células.

    É o que permite classificar o cenário sem transcrever letra: comparar este
    padrão com os comprimentos das palavras do nome da base já diz se houve
    contração, omissão ou truncamento.
    """
    marcas = ["#" if tem_tinta(c) else "." for c in celulas]
    return "".join(marcas)


def comprimentos(padrao: str) -> list[int]:
    return [len(p) for p in padrao.replace(".", " ").split()]


def candidatos(matricula_lida: str, base: list[dict], n: int = 3) -> list[tuple[float, dict]]:
    """Ranqueia a base só pelo canal da matrícula: o nome ainda não foi lido."""
    limpa = matricula_lida.replace(".", "?").replace("_", "?")
    pontuados = [(sim_levenshtein(limpa, a["matricula"]), a) for a in base]
    pontuados.sort(key=lambda t: -t[0])
    return pontuados[:n]


def png_base64(recorte: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", recorte)
    if not ok:
        return ""
    return base64.b64encode(buf).decode("ascii")


def processar_folha(nome_folha: str, ref_folha: int | str, base: list[dict],
                    ocr: OCR) -> list[dict]:
    caminho = f"dados/{nome_folha}.png"
    if not os.path.exists(caminho):
        return []
    gabarito = carregar_gabarito(caminho)
    modelo = folha(ref_folha)
    alinhada = Image().set_data(Align(*modelo.warp).aruco(
        Image().load_from_file(caminho).data))

    cortador = SheetCutter("cache_temp")
    mats, _ = cortador.cut_info(alinhada, modelo, "matricula")
    nomes, _ = cortador.cut_info(alinhada, modelo, "nome")

    h, w = alinhada.get_h_w()
    x0, x1 = modelo.faixa_x(w)

    linhas = []
    for i in range(modelo.linhas):
        padrao = padrao_palavras(nomes[i])
        if "#" not in padrao and not any(tem_tinta(c) for c in mats[i]):
            continue  # linha em branco

        lida, confs = ler_matricula(mats[i], ocr)
        topo = candidatos(lida, base)
        ya, yb = modelo.faixa_y(h, i, folga=2)
        recorte = alinhada.data[max(0, ya):yb, x0:x1]

        linhas.append({
            "folha": nome_folha, "linha": i + 1,
            "img": png_base64(recorte),
            "lida": lida, "confs": confs,
            "padrao": padrao, "comprimentos": comprimentos(padrao),
            "candidatos": topo,
            "gabarito": gabarito.get(i + 1),
        })
    return linhas


def cor_confianca(c: float) -> str:
    if c >= 0.999:
        return "ok"
    if c >= 0.6:
        return "duvida"
    return "ruim"


def render(linhas: list[dict]) -> str:
    partes = ["""<meta charset="utf-8"><title>Conferência das folhas</title>
<style>
:root{--bg:#fbfaf8;--fg:#1c1a17;--linha:#e2ddd5;--caixa:#fff;--sub:#6b645b;
      --ok:#1a7f4b;--duvida:#b06a00;--ruim:#b3261e;--marca:#3d5a8a}
@media (prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#eae7e1;--linha:#2f2c34;
      --caixa:#1e1d23;--sub:#9a938a;--ok:#57c98a;--duvida:#e0a34a;--ruim:#f2857d;--marca:#8fabd8}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
     font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--sub);margin:0 0 24px;max-width:70ch}
.folha{font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
       color:var(--sub);margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--linha)}
.linha{background:var(--caixa);border:1px solid var(--linha);border-radius:10px;
       padding:12px 14px;margin-bottom:12px}
.tira{overflow-x:auto;margin:-2px 0 10px}
.tira img{display:block;height:44px;image-rendering:crisp-edges;
          border-radius:4px;background:#fff;min-width:0}
.dados{display:flex;flex-wrap:wrap;gap:18px;align-items:baseline;font-size:14px}
.rot{color:var(--sub);font-size:12px;letter-spacing:.04em;text-transform:uppercase;
     display:block;margin-bottom:2px}
.mat{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:19px;letter-spacing:.14em}
.ok{color:var(--ok)}.duvida{color:var(--duvida)}.ruim{color:var(--ruim)}
.pad{font-family:ui-monospace,Menlo,monospace;color:var(--marca);font-size:14px;letter-spacing:.02em}
.cands{margin:10px 0 0;padding:0;list-style:none;font-size:14px}
.cands li{padding:3px 0;border-top:1px dashed var(--linha)}
.cands li:first-child{border-top:0}
.score{font-family:ui-monospace,Menlo,monospace;color:var(--sub);font-size:12px}
.gab{color:var(--ok);font-weight:600}
.aviso{background:var(--caixa);border:1px solid var(--linha);border-left:3px solid var(--duvida);
       border-radius:8px;padding:12px 14px;margin-bottom:20px;font-size:14px;max-width:80ch}
</style>
<h1>Conferência das folhas de desenvolvimento</h1>
<p class="sub">Uma linha por pessoa. Confira <strong>só a matrícula</strong> — o cenário de
preenchimento sai do padrão de palavras, sem precisar transcrever letra. Marque as
que estiverem erradas e me devolva a lista.</p>
<div class="aviso"><strong>Contém dado pessoal real.</strong> Arquivo gerado em
<code>dados/</code>, que o git ignora. Não subir para lugar nenhum.</div>"""]

    folha_atual = None
    for L in linhas:
        if L["folha"] != folha_atual:
            folha_atual = L["folha"]
            partes.append(f'<div class="folha">{html.escape(folha_atual)}</div>')

        digitos = "".join(
            f'<span class="{cor_confianca(c)}">{html.escape(d)}</span>'
            for d, c in zip(L["lida"], L["confs"]))

        partes.append('<div class="linha">')
        if L["img"]:
            partes.append(f'<div class="tira"><img src="data:image/png;base64,{L["img"]}" alt=""></div>')
        partes.append('<div class="dados">')
        partes.append(f'<div><span class="rot">linha</span><strong>{L["linha"]:02d}</strong></div>')
        partes.append(f'<div><span class="rot">matrícula lida</span><span class="mat">{digitos}</span></div>')
        partes.append(f'<div><span class="rot">estrutura do nome</span>'
                      f'<span class="pad">{html.escape(L["padrao"])}</span> '
                      f'<span class="score">{L["comprimentos"]}</span></div>')
        if L["gabarito"] and L["gabarito"][0]:
            partes.append(f'<div><span class="rot">gabarito conhecido</span>'
                          f'<span class="gab">{html.escape(L["gabarito"][0])}</span></div>')
        partes.append('</div>')

        partes.append('<ul class="cands">')
        for s, a in L["candidatos"]:
            partes.append(f'<li><span class="score">{s:.3f}</span> &nbsp;'
                          f'<strong>{html.escape(a["matricula"])}</strong> &nbsp;'
                          f'{html.escape(normalizar(a["nome"]))} '
                          f'<span class="score">· {html.escape(a["curso"] or "?")}</span></li>')
        partes.append('</ul></div>')

    return "\n".join(partes)


if __name__ == "__main__":
    print("carregando base...")
    base = carregar_base(BASE_XLSX)
    ocr = OCR()

    todas: list[dict] = []
    for nome_folha, ref in FOLHAS_DEV:
        linhas = processar_folha(nome_folha, ref, base, ocr)
        print(f"  {nome_folha}: {len(linhas)} linhas preenchidas")
        todas.extend(linhas)

    os.makedirs("dados", exist_ok=True)
    with open(SAIDA, "w") as f:
        f.write(render(todas))
    tam = os.path.getsize(SAIDA) / 1024
    print(f"\n{len(todas)} linhas em {SAIDA} ({tam:.0f} KB)")
