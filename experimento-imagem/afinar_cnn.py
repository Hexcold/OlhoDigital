"""Item 0c-2: a lacuna holdout -> folha e falta de domínio, não de capacidade?

A CNN faz **0,986 no holdout do EMNIST** e **0,849 na folha**. Treze pontos e
sete décimos de diferença. Duas explicações concorrem:

  capacidade  a rede não dá conta do problema, e precisaria ser maior
  domínio     a rede dá conta, mas o EMNIST não se parece com a folha

Se for domínio, um punhado de células reais deve fechar boa parte da lacuna —
porque a informação que falta é sobre a distribuição, não sobre a tarefa. É o
que este módulo mede: parte da CNN já treinada no EMNIST e a **afina** nas
células reais.

## O problema metodológico, que é o experimento inteiro

Treinar e avaliar nas mesmas 304 células daria um número lindo e falso. Então
validação cruzada — mas *cruzada como?*

  por CÉLULA   as células de uma mesma pessoa caem nos dois lados. Responde
               "a rede consegue aprender esta caligrafia?"
  por LINHA    cada linha da folha foi preenchida por uma PESSOA diferente,
               então agrupar por linha é agrupar por escritor. Responde
               "isto transfere para alguém que a rede nunca viu?"
  por FOLHA    treina em duas folhas e prediz a terceira. Responde "isto
               transfere para uma CAPTURA que a rede nunca viu?"

A segunda é a pergunta que o projeto tinha quando só havia a m3_2. A terceira é
a que ele tem agora — na coleta dos Encontros não só ninguém terá escrito
antes, como a folha, a impressão e a digitalização serão outras. A diferença
entre célula e escritor é **a medida do vazamento de estilo**; a diferença
entre escritor e folha é a medida do vazamento de **captura**.

## Por que o esquema por folha teve que existir (28→30/08)

A35 concluiu que "o ganho sobrevive a folha nova" porque o conjunto passou a
ter três folhas de geometrias diferentes e o ganho continuou. Não segue: com
GroupKFold sobre ~45 escritores das três folhas, **todo fold de treino contém
células da mesma folha que o de teste**. O que aquilo mede é que afinar numa
mistura de três domínios ajuda *nesses três domínios* — não que transfira para
um quarto. `LeaveOneGroupOut` sobre a folha é o teste direto, e é o que decide
se o afinamento entra no pipeline: as 500 folhas dos Encontros são, todas,
folha nunca vista.

## O que este experimento ainda NÃO pode dizer

Três folhas dão três folds, e cada fold treina em ~2/3 das células. Um
resultado negativo aqui é ambíguo entre "não transfere" e "faltou dado" — o
conjunto de treino do LOSO é menor que o dos outros dois esquemas. Um
resultado positivo, esse sim, é limpo.

## Comparação

Cada célula recebe exatamente UMA predição out-of-fold, de um modelo que nunca
a viu. Isso torna o agregado diretamente comparável à CNN base nas mesmas 304
células, e pareável célula a célula — McNemar, que é o teste para amostras
pareadas, o mesmo usado em KNN x CNN.

Uso:
    python afinar_cnn.py                                  # os três esquemas
    python afinar_cnn.py --campo nome --esquema folha     # só o LOSO
    python afinar_cnn.py --campo nome --esquema folha --estrategia inteira \
        --salvar-modelos --final     # os held-out (para medir) e o de produção
"""

from __future__ import annotations

# o backend tem que ser escolhido ANTES de importar keras (ver treinar_cnn)
import os
os.environ.setdefault("KERAS_BACKEND", "torch")

import argparse
import json
import time
from math import comb

import numpy as np

from avaliar import carregar_celulas
from dados_treino import normalizar_lote, ALFABETO_MAIUSCULAS, ALFABETO_DIGITOS
from treinar_cnn import LADO

# Um campo, um classificador (D08) — e portanto um modelo base e um alfabeto
# por campo. Estava tudo preso ao nome, porque a matrícula não tinha CNN até
# 27/08; agora tem, e a lacuna dela (holdout 0,9937 x folha 0,8963 = 9,7 pontos)
# é o mesmo alvo que A28 atacou nas letras.
CAMPOS = {
    "nome": ("modelo/cnn_emnist_maiusculas_28x28.keras", ALFABETO_MAIUSCULAS),
    "matricula": ("modelo/cnn_mnist_28x28.keras", ALFABETO_DIGITOS),
}
SAIDA = "dados/afinamento_cnn_{campo}.json"
# Saída separada quando se roda um subconjunto dos esquemas: o JSON de A35 é a
# fonte da tabela publicada, e sobrescrevê-lo com metade das configurações
# apagaria a evidência dela em silêncio.
SAIDA_PARCIAL = "dados/afinamento_cnn_{campo}_{recorte}.json"
ESQUEMAS = ("celula", "escritor", "folha")
SEMENTE = 42

