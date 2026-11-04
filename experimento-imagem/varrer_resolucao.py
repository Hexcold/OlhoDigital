"""Quanto custa ler numa célula menor? Varredura de resolução sobre tinta real.

## A pergunta

A folha gerada ([[D10]]) tem célula de **5,58 mm de largura**; as folhas de
desenvolvimento têm **7,31 mm** (medido: o retângulo dos 4 marcadores mede
189,99 x 275,08 mm nos scans, e a coluna de nome ocupa a fração 0,2847–0,9776
dele em 18 células). A célula nova é 24% mais estreita, e todos os números de
leitura do TCC 2 — inclusive os 0,908 da CNN afinada — foram medidos na antiga.

O risco tem duas metades, e elas são separáveis:

  RESOLUÇÃO   menos pixels por célula. Mensurável AGORA, sem imprimir nada:
              basta encolher o warp das folhas antigas até a célula delas ter
              tantos pixels quanto a nova terá.
  CALIGRAFIA  gente escrevendo espremido escreve diferente. Só papel responde,
              e é o item de bancada do Backlog.

Este módulo mede a primeira. O que ele NÃO mede é a segunda — a tinta aqui foi
escrita em célula de 7,31 mm, só está sendo amostrada mais grosso.

## O confundidor, e por que ele tem uma coluna própria

`Image.cut_borders(2)` corta **2 pixels absolutos** de cada lado da célula, para
tirar o resíduo da grade impressa. Numa célula de 43,9 px isso é 4,6% do lado;
numa de 32,6 px é 6,1%; numa de 25 px, 8%. Ou seja: encolher o warp corta mais
traço, e a degradação que aparecesse poderia ser do corte e não da falta de
pixel. As duas séries rodam lado a lado — borda fixa e borda proporcional — e a
diferença entre elas é o tamanho do confundidor.

## O eixo

A razão do warp fica FIXA em 1140/1527. Ela não é a razão física do papel (ver
o cabeçalho do `sheet_cutter`): o warp estica a horizontal em ~1,08 nas duas
folhas, e é essa distorção que está nas células em que a CNN foi afinada.
Mexer nela aqui misturaria dois efeitos.

## O ponto que está fora do eixo (25/08 mediu só metade)

A varredura acima anda na diagonal — encolher o warp encolhe as duas dimensões
juntas. A folha gerada NÃO cai nessa diagonal, e é por construção: a célula
dela é quadrada e a de dev não é, então `LARGURA_WARP_GERADA = 1536` iguala a
horizontal em 43,9 px e deixa a vertical em 40,7 contra 45,6. A31 concluiu
"mesmo domínio de amostragem, de graça" a partir da horizontal só.

`warp_da_folha_gerada()` devolve o warp de dev que reproduz as DUAS dimensões
da gerada, e o ponto entra na mesma tabela com o mesmo McNemar pareado. Sem
ele, o que se tem sobre a vertical é um teto por dominância sobre o ponto 1000
— e teto deduzido não é medição.

Uso:
    python varrer_resolucao.py
    python varrer_resolucao.py --larguras 1140,846 --json dados/resolucao.json
"""

from __future__ import annotations

import os
os.environ.setdefault("KERAS_BACKEND", "torch")

import argparse
import json
import time

import numpy as np

from afinar_cnn import mcnemar
from avaliar import ALVOS, extrair_celulas, prever_cnn
from dados_treino import ALFABETO_MAIUSCULAS, ALFABETO_DIGITOS, normalizar_lote
from process import folha
from treinar_cnn import LADO

MODELO_BASE = "modelo/cnn_emnist_maiusculas_28x28.keras"
SAIDA = "dados/varredura_resolucao.json"

# 1140 é a resolução herdada, em que tudo foi medido. As demais descem até a
# folha gerada e um pouco além, para ver a forma da curva em vez de um ponto.
LARGURAS = (1140, 1000, 900, 846, 760, 660)

# Fração da célula que `cut_borders(2)` come no warp herdado. Usada como corte
# proporcional nas outras resoluções, para que a série de borda relativa tire
# sempre a mesma fatia de traço.
BORDA_FIXA = 2


def px_por_celula(largura_warp: int, altura_warp: int = 0,
                  ref: int | str = 3) -> tuple[float, float]:
    from dataclasses import replace
    m = folha(ref)
    razao = m.warp[0] / m.warp[1]
    m = replace(m, warp=(largura_warp,
                         altura_warp or round(largura_warp / razao)))
    return m.px_por_celula()


