"""Avalia um classificador contra o gabarito das folhas reais.

Serviu primeiro ao desenho 2x2 que separou duas causas da acurácia baixa nos
dígitos (A07):

                    | normalização ingênua | normalização MNIST
    digits.png (5k) |   A (o protótipo)    |   D
    MNIST     (60k) |   B                  |   C

Se B >> A, o problema era o dado. Se D >> A, era o pré-processamento. Foi D.

Agora serve também à coluna de NOME, onde a pergunta é outra: o EMNIST ByClass
é 10,1x desbalanceado (O=24.983 amostras, K=2.468), e as classes densas são
exatamente as que aparecem erradamente na saída. `--por-classe` testa se
equilibrar o conjunto corrige isso.

As células da folha são extraídas UMA vez e guardadas em cache (.npz); os
modelos são avaliados muitas vezes em cima delas. Mesmo princípio do
`analise_limiar.py`: pontue uma vez, limiarize muitas.
"""

from __future__ import annotations
import argparse
import os
from dataclasses import replace
import numpy as np
from numpy.typing import NDArray
from sklearn.neighbors import KNeighborsClassifier
from sklearn.model_selection import train_test_split

from image import Image
from align import Align
from border import Border
from region import Decomposer
from sheet_cutter import SheetCutter
from process import carregar_gabarito, folha
from treinar_knn import subamostrar
from dados_treino import (
    carregar_digits_png, carregar_mnist, carregar_emnist_maiusculas,
    normalizar_ingenuo, normalizar_mnist, normalizar_lote, _bounding_box,
    ALFABETO_DIGITOS, ALFABETO_MAIUSCULAS,
)

SEMENTE = 42

# Folhas com gabarito, por coluna. O critério é UM só, e é a procedência da
# verdade: entram as folhas cujo rótulo veio de fonte que não é a própria imagem.
#
#   m3_2   lista de participantes, conferida 22/22 (A25)
#   m4_01  as 7 identidades confirmadas pelo autor em 27/08
#   m7_02  as 16 identidades confirmadas pelo autor em 27/08
#
# Saíram a m2-1 e a m3_1, cujos gabaritos foram TRANSCRITOS da própria imagem —
# a circularidade que A25 alerta. Elas respondiam por 108 das 240 células de
# dígito, e medir em rótulo não verificado já é ruim; treinar nele (o afinamento
# de A28) é pior. Decisão do autor, 27/08.
#
# Consequência a não esquecer: os números de A28 foram medidos nas 304 células
# da m3_2 sozinha e **não são diretamente comparáveis** com o que sair daqui.
# Em troca, o nome passa de 304 para 694 células e a matrícula de 132
# verificadas para 270, e as duas folhas novas trazem a geometria da coleta.
ALVOS = {
    "matricula": [("dados/m3_2.png", 3), ("dados/m4_01.png", "gerada_v1"),
                  ("dados/m7_02.png", "gerada_v2")],
    "nome": [("dados/m3_2.png", 3), ("dados/m4_01.png", "gerada_v1"),
             ("dados/m7_02.png", "gerada_v2")],
}


