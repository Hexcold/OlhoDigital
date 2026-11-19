"""Recorte da célula que tira a grade pela tinta, e não pelo corte cego de borda (A56, D23).

A grade da folha é cinza por projeto (`gerar_folha.COR_GRADE` = #8c8c8c) e a caneta não. Na
gráfica dos Encontros a grade saiu escura (tom ~70 depois do escaneamento) e 1 px abaixo do
modelo (A54): a binarização a trata como tinta, e o corte de 2 px de borda que a removia levava
junto o traço que a letra apoia na linha — o E vira F, o L vira I, o 2 vira 7 (A56). Foi um 2
assim que produziu o único aceite errado de pessoa da base na calibração do Experimento 03.

Aqui cada uma das quatro linhas da grade é achada por projeção numa janela com folga em volta da
célula, e nas fileiras (ou colunas) dela só fica o pixel bem mais escuro que a própria linha: a
caneta sobre a linha é mais escura que as duas sozinhas. Fora das linhas vale o Otsu da célula,
o mesmo do recorte de sempre. Linha que não é achada (grade clara, que some na binarização, como
nas folhas de dev, tom ~204) cai no corte de 2 px de sempre.

Medido na calibração do Experimento 03: letras 0,924 → 0,958 e dígitos 0,930 → 0,948 por célula,
e as trocas do traço de baixo de 63 para 8. No teste, o braço com este recorte e a regra dos dois
canais fez 583 aceites e 0 erros (A58). Este módulo é cópia literal do recorte "V3" medido lá
(`coleta-eu2026/scripts/recorte_variantes.py`, sha256 e6f43a6b…), conferida contra as leituras
gravadas em `scripts/conferir_servidor_v3.py`.

Parâmetros fixados antes de medir, e não varridos — escolhê-los pelo acerto nas mesmas células
seria calibrar no que se mede.
"""
from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from image import Image
from region import Decomposer

# A linha da grade tem que caber inteira na janela, com o desvio de A54 (~1 px) e o que a impressão
# variar. A célula da v4 tem ~44 x 41 px depois do warp.
FOLGA = 6
RAIO = 5          # até onde, em px, a linha real pode estar da linha do modelo
NUCLEO = 0.75     # fração de tinta numa fileira para ela ser núcleo de linha da grade
# Nas fileiras da linha, fica o pixel abaixo deste fator x a mediana da fileira (o tom da linha).
FATOR_CANETA = 0.6


def nucleo(frac: np.ndarray, esperado: int) -> tuple[int, int] | None:
    """Fileiras perto de `esperado` que são o núcleo da linha da grade (tinta > NUCLEO).

    Se o núcleo vier partido (um traço longo ao lado da linha), fica o grupo mais perto
    da posição esperada.
    """
    lo, hi = max(0, esperado - RAIO), min(len(frac), esperado + RAIO + 1)
    idx = [k for k in range(lo, hi) if frac[k] > NUCLEO]
    if not idx:
        return None
    grupos, atual = [], [idx[0]]
    for k in idx[1:]:
        if k == atual[-1] + 1:
            atual.append(k)
        else:
            grupos.append(atual)
            atual = [k]
    grupos.append(atual)
    g = min(grupos, key=lambda g: abs((g[0] + g[-1]) / 2 - esperado))
    return g[0], g[-1]