def warp_da_folha_gerada(ref: int | str = 3) -> tuple[int, int]:
    """Warp da folha de DEV cuja célula amostra como a da folha GERADA.

    A31 igualou a horizontal e parou ali: `LARGURA_WARP_GERADA = 1536` dá
    43,89 px de largura na gerada, os mesmos 43,88 que 1140 dá aqui. A vertical
    NÃO pode ser igualada junto, e não por falta de tentativa — a célula gerada
    é quadrada (5,580 x 5,580mm) e a de dev não é (7,314 x 8,211mm), então com
    a razão do warp preservada a altura cai em 40,66 px contra 45,58.

    Preservar a razão não é preciosismo: é ela que mantém a distorção do traço
    em 1,08 nas duas folhas (medido: 1,0809 na dev, 1,0794 na gerada), que é a
    porta pela qual A28 transfere. Consertar a vertical quebraria a horizontal.

    Logo a folha gerada opera FORA da linha isotrópica que a varredura de A31
    percorreu: 43,9 x 40,7 não é ponto nenhum daquela curva. Ela domina o ponto
    1000 (38,5 x 40,0) nas duas dimensões, o que dá um TETO de -0,030 por
    dominância — mas teto deduzido não é medição, e é isso que este ponto mede.
    """
    px_x, px_y = folha("gerada").px_por_celula()
    m = folha(ref)
    col = m.colunas["nome"]
    return (round(px_x * col["columns"] / (col["x_end"] - col["x_begin"])),
            round(px_y * m.linhas / (m.y_end - m.y_begin)))


def avaliar_ponto(modelo, X: np.ndarray, y: np.ndarray,
                  alfabeto: str) -> tuple[int, int, np.ndarray]:
    """Acertos da CNN base nas células dadas. Devolve o vetor pareado.

    A célula passa pelo MESMO `normalizar_mnist` do pipeline antes de entrar na
    rede — é o casamento de domínio que A19 mostrou valer 22,9 pontos quando
    esquecido. E é ele que torna esta varredura uma varredura de resolução e
    não de tamanho de entrada: toda célula chega à rede em 28x28, independente
    de quantos pixels tinha no papel retificado.
    """
    letras = list(alfabeto)
    indices, _ = prever_cnn(modelo, normalizar_lote(X, lado=LADO))
    preditos = np.array([letras[i] for i in indices])
    certo = preditos == y
    return int(certo.sum()), len(y), certo