def extrair_celulas(alvos: list[tuple[str, int | str]], coluna: str,
                    largura_warp: int = 0, borda: int = 2,
                    borda_relativa: float = 0.0, altura_warp: int = 0
                    ) -> tuple[NDArray[np.uint8], NDArray[np.str_], NDArray[np.str_]]:
    """Roda o pipeline até a imagem filtrada e devolve as células com gabarito.

    Guarda a célula BINÁRIA FILTRADA (antes de qualquer normalização), para que
    as normalizações possam ser comparadas sobre a mesma entrada. O rótulo é o
    CARACTERE, não um índice: cada modelo mapeia pelo seu próprio alfabeto.

    O terceiro retorno é a PROCEDÊNCIA — `"folha/linha"` de cada célula. Cada
    linha da folha foi preenchida por uma pessoa diferente, então agrupar por
    linha é agrupar por escritor, e é isso que permite uma validação cruzada
    que responde "transfere para alguém novo?" em vez de "decorou esta letra?".

    `largura_warp` sobrescreve a resolução do warp mantendo a razão do modelo,
    e existe só para varrer o efeito da RESOLUÇÃO sobre a leitura — a folha
    gerada tem célula 24% mais estreita que a das folhas de desenvolvimento, e
    encolher o warp destas é como simular aquela sem imprimir nada.

    `altura_warp` solta a altura da razão, e sem ele a folha gerada não é
    representável: a célula dela é QUADRADA e a de dev não é (7,314 x 8,211mm),
    então igualar a horizontal em 43,9 px — que é o que A31 fez e o que
    `LARGURA_WARP_GERADA` entrega — deixa a vertical cair em 40,66 px, 10,8%
    abaixo do domínio em que a leitura foi medida. Esse ponto está FORA da
    linha isotrópica que a razão descreve, e é onde a folha gerada vai operar.

    `borda_relativa`, quando dada, substitui os `borda` pixels fixos por uma
    fração do lado da célula. `cut_borders` sempre cortou 2 px absolutos, o que
    em célula menor come proporcionalmente mais traço — confundidor que a
    varredura de resolução precisa separar da perda de informação em si.
    """
    campo = 1 if coluna == "nome" else 0
    celulas: list[NDArray[np.uint8]] = []
    rotulos: list[str] = []
    grupos: list[str] = []

    for caminho_folha, ref in alvos:
        if not os.path.exists(caminho_folha):
            continue
        modelo = folha(ref)
        if largura_warp or altura_warp:
            razao = modelo.warp[0] / modelo.warp[1]
            largura = largura_warp or modelo.warp[0]
            modelo = replace(modelo, warp=(largura,
                                           altura_warp or round(largura / razao)))
        corte = borda
        if borda_relativa:
            corte = max(1, round(borda_relativa * modelo.px_por_celula()[0]))
        gabarito = carregar_gabarito(caminho_folha)
        img = Image().load_from_file(caminho_folha)
        alinhada = Image().set_data(Align(*modelo.warp).aruco(img.data))
        linhas, _ = SheetCutter("cache_temp").cut_info(alinhada, modelo, coluna)

        for i, linha in enumerate(linhas):
            num = i + 1
            if num not in gabarito:
                continue
            esperado = gabarito[num][campo]
            for j, celula in enumerate(linha):
                if j >= len(esperado) or esperado[j] == " ":
                    continue          # espaço não é caractere a reconhecer
                bin_ = celula.binarize(gaussian=False).inversion().cut_borders(corte)
                filtrada = Decomposer(bin_).erase_small_regions(0.2).erase_regions_outside_bounds()
                alvo = filtrada.get_image()
                if alvo.is_empty():
                    continue
                # padroniza num quadro fixo grande, sem escalar: preserva o traço cru
                dados = alvo.data
                quadro = np.zeros((64, 64), dtype=np.uint8)
                h, w = dados.shape[:2]
                h, w = min(h, 64), min(w, 64)
                quadro[:h, :w] = dados[:h, :w]
                celulas.append(quadro)
                rotulos.append(esperado[j])
                grupos.append(f"{os.path.basename(caminho_folha)}/{num:02d}")

    return (np.array(celulas, dtype=np.uint8), np.array(rotulos),
            np.array(grupos))


def assinatura(alvos: list[tuple[str, int | str]]) -> str:
    """O que o cache precisa descrever: quais folhas, com qual modelo de folha.

    Existe porque o cache não tinha chave nenhuma. Quando a m2-1 e a m3_1
    entraram em `ALVOS["matricula"]`, o `cache_celulas_matricula.npz` continuou
    devolvendo as 132 células da m3_2 sozinha — em silêncio, e por três sessões.
    Cache cuja chave não descreve o conteúdo é o mesmo defeito do prefixo
    inferido do `SheetCutter`: o estado errado passa por certo sem reclamar.
    """
    return "|".join(f"{caminho}:{ref}" for caminho, ref in sorted(alvos))