# Dois artefatos, e a diferença entre eles é o que separa medir de operar:
#   HELDOUT  um por folha, treinado nas OUTRAS. É o que dá número honesto no
#            ponta a ponta — cada folha é lida por um modelo que não a viu.
#   FINAL    treinado em todas as células reais. É o que vai para a coleta,
#            onde a folha é inédita por construção e não há o que contaminar.
# Usar o FINAL para medir nas folhas de dev seria avaliar no treino; usar um
# HELDOUT na coleta seria jogar fora um terço do dado sem motivo.
MODELO_HELDOUT = "modelo/cnn_{campo}_afinada_{estrategia}_sem_{folha}.keras"
MODELO_FINAL = "modelo/cnn_{campo}_afinada_{estrategia}.keras"

# Épocas fixadas ANTES de olhar resultado. As curvas por época saem no relato
# como sensibilidade, mas o número de manchete é este — escolher a época pelo
# desempenho no fold de teste seria seleção no conjunto de avaliação.
EPOCAS = 20
BATCH = 16


def preparar_celulas(X: np.ndarray) -> np.ndarray:
    """O MESMO caminho que a célula percorre em `OCR.preparar_amostra` (A19).

    Foi esquecer isto que custou 22,9 pontos ao KNN. A CNN é menos sensível a
    escala, mas o casamento de domínio não é opcional em lugar nenhum.
    """
    X = normalizar_lote(X, lado=LADO)
    return (X.astype("float32") / 255.0)[..., None]


def indices_por_classe(y: np.ndarray, campo: str = "nome"
                       ) -> tuple[np.ndarray, list[str]]:
    """Rótulo-caractere -> índice no alfabeto do campo."""
    alfabeto = list(CAMPOS[campo][1])
    idx = np.array([alfabeto.index(c) for c in y])
    return idx, alfabeto


def carregar_base_pristina(campo: str = "nome"):
    """Recarrega do disco a cada fold: `fit` muda os pesos in-place, e reusar o
    mesmo objeto faria o fold 2 partir de onde o fold 1 parou — vazamento de
    todos contra todos, disfarçado de validação cruzada."""
    from keras import models
    return models.load_model(CAMPOS[campo][0])


def afinar(modelo, X_tr, y_tr, estrategia: str, epocas: int, semente: int):
    """Ajusta a rede às células reais, por uma de duas estratégias.

    `cabeca`  congela as convoluções e retreina só as densas. Com ~240 amostras
              é a aposta conservadora: menos parâmetros livres, menos decoreba.
    `inteira` libera tudo com lr 10x menor que o do treino original. Pode ir
              mais longe se a diferença de domínio estiver nas features de
              baixo nível — traço mais grosso, borda de célula — e não só na
              fronteira de decisão.
    """
    from keras import optimizers
    import keras

    keras.utils.set_random_seed(semente)

    if estrategia == "cabeca":
        for camada in modelo.layers:
            camada.trainable = "dense" in camada.name.lower()
        lr = 1e-3
    else:
        for camada in modelo.layers:
            camada.trainable = True
        lr = 1e-4

    modelo.compile(optimizer=optimizers.Adam(learning_rate=lr),
                   loss="sparse_categorical_crossentropy",
                   metrics=["accuracy"])
    modelo.fit(X_tr, y_tr, epochs=epocas, batch_size=BATCH, verbose=0)
    return modelo


def folds_por_celula(y: np.ndarray, n: int, semente: int):
    from sklearn.model_selection import StratifiedKFold
    return StratifiedKFold(n_splits=n, shuffle=True,
                           random_state=semente).split(np.zeros(len(y)), y)


def folds_por_escritor(y: np.ndarray, g: np.ndarray, n: int, semente: int):
    """GroupKFold embaralhado: nenhum escritor aparece nos dois lados."""
    from sklearn.model_selection import GroupKFold
    return GroupKFold(n_splits=n, shuffle=True,
                      random_state=semente).split(np.zeros(len(y)), y, groups=g)


def folha_de(g: np.ndarray) -> np.ndarray:
    """`"m3_2.png/07"` -> `"m3_2.png"`. A procedência é `folha/linha` (avaliar)."""
    return np.array([k.split("/")[0] for k in g])


def folds_por_folha(y: np.ndarray, g: np.ndarray):
    """Leave-one-sheet-out: uma folha inteira de fora por vez.

    Não recebe `n` — o número de folds é o número de folhas, e não é escolha.
    Pedir 5 folds sobre 3 folhas ou daria erro ou juntaria folhas num fold, que
    é justamente o vazamento que este esquema existe para eliminar.
    """
    from sklearn.model_selection import LeaveOneGroupOut
    return LeaveOneGroupOut().split(np.zeros(len(y)), y, groups=folha_de(g))