def main() -> None:
    p = argparse.ArgumentParser(description="Custo de resolução na leitura da célula.")
    p.add_argument("--larguras", default=",".join(str(l) for l in LARGURAS))
    p.add_argument("--coluna", default="nome", choices=("nome", "matricula"))
    p.add_argument("--json", default=SAIDA)
    p.add_argument("--sem-gerada", dest="gerada", action="store_false",
                   help="não medir o ponto anisotrópico da folha gerada")
    args = p.parse_args()

    larguras = [int(x) for x in args.larguras.split(",")]
    borda_ref = BORDA_FIXA / px_por_celula(larguras[0])[0]
    razao_dev = folha(3).warp[0] / folha(3).warp[1]

    # (rótulo, largura, altura). Altura 0 = derivada da razão, que é a linha
    # isotrópica de A31. O ponto "gerada" é o único fora dela, e é a folha que
    # vai para a impressora.
    pontos: list[tuple[str, int, int]] = [(str(l), l, 0) for l in larguras]
    if args.gerada:
        pontos.append(("gerada", *warp_da_folha_gerada()))

    alfabeto = (ALFABETO_MAIUSCULAS if args.coluna == "nome"
                else ALFABETO_DIGITOS)

    from keras import saving
    modelo = saving.load_model(MODELO_BASE)
    print(f"CNN base: {modelo.count_params():,} parâmetros | "
          f"coluna={args.coluna} | {len(alfabeto)} classes\n")

    print(f"{'ponto':>6} {'px/célula':>12} {'borda fixa (2px)':>22} "
          f"{'borda proporcional':>22}")
    print(f"{'':6} {'':12} {'acertos    acurácia':>22} {'acertos    acurácia':>22}")

    registros = []
    acertos_por_ponto: dict[str, np.ndarray] = {}
    for nome_ponto, largura, altura in pontos:
        px_x, px_y = px_por_celula(largura, altura)
        linha = {"ponto": nome_ponto, "largura_warp": largura,
                 "altura_warp": altura or round(largura / razao_dev),
                 "px_x": px_x, "px_y": px_y}
        for rotulo, kwargs in (("fixa", {"borda": BORDA_FIXA}),
                               ("relativa", {"borda_relativa": borda_ref})):
            t0 = time.perf_counter()
            X, y, g = extrair_celulas(ALVOS[args.coluna], args.coluna,
                                      largura_warp=largura,
                                      altura_warp=altura, **kwargs)
            acertos, n, certo = avaliar_ponto(modelo, X, y, alfabeto)
            linha[rotulo] = {"acertos": acertos, "n": n,
                             "acuracia": acertos / n if n else 0.0,
                             "corte_px": (BORDA_FIXA if rotulo == "fixa"
                                          else max(1, round(borda_ref * px_x))),
                             "segundos": time.perf_counter() - t0}
            if rotulo == "fixa":
                acertos_por_ponto[nome_ponto] = certo
        print(f"{nome_ponto:>6} {px_x:>5.1f}x{px_y:<5.1f} "
              f"{linha['fixa']['acertos']:>10}/{linha['fixa']['n']:<4} "
              f"{linha['fixa']['acuracia']:>7.4f} "
              f"{linha['relativa']['acertos']:>10}/{linha['relativa']['n']:<4} "
              f"{linha['relativa']['acuracia']:>7.4f}")
        registros.append(linha)

    # As células são AS MESMAS em todos os pontos — mesma tinta, só amostrada
    # mais grosso. Comparar por proporção jogaria fora esse pareamento, e é o
    # pareamento que dá poder com n=304. McNemar exato, como em KNN x CNN.
    base = pontos[0][0]
    base_certo = acertos_por_ponto[base]
    print(f"\n=== McNemar pareado contra o warp {base} "
          f"({registros[0]['px_x']:.1f}px), borda fixa ===")
    print(f"{'ponto':>6} {'px':>13} {'Δ acurácia':>11} {'só ref':>7} "
          f"{'só este':>8} {'p':>9}")
    for (nome_ponto, _, _), r in zip(pontos[1:], registros[1:]):
        certo = acertos_por_ponto[nome_ponto]
        so_ref, so_este, p = mcnemar(base_certo, certo)
        r["mcnemar_vs_referencia"] = {"so_referencia": so_ref,
                                      "so_este": so_este, "p": p}
        print(f"{nome_ponto:>6} {r['px_x']:>6.1f}x{r['px_y']:<6.1f} "
              f"{r['fixa']['acuracia'] - registros[0]['fixa']['acuracia']:>+11.4f} "
              f"{so_ref:>7} {so_este:>8} {p:>9.4f}")

    # a curva não é monótona; dizer se isso é ruído é o mesmo teste, entre
    # pontos vizinhos. Só na linha isotrópica: o ponto da folha gerada não está
    # nela, então não tem vizinho de quem ser sucessor.
    print(f"\n=== vizinhos consecutivos (a queda é monótona?) ===")
    for a, b in zip(larguras, larguras[1:]):
        so_a, so_b, p = mcnemar(acertos_por_ponto[str(a)], acertos_por_ponto[str(b)])
        print(f"  {a:>4} -> {b:<4}  só {a}: {so_a:>3}  só {b}: {so_b:>3}  "
              f"p={p:.4f}")

    # A dominância que dá o TETO: a gerada tem mais px que o ponto 1000 nas duas
    # dimensões. Se ela cair ABAIXO dele, a dominância não vale como cota e o
    # argumento de A31 precisa ser refeito — por isso o teste, e não só a conta.
    if args.gerada and "1000" in acertos_por_ponto:
        so_dom, so_ger, p = mcnemar(acertos_por_ponto["1000"],
                                    acertos_por_ponto["gerada"])
        print(f"\n=== a dominância se confirma? gerada (43,9x40,7) x 1000 (38,5x40,0) ===")
        print(f"  só 1000: {so_dom}  só gerada: {so_ger}  p={p:.4f}")

    with open(args.json, "w") as f:
        json.dump(registros, f, indent=2)
    print(f"\nregistros crus em {args.json}")


if __name__ == "__main__":
    main()