def limpar(cinza: np.ndarray, caixa: tuple[int, int, int, int],
           contagem: Counter | None = None) -> tuple[Image, tuple[float, float]]:
    """A célula `caixa` (x1, y1, x2, y2) da folha alinhada `cinza`, sem a grade e com o traço.

    Devolve a imagem binária invertida (tinta = 255) entre as linhas da grade e os limites do
    filtro de tamanho em px, os mesmos da célula do recorte de sempre — o filtro relativo, num
    recorte maior, apagava a barra do meio do E escrita solta. `contagem`, se vier, recebe as
    linhas que não foram achadas.
    """
    x1, y1, x2, y2 = caixa
    H, W = cinza.shape
    ya, yb = max(0, y1 - FOLGA), min(H, y2 + FOLGA)
    xa, xb = max(0, x1 - FOLGA), min(W, x2 + FOLGA)
    t, _ = cv2.threshold(cinza[y1:y2, x1:x2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    g = cinza[ya:yb, xa:xb].astype(np.float32)
    M = g <= t
    nucleos = {}
    for lado, frac, esperado in (("cima", M.mean(axis=1), y1 - ya), ("baixo", M.mean(axis=1), y2 - ya),
                                 ("esq", M.mean(axis=0), x1 - xa), ("dir", M.mean(axis=0), x2 - xa)):
        nucleos[lado] = nucleo(frac, esperado)
        if nucleos[lado] is None and contagem is not None:
            contagem[f"V3 linha não achada: {lado}"] += 1
    for lado in ("cima", "baixo"):
        if nucleos[lado]:
            c0, c1 = nucleos[lado]
            for r in range(max(0, c0 - 1), min(M.shape[0], c1 + 2)):
                M[r, :] = g[r, :] < FATOR_CANETA * np.median(g[r, :])
    for lado in ("esq", "dir"):
        if nucleos[lado]:
            c0, c1 = nucleos[lado]
            for c in range(max(0, c0 - 1), min(M.shape[1], c1 + 2)):
                M[:, c] &= g[:, c] < FATOR_CANETA * np.median(g[:, c])
    lim = {}
    for lado, esperado, dentro, n in (("cima", y1 - ya, +1, M.shape[0]), ("baixo", y2 - ya, -1, M.shape[0]),
                                      ("esq", x1 - xa, +1, M.shape[1]), ("dir", x2 - xa, -1, M.shape[1])):
        nuc = nucleos[lado]
        if nuc is None:
            lim[lado] = esperado + (2 if dentro > 0 else -3)    # o corte de 2 px de sempre
        else:
            lim[lado] = max(0, nuc[0] - 1) if dentro > 0 else min(n - 1, nuc[1] + 1)
    recorte = M[lim["cima"]:lim["baixo"] + 1, lim["esq"]:lim["dir"] + 1]
    img = Image().set_data((recorte * 255).astype(np.uint8)).set_binary(True).set_inversed(True)
    # o filtro de tamanho de sempre mede 20% da célula já cortada 2 px de cada lado
    return img, (0.2 * (y2 - y1 - 4), 0.2 * (x2 - x1 - 4))


def filtrar(img: Image, limites: tuple[float, float]) -> Decomposer:
    """O filtro de componentes de sempre, com o limite de tamanho em px em vez de fração."""
    dec = Decomposer(img)
    lim_h, lim_w = limites
    dec.regions = [r for r in dec.regions if r.get_h_w()[0] > lim_h or r.get_h_w()[1] > lim_w]
    return dec.erase_regions_outside_bounds()


def celula_sintetica(tom_grade: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Uma célula de 44 x 41 px com grade de 3 px no tom pedido e um L de caneta (tom 30) cujo
    pé está em cima da linha de baixo, 1 px abaixo da borda do modelo, como na gráfica (A54)."""
    cinza = np.full((120, 120), 250, np.uint8)
    x1, y1, x2, y2 = 30, 30, 74, 71
    for y in (y1 + 1, y2 + 1):                  # a grade real caiu 1 px
        cinza[y:y + 3, 10:110] = tom_grade
    for x in (x1, x2):
        cinza[10:110, x:x + 3] = tom_grade
    cinza[38:73, 40:44] = 30                    # a haste do L
    cinza[70:74, 40:64] = 30                    # o pé, sobre a linha de baixo
    return cinza, (x1, y1, x2, y2)


if __name__ == "__main__":
    # Autoconferência: o pé do L some no recorte de sempre e fica neste; grade clara não quebra.
    def corte_de_sempre(cinza, caixa):
        x1, y1, x2, y2 = caixa
        cel = Image().set_data(cinza[y1:y2, x1:x2].copy()).binarize(gaussian=False).inversion().cut_borders(2)
        return Decomposer(cel).erase_small_regions(0.2).erase_regions_outside_bounds().get_image().data

    def largura_do_pe(img: np.ndarray) -> int:
        """Maior número de pixels de tinta numa das 5 fileiras de baixo."""
        return int((img[-5:] > 0).sum(axis=1).max()) if img.size else 0

    cinza, caixa = celula_sintetica(80)
    antes = corte_de_sempre(cinza, caixa)
    depois = filtrar(*limpar(cinza, caixa)).get_image().data
    print(f"grade escura: pé do L no recorte de sempre {largura_do_pe(antes)} px, "
          f"no V3 {largura_do_pe(depois)} px")
    # o pé tem 24 px: some no corte de sempre (só sobra a haste, 4 px) e volta no V3
    assert largura_do_pe(antes) < 8, "o recorte de sempre devia perder o pé"
    assert largura_do_pe(depois) >= 18, "o V3 tinha que devolver o pé"
    c = Counter()
    cinza, caixa = celula_sintetica(205)
    clara = filtrar(*limpar(cinza, caixa, c)).get_image().data
    print(f"grade clara: linhas não achadas {dict(c) or 0} | tinta na célula: {int((clara > 0).sum())} px "
          f"| pé {largura_do_pe(clara)} px")
    assert (clara > 0).sum() > 0, "grade clara: a letra não pode sumir"
    print("autoconferência ok")