def carregar_celulas(coluna: str, recriar: bool = False, com_grupo: bool = False):
    """Células reais com gabarito, do cache. `com_grupo` devolve a procedência.

    O cache é descartado quando os alvos mudam, quando falta a procedência
    (campo acrescentado depois) ou quando `recriar` é pedido. Reextrair custa o
    pipeline de imagem inteiro, então não se recria à toa — mas devolver o
    conteúdo errado custa mais.
    """
    cache = f"cache_celulas_{coluna}.npz"
    alvos = ALVOS[coluna]
    esperada = assinatura(alvos)
    if os.path.exists(cache) and not recriar:
        d = np.load(cache)
        gravada = str(d["alvos"]) if "alvos" in d else ""
        if gravada != esperada:
            print(f"  cache de {coluna} obsoleto, reextraindo\n"
                  f"    gravado:  {gravada or '(sem assinatura)'}\n"
                  f"    esperado: {esperada}")
        elif not com_grupo:
            return d["X"], d["y"]
        elif "g" in d:
            return d["X"], d["y"], d["g"]
    X, y, g = extrair_celulas(alvos, coluna)
    np.savez_compressed(cache, X=X, y=y, g=g, alvos=esperada)
    return (X, y, g) if com_grupo else (X, y)


def treinar(X_img: NDArray[np.uint8], y: NDArray[np.int32], k: int, peso: str,
            lado: int, por_classe: int = 0, norm=None):
    """Treina KNN aplicando ao conjunto a MESMA normalização usada no teste.

    O par tem que ser casado. Treinar cru e testar normalizado custou 22,9
    pontos nas letras — ver `dados_treino.normalizar_lote`.
    """
    if por_classe:
        X_img, y = subamostrar(X_img, y, por_classe, SEMENTE)
    X_img = normalizar_lote(X_img, norm, lado)
    X = X_img.reshape(len(X_img), -1).astype(np.float32)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=SEMENTE, stratify=y)
    modelo = KNeighborsClassifier(n_neighbors=k, weights=peso).fit(X_tr, y_tr)
    n_amostra = min(2000, len(y_te))
    holdout = float(modelo.score(X_te[:n_amostra], y_te[:n_amostra]))
    return modelo, holdout, n_amostra


def prever_knn(modelo, X_img: NDArray[np.uint8]):
    """KNN do sklearn: entrada plana, confiança = fração dos k votos."""
    X = X_img.reshape(len(X_img), -1).astype(np.float32)
    return modelo.predict(X), modelo.predict_proba(X).max(axis=1)


def prever_cnn(modelo, X_img: NDArray[np.uint8]):
    """CNN do Keras: entrada (n,lado,lado,1) em [0,1], confiança = softmax.

    A diferença que importa para o projeto não é a interface, é a ESCALA da
    confiança. O KNN devolve k+1 valores possíveis — 4 com k=3 — e é essa
    granularidade grossa que impede calibrar o corte do `?` (A20). A softmax é
    contínua, então o corte da abstenção do D02 passa a ter onde se mover.
    """
    X = (X_img.astype(np.float32) / 255.0)[..., None]
    probs = modelo.predict(X, verbose=0)
    return probs.argmax(axis=1), probs.max(axis=1)


def avaliar(modelo, alfabeto: str, celulas: NDArray[np.uint8],
            rotulos: NDArray[np.str_], norm, lado: int,
            prever=prever_knn, corte: float = 0.999):
    """Normaliza as células reais e mede acurácia, cobertura e precisão.

    Predição em LOTE, não célula a célula: com as 151k referências do EMNIST a
    diferença medida é de 67x (204 ms contra 3,0 ms por célula).

    `corte` é o limiar de confiança acima do qual o caractere é emitido; abaixo
    dele viria `?`. Para o KNN, 0,999 significa "os k vizinhos foram unânimes",
    o único corte que k=3 oferece. Para a CNN é um parâmetro de verdade, e vale
    varrer.
    """
    amostras, alvos = [], []
    for img, r in zip(celulas, rotulos):
        n = norm(img, lado)
        if n is None:
            continue
        amostras.append(n)
        alvos.append(str(r))
    if not amostras:
        return float("nan"), float("nan"), float("nan"), 0, None

    X = np.array(amostras, dtype=np.uint8)
    y = np.array(alvos)
    indices, conf = prever(modelo, X)
    pred = np.array([alfabeto[int(i)] for i in indices])
    acc = float((pred == y).mean())
    emitidos = conf >= corte
    cob = float(emitidos.mean())
    prec = (float((pred[emitidos] == y[emitidos]).mean())
            if emitidos.any() else float("nan"))
    return acc, cob, prec, len(y), (y, pred)