def mcnemar(a: np.ndarray, b: np.ndarray) -> tuple[int, int, float]:
    """Teste exato sobre as discordâncias — o certo para amostras pareadas."""
    so_a = int((a & ~b).sum())
    so_b = int((~a & b).sum())
    n, k = so_a + so_b, min(so_a, so_b)
    if n == 0:
        return so_a, so_b, 1.0
    p = 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return so_a, so_b, min(p, 1.0)


def rodar(esquema: str, estrategia: str, X: np.ndarray, y_idx: np.ndarray,
          g: np.ndarray, n_folds: int, epocas: int, campo: str = "nome",
          ok_base: np.ndarray | None = None,
          salvar: str = "") -> tuple[np.ndarray, list[dict]]:
    """Uma predição out-of-fold por célula. Devolve as predições e os folds.

    O detalhe por fold só interessa de verdade no esquema por folha, onde cada
    fold É uma folha: o agregado pode subir com o afinamento pagando em duas
    capturas e custando na terceira, e é a terceira que se pareceria com os
    Encontros.
    """
    pred = np.full(len(y_idx), -1, dtype=int)
    if esquema == "celula":
        gerador = folds_por_celula(y_idx, n_folds, SEMENTE)
    elif esquema == "escritor":
        gerador = folds_por_escritor(y_idx, g, n_folds, SEMENTE)
    else:
        gerador = folds_por_folha(y_idx, g)

    folhas = folha_de(g)
    folds = []
    for i, (tr, te) in enumerate(gerador):
        modelo = carregar_base_pristina(campo)
        # semente por fold, contador inteiro — nunca derivada de hash()
        modelo = afinar(modelo, X[tr], y_idx[tr], estrategia, epocas,
                        SEMENTE + i)
        pred[te] = modelo.predict(X[te], verbose=0).argmax(axis=1)
        ok = pred[te] == y_idx[te]
        if salvar:
            # o modelo do fold é o único que pode ler esta folha sem tê-la
            # visto: é ele que o ponta a ponta precisa para dar um número
            # limpo, e ele existia só dentro deste laço
            fora = os.path.splitext(sorted(set(folhas[te]))[0])[0]
            caminho = salvar.format(campo=campo, estrategia=estrategia,
                                    folha=fora)
            modelo.save(caminho)
            print(f"     modelo do fold em {caminho}")
        folds.append({
            "fold": i, "n_treino": len(tr), "n_teste": len(te),
            "teste": sorted(set(folhas[te])), "treino": sorted(set(folhas[tr])),
            # classes vistas no treino: com uma folha inteira de fora, uma letra
            # rara pode sumir do treino, e aí o fold pune o afinamento por falta
            # de dado, não por falta de transferência
            "classes_treino": len(set(y_idx[tr].tolist())),
            "classes_teste": len(set(y_idx[te].tolist())),
            "acertos": int(ok.sum()),
            "base_acertos": (None if ok_base is None
                             else int(ok_base[te].sum())),
        })
    assert (pred >= 0).all(), "toda célula tem que receber predição out-of-fold"
    return pred, folds


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Item 0c-2: afinar a CNN nas células reais.")
    p.add_argument("--campo", default="nome", choices=list(CAMPOS))
    p.add_argument("--epocas", type=int, default=EPOCAS)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--esquema", nargs="+", default=list(ESQUEMAS),
                   choices=list(ESQUEMAS),
                   help="folha = leave-one-sheet-out (folds = nº de folhas)")
    p.add_argument("--salvar-modelos", action="store_true",
                   help="grava o modelo de cada fold do esquema por folha")
    p.add_argument("--final", action="store_true",
                   help="treina em TODAS as células reais e grava o modelo de "
                        "produção (não é avaliável nas folhas de dev)")
    p.add_argument("--estrategia", nargs="+", default=["cabeca", "inteira"],
                   choices=["cabeca", "inteira"])
    p.add_argument("--saida", default="")
    args = p.parse_args()
    esquemas = [e for e in ESQUEMAS if e in args.esquema]   # ordem canônica
    estrategias = [e for e in ("cabeca", "inteira") if e in args.estrategia]
    # O nome do arquivo tem que descrever o que está dentro dele nas DUAS
    # dimensões: rodar um esquema a menos e rodar uma estratégia a menos
    # produzem JSONs igualmente parciais, e foi por o nome ignorar a segunda
    # que uma corrida com `--estrategia inteira` apagou o registro da cabeça.
    recorte = "_".join(esquemas + ([] if len(estrategias) == 2 else estrategias))
    saida = args.saida or (
        SAIDA.format(campo=args.campo)
        if esquemas == list(ESQUEMAS[:2]) and len(estrategias) == 2
        else SAIDA_PARCIAL.format(campo=args.campo, recorte=recorte))

    X_cru, y, g = carregar_celulas(args.campo, com_grupo=True)
    y_idx, alfabeto = indices_por_classe(y, args.campo)
    X = preparar_celulas(X_cru)
    print(f"{len(X)} células · {len(set(g))} escritores · "
          f"{len(set(y))} classes presentes de {len(alfabeto)}")
    print(f"folhas: {sorted({k.split('/')[0] for k in g})}")

    # linha de base: a CNN como ela está hoje, nas mesmas células
    base = carregar_base_pristina(args.campo)
    pred_base = base.predict(X, verbose=0).argmax(axis=1)
    ok_base = pred_base == y_idx
    print(f"\nCNN base (EMNIST, sem afinar): {ok_base.sum()}/{len(X)} = "
          f"{ok_base.mean():.3f}")

    resultados = []
    for esquema in esquemas:
        for estrategia in estrategias:
            t0 = time.perf_counter()
            salvar = (MODELO_HELDOUT if args.salvar_modelos
                      and esquema == "folha" else "")
            pred, folds = rodar(esquema, estrategia, X, y_idx, g, args.folds,
                                args.epocas, args.campo, ok_base, salvar)
            ok = pred == y_idx
            so_af, so_base, pval = mcnemar(ok, ok_base)
            dt = time.perf_counter() - t0
            print(f"\nafinada · CV por {esquema:<8} · {estrategia:<8} "
                  f"{ok.sum()}/{len(X)} = {ok.mean():.3f}   "
                  f"({ok.mean() - ok_base.mean():+.3f})")
            print(f"   McNemar: só afinada {so_af} · só base {so_base} · "
                  f"p = {pval:.4f}   ({dt:.0f} s)")
            if esquema == "folha":
                for d in folds:
                    print(f"     fora: {d['teste'][0]:<12} "
                          f"{d['acertos']:>3}/{d['n_teste']:<3} = "
                          f"{d['acertos'] / d['n_teste']:.3f}  "
                          f"(base {d['base_acertos'] / d['n_teste']:.3f}, "
                          f"{d['base_acertos']:>3}/{d['n_teste']})  "
                          f"treino {d['n_treino']} céls · "
                          f"{d['classes_treino']} classes")
            resultados.append({
                "esquema": esquema, "estrategia": estrategia,
                "epocas": args.epocas,
                "folds": len(folds), "detalhe_folds": folds,
                "acertos": int(ok.sum()), "n": len(X),
                "acuracia": float(ok.mean()),
                "base_acertos": int(ok_base.sum()),
                "base_acuracia": float(ok_base.mean()),
                "mcnemar_so_afinada": so_af, "mcnemar_so_base": so_base,
                "p": pval,
                # predição POR CÉLULA, para a análise dos erros que sobram.
                # O agregado diz que a lacuna caiu 6 pontos; só o caso a caso diz
                # se os ~8 restantes são recuperáveis ou entrada ilegível.
                "predicoes": [
                    {"grupo": str(gr), "verdade": alfabeto[vi],
                     "predito": alfabeto[pi], "base": alfabeto[bi]}
                    for gr, vi, pi, bi in zip(g, y_idx, pred, pred_base)
                ],
            })

    if args.final:
        for estrategia in estrategias:
            modelo = carregar_base_pristina(args.campo)
            modelo = afinar(modelo, X, y_idx, estrategia, args.epocas, SEMENTE)
            caminho = MODELO_FINAL.format(campo=args.campo,
                                          estrategia=estrategia)
            modelo.save(caminho)
            print(f"\nmodelo final ({estrategia}, {len(X)} células) em {caminho}"
                  f"\n  ATENÇÃO: viu todas as células de dev. Qualquer acurácia "
                  "medida nelas com ESTE modelo é\n  medida no treino — o número "
                  "publicável sai dos held-out acima.")

    os.makedirs("dados", exist_ok=True)
    with open(saida, "w") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    print(f"\n{len(resultados)} configurações em {saida}")
    n_folhas = len(set(folha_de(g).tolist()))
    print(f"ressalva: {len(X)} células de {n_folhas} folhas, todas com verdade "
          "de fonte externa (ver ALVOS).")
    if "folha" in esquemas:
        print(f"o esquema por folha treina em {n_folhas - 1} folhas e prediz a "
              "que ficou de fora: aí sim é transferência para captura\nnova, "
              "com conjunto de treino menor que o dos outros esquemas.")
    else:
        print("célula e escritor mantêm constante tudo que é da captura — sem "
              "o esquema por folha,\nisto não mede transferência para folha nova.")