def varrer_corte(modelo, alfabeto: str, celulas, rotulos, prever,
                 cortes=(0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999)) -> None:
    """Curva precisão x cobertura pelo corte de confiança — o `?` do D02.

    Este é o experimento que o KNN não deixa fazer. Com k vizinhos a confiança
    assume k+1 valores, então "varrer o corte" é escolher entre 4 degraus
    (A20). A softmax da CNN é contínua, e aqui o corte finalmente tem onde se
    mover.

    É a **honestidade parcial** do backlog, no nível do caractere: cada linha
    diz o que se ganha em precisão ao trocar cobertura por `?`. A recomendação
    central do TCC — abster em vez de chutar — passa a ter uma curva em vez de
    uma afirmação.
    """
    print(f"\ncorte de confiança (o '?' do D02):")
    print(f"{'corte':>7} {'emitidos':>9} {'cobertura':>10} {'precisão':>9} "
          f"{'erros emitidos':>15}")
    for c in cortes:
        acc, cob, prec, n, par = avaliar(modelo, alfabeto, celulas, rotulos,
                                         normalizar_mnist, 28, prever, c)
        emitidos = int(round(cob * n))
        errados = int(round(emitidos * (1 - prec))) if emitidos else 0
        print(f"{c:>7.3f} {emitidos:>9} {cob:>10.3f} {prec:>9.4f} {errados:>15}")


def confusoes(y: NDArray[np.str_], pred: NDArray[np.str_], n: int = 12) -> None:
    """As trocas mais frequentes. Insumo das regras de correção pós-OCR."""
    from collections import Counter
    erros = Counter((a, b) for a, b in zip(y, pred) if a != b)
    if not erros:
        print("  nenhum erro")
        return
    print(f"  {'troca':<10} {'n':>3}   " * 1)
    for (esperado, obtido), c in erros.most_common(n):
        print(f"  {esperado} -> {obtido:<5} {c:>3}")

    # que classes o modelo emite demais? é a assinatura do viés de frequência
    from collections import Counter as C
    emitidos, esperados = C(pred), C(y)
    excesso = sorted(((emitidos[c] - esperados[c], c) for c in set(pred) | set(y)),
                     reverse=True)[:6]
    print("  emitidas em excesso: "
          + ", ".join(f"{c}{d:+d}" for d, c in excesso if d > 0))


def comparar_pareado(coluna: str, k: int, peso: str, saida: str = "") -> dict:
    """KNN x CNN nas MESMAS células, pareado célula a célula — objetivo (d).

    O módulo já media os dois lados, mas em execuções separadas: cada um
    imprimia sua acurácia e a comparação era feita no olho. Duas acurácias
    agregadas não dizem se a diferença é do modelo ou da amostra — para isso é
    preciso saber QUAIS células mudaram de lado, que é o que McNemar consome. O
    p=0,018 do nome foi calculado à mão numa sessão anterior; aqui a conta passa
    a morar junto do experimento.

    O pareamento exige que os dois vejam exatamente o mesmo conjunto. Ambos
    passam por `normalizar_mnist` a 28 px, que descarta a célula vazia devolvendo
    None — então o subconjunto sobrevivente é o mesmo por construção, e não por
    coincidência. É por isso que a normalização é aplicada UMA vez aqui, e não
    dentro de cada avaliação.

    Nenhum dos dois viu célula real: o KNN treina no MNIST/EMNIST e a CNN
    também. A CNN AFINADA é outra medição (out-of-fold, `afinar_cnn.py`) e não
    entra aqui — misturá-las compararia dois fatores de uma vez.
    """
    os.environ.setdefault("KERAS_BACKEND", "torch")
    import keras
    from treinar_cnn import CAMPOS
    from afinar_cnn import mcnemar

    campo = "nome" if coluna == "nome" else "matricula"
    carregar, alfabeto, caminho = CAMPOS[campo]
    if not os.path.exists(caminho):
        raise SystemExit(f"{caminho} não existe — rode treinar_cnn.py --campo {campo}")

    celulas, rotulos, grupos = carregar_celulas(coluna, com_grupo=True)

    # normalizar uma vez: é o que garante o MESMO conjunto para os dois modelos
    validas = [(i, normalizar_mnist(c, 28)) for i, c in enumerate(celulas)]
    validas = [(i, n) for i, n in validas if n is not None]
    idx = np.array([i for i, _ in validas])
    X = np.array([n for _, n in validas], dtype=np.uint8)
    y = rotulos[idx].astype(str)
    g = grupos[idx].astype(str)
    if len(idx) < len(celulas):
        print(f"  {len(celulas) - len(idx)} células sem traço, fora dos dois lados")

    X_tr, y_tr = carregar()
    modelo_knn, holdout_knn, _ = treinar(X_tr, y_tr, k, peso, 28, norm=normalizar_mnist)
    i_knn, conf_knn = prever_knn(modelo_knn, X)
    pred_knn = np.array([alfabeto[int(i)] for i in i_knn])

    modelo_cnn = keras.saving.load_model(caminho)
    i_cnn, conf_cnn = prever_cnn(modelo_cnn, X)
    pred_cnn = np.array([alfabeto[int(i)] for i in i_cnn])

    ok_knn, ok_cnn = pred_knn == y, pred_cnn == y
    so_knn, so_cnn, p = mcnemar(ok_knn, ok_cnn)

    print(f"\nKNN(k={k}) x CNN, pareado em {len(y)} células de {coluna}")
    print(f"  KNN  {ok_knn.sum():>4}/{len(y)} = {ok_knn.mean():.4f}  (holdout {holdout_knn:.4f})")
    print(f"  CNN  {ok_cnn.sum():>4}/{len(y)} = {ok_cnn.mean():.4f}"
          f"  ({modelo_cnn.count_params():,} parâmetros)")
    print(f"  discordâncias: só KNN {so_knn}, só CNN {so_cnn} — McNemar exato p={p:.4g}")

    # por folha: as folhas têm geometrias diferentes (ver a linhagem das versões),
    # e o agregado esconderia uma vencer e a outra perder
    print(f"\n  {'folha':<12} {'n':>4} {'KNN':>8} {'CNN':>8}")
    for f in sorted(set(s.split("/")[0] for s in g)):
        m = np.array([s.startswith(f) for s in g])
        print(f"  {f:<12} {m.sum():>4} {ok_knn[m].mean():>8.4f} {ok_cnn[m].mean():>8.4f}")

    registros = {
        "coluna": coluna, "k": k, "peso": peso, "n": len(y),
        "knn_acertos": int(ok_knn.sum()), "cnn_acertos": int(ok_cnn.sum()),
        "knn_holdout": holdout_knn,
        "so_knn": so_knn, "so_cnn": so_cnn, "p": p,
        "predicoes": [
            {"grupo": str(gi), "verdade": str(yi), "knn": str(a), "cnn": str(b),
             "conf_knn": float(ck), "conf_cnn": float(cc)}
            for gi, yi, a, b, ck, cc in
            zip(g, y, pred_knn, pred_cnn, conf_knn, conf_cnn)
        ],
    }
    if saida:
        import json
        with open(saida, "w") as fp:
            json.dump(registros, fp, ensure_ascii=False, indent=1)
        print(f"\nregistros crus em {saida} — pontue uma vez, limiarize muitas")
    return registros


def varrer_k(X_img, y, alfabeto: str, celulas, rotulos, peso: str, lado: int = 28,
             ks=(1, 3, 5, 7, 11, 15, 21)):
    """Varre k medindo acurácia E resolução da confiança.

    Pedido por A11 e pela banca (D06: "justificar k=3 ou testar"). Duas colunas
    importam, e a segunda costuma passar batida:

    - `FOLHA acc`  : qualidade bruta da leitura.
    - `níveis`     : quantos valores distintos a confiança pode assumir, que é
                     k+1. Com k=3 são 4, e essa granularidade grossa está na
                     raiz de A11 e A14. A abstenção do D02 precisa de um corte,
                     e um corte precisa de escala para se mover.
    - `precisão`   : acerto entre os que saem unânimes, isto é, o que a
                     abstenção entrega. `ganho abst` é quanto ela soma sobre a
                     acurácia bruta — o preço aparece em `cobertura`.
    """
    print(f"\nvarredura de k (conjunto completo, peso={peso})")
    print(f"{'k':>3} {'holdout':>8} {'FOLHA acc':>10} {'níveis':>7} "
          f"{'cobertura':>10} {'precisão':>9} {'ganho abst':>11}")
    melhor = None
    for k in ks:
        modelo, holdout, _ = treinar(X_img, y, k, peso, lado)
        acc, cob, prec, n, par = avaliar(modelo, alfabeto, celulas, rotulos,
                                         normalizar_mnist, lado)
        print(f"{k:>3} {holdout:>8.4f} {acc:>10.4f} {k + 1:>7} "
              f"{cob:>10.3f} {prec:>9.4f} {prec - acc:>+11.4f}")
        if melhor is None or acc > melhor[0]:
            melhor = (acc, k, par)
    return melhor


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Avalia classificador contra folha real.")
    p.add_argument("--coluna", default="matricula", choices=list(ALVOS))
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--peso", default="uniform", choices=["uniform", "distance"])
    p.add_argument("--recriar", action="store_true", help="reextrair as células das folhas")
    p.add_argument("--folha", default="", help="restringir a uma folha (ex: m2-1). "
                   "Os números do A07 são só da m2-1: --coluna matricula --folha m2-1")
    p.add_argument("--cnn", action="store_true",
                   help="avaliar a CNN treinada em vez de varrer o KNN")
    p.add_argument("--parear", action="store_true",
                   help="KNN x CNN nas mesmas células, com McNemar — objetivo (d)")
    p.add_argument("--saida", default="", help="JSON com as predições cruas do pareamento")
    args = p.parse_args()

    if args.parear:
        saida = args.saida or f"dados/knn_x_cnn_{args.coluna}.json"
        comparar_pareado(args.coluna, args.k, args.peso, saida)
        raise SystemExit

    if args.folha:
        ALVOS[args.coluna] = [(f, m) for f, m in ALVOS[args.coluna]
                              if args.folha in f]
        if not ALVOS[args.coluna]:
            raise SystemExit(f"folha sem gabarito nesta coluna: {args.folha}")
        args.recriar = True     # o cache é da combinação anterior de folhas

    celulas, rotulos = carregar_celulas(args.coluna, args.recriar)
    ocup = np.mean([max(_bounding_box(c).shape) / 64 for c in celulas if _bounding_box(c) is not None])
    print(f"coluna={args.coluna} | células reais com gabarito: {len(celulas)} "
          f"em {len(set(rotulos))} classes | k={args.k} peso={args.peso}")
    print(f"folhas: {', '.join(f for f, _ in ALVOS[args.coluna])}")
    print(f"traço médio: {ocup * 64:.1f}px de altura/largura máxima\n")

    if args.cnn:
        # KNN x CNN no MESMO conjunto de células — objetivo específico (d)
        import os as _os
        _os.environ.setdefault("KERAS_BACKEND", "torch")
        import keras
        from treinar_cnn import CAMPOS

        campo = "nome" if args.coluna == "nome" else "matricula"
        _, alfabeto, caminho = CAMPOS[campo]
        if not _os.path.exists(caminho):
            raise SystemExit(f"{caminho} não existe — rode treinar_cnn.py --campo {campo}")

        modelo = keras.saving.load_model(caminho)
        acc, cob, prec, n, par = avaliar(modelo, alfabeto, celulas, rotulos,
                                         normalizar_mnist, 28, prever_cnn)
        print(f"CNN ({modelo.count_params():,} parâmetros)")
        print(f"  FOLHA acc {acc:.4f} em {n} células")
        print(f"  cobertura {cob:.3f} · precisão {prec:.4f} (corte 0,999)")

        varrer_corte(modelo, alfabeto, celulas, rotulos, prever_cnn)

        print("\ntrocas mais frequentes:")
        confusoes(*par)
        raise SystemExit

    if args.coluna == "matricula":
        # o desenho 2x2 original: fonte de treino x normalização (A07)
        fontes = {"digits.png": carregar_digits_png, "MNIST": carregar_mnist}
        normas = {"ingênua(20)": (normalizar_ingenuo, 20), "MNIST(28)": (normalizar_mnist, 28)}
        print(f"{'fonte':<12} {'normalização':<14} {'holdout':>8} {'FOLHA acc':>10} "
              f"{'cobertura':>10} {'precisão':>9}")
        for nome_f, carregar in fontes.items():
            X_img, y = carregar()
            for nome_n, (norm, lado) in normas.items():
                # par CASADO: a mesma norm no treino e no teste
                modelo, holdout, _ = treinar(X_img, y, args.k, args.peso, lado,
                                             norm=norm)
                acc, cob, prec, n, _ = avaliar(modelo, ALFABETO_DIGITOS, celulas,
                                               rotulos, norm, lado)
                print(f"{nome_f:<12} {nome_n:<14} {holdout:>8.4f} {acc:>10.4f} "
                      f"{cob:>10.3f} {prec:>9.4f}")

        # A11 pediu esta varredura para os dígitos e ela nunca foi feita
        X_img, y = carregar_mnist()
        melhor = varrer_k(X_img, y, ALFABETO_DIGITOS, celulas, rotulos, args.peso)
        print(f"\nmelhor na folha: k={melhor[1]} com {melhor[0]:.4f} "
              f"em {len(celulas)} células")
        print("trocas mais frequentes nessa configuração:")
        confusoes(*melhor[2])
        raise SystemExit

    # ---- coluna de nome ----
    X_img, y = carregar_emnist_maiusculas()
    print(f"EMNIST maiúsculas: {len(y)} amostras\n")

    # 1. tamanho do conjunto. O ByClass é 10,1x desbalanceado; equilibrar testa
    #    se o viés de frequência explica os erros.
    print(f"{'por classe':>10} {'n treino':>9} {'holdout':>8} {'FOLHA acc':>10} "
          f"{'cobertura':>10} {'precisão':>9}")
    for por_classe in (0, 10000, 5000, 2468):
        modelo, holdout, _ = treinar(X_img, y, args.k, args.peso, 28, por_classe)
        acc, cob, prec, n, _ = avaliar(modelo, ALFABETO_MAIUSCULAS, celulas,
                                       rotulos, normalizar_mnist, 28)
        rotulo = "tudo" if por_classe == 0 else str(por_classe)
        print(f"{rotulo:>10} {modelo.n_samples_fit_:>9} {holdout:>8.4f} {acc:>10.4f} "
              f"{cob:>10.3f} {prec:>9.4f}")

    # 2. k, herdado sem justificativa do protótipo anterior
    melhor = varrer_k(X_img, y, ALFABETO_MAIUSCULAS, celulas, rotulos, args.peso)

    print(f"\nmelhor na folha: k={melhor[1]} com {melhor[0]:.4f} em {len(celulas)} células")
    print("trocas mais frequentes nessa configuração:")
    confusoes(*melhor[2])
