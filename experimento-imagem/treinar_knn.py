"""Treina um classificador KNN por CAMPO e salva o modelo.

Substitui `cv2.ml.KNearest` do protótipo anterior, removido no OpenCV 5. O
algoritmo é o que a Seção 4.5 do TCC fixa — KNN, k=3 por padrão; mudou apenas
a biblioteca que o hospeda.

São dois modelos, não um de 36 classes: a matrícula só tem dígitos e o nome só
tem letras, então cada campo tem seu alfabeto (ver o cabeçalho de `ocr.py`).

  matricula -> MNIST, 10 classes. Escolhido por medição, não preferência: contra
               o gabarito da m2-1 a combinação anterior (digits.png +
               normalização ingênua 20x20) dá 0,410 e esta dá 0,885 (A07).
  nome      -> EMNIST ByClass filtrado para maiúsculas, 26 classes. Medido pela
               primeira vez em 22/08/2026: 0,543 na m3_2, contra 0,788 dos
               dígitos na MESMA folha. Ver A17 no vault — é o gargalo aberto.

O custo do KNN é de PREDIÇÃO, não de treino — ele só guarda o conjunto. Com
~208k maiúsculas, cada célula é comparada contra 166k vetores de 784 dimensões,
e a folha tem 450 células de nome. Por isso `--por-classe` existe: permite medir
acurácia contra custo antes de decidir o tamanho do conjunto.
"""

import argparse
import datetime as dt
import numpy as np
from sklearn.neighbors import KNeighborsClassifier
from sklearn.model_selection import train_test_split
import joblib

from dados_treino import (
    carregar_digits_png, carregar_mnist, carregar_emnist_maiusculas,
    normalizar_lote, ALFABETO_DIGITOS, ALFABETO_MAIUSCULAS,
)

LADO = 28
SEMENTE = 42

# fonte -> (carregador, alfabeto, caminho do modelo treinado)
FONTES = {
    "mnist":  (carregar_mnist, ALFABETO_DIGITOS, "modelo/knn_mnist_28x28.joblib"),
    "digits": (carregar_digits_png, ALFABETO_DIGITOS, "modelo/knn_digits_20x20.joblib"),
    "emnist": (carregar_emnist_maiusculas, ALFABETO_MAIUSCULAS,
               "modelo/knn_emnist_maiusculas_28x28.joblib"),
}


def subamostrar(X: np.ndarray, y: np.ndarray, por_classe: int,
                semente: int = SEMENTE) -> tuple[np.ndarray, np.ndarray]:
    """Corta o conjunto para no máximo `por_classe` amostras de cada rótulo.

    Serve ao orçamento do KNN, cujo custo de predição é linear no tamanho do
    conjunto. Também equilibra o ByClass, que é bem desbalanceado — 'O' e 'S'
    têm ordens de magnitude mais amostras que 'Q' ou 'X'.
    """
    rng = np.random.default_rng(semente)
    escolhidos = []
    for c in np.unique(y):
        idx = np.flatnonzero(y == c)
        if len(idx) > por_classe:
            idx = rng.choice(idx, por_classe, replace=False)
        escolhidos.append(idx)
    idx = np.sort(np.concatenate(escolhidos))
    return X[idx], y[idx]


def treinar(fonte: str = "mnist", k: int = 3, peso: str = "uniform",
            semente: int = SEMENTE, por_classe: int = 0
            ) -> tuple[KNeighborsClassifier, float, int]:
    """Treina com split 80/20 (Seção 4.5.1). Devolve modelo, acurácia e n de teste.

    A acurácia é de holdout no próprio dataset — e mede mal o desempenho real.
    Ver a ressalva em `avaliar.py`: holdout 0,936 correspondeu a 0,416 na folha.
    """
    carregar, _, _ = FONTES[fonte]
    X_img, y = carregar()
    if por_classe:
        X_img, y = subamostrar(X_img, y, por_classe, semente)

    # O treino passa pela MESMA normalização que a célula da folha percorre em
    # `OCR.preparar_amostra`. Não é detalhe: treinar com a imagem crua do EMNIST
    # e testar com a célula normalizada custou 22,9 pontos (0,543 -> 0,773),
    # porque o glifo do EMNIST ocupa 0,895 do quadro e o da folha 0,714.
    # No MNIST é idempotente — ele já nasce neste formato —, e foi por isso que
    # o erro não apareceu nos dígitos.
    X_img = normalizar_lote(X_img, lado=LADO)
    X = X_img.reshape(len(X_img), -1).astype(np.float32)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=semente, stratify=y
    )
    modelo = KNeighborsClassifier(n_neighbors=k, weights=peso).fit(X_tr, y_tr)

    n = min(2000, len(y_te))
    return modelo, float(modelo.score(X_te[:n], y_te[:n])), n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Treina o KNN de um campo.")
    parser.add_argument("--fonte", default="mnist", choices=list(FONTES))
    parser.add_argument("-k", type=int, default=3, help="vizinhos (padrão 3, como no TCC)")
    parser.add_argument("--peso", default="uniform", choices=["uniform", "distance"])
    parser.add_argument("--por-classe", type=int, default=0,
                        help="teto de amostras por classe (0 = tudo). Orçamento do KNN.")
    parser.add_argument("--varrer", action="store_true", help="varre k: acurácia x degraus de confiança")
    args = parser.parse_args()

    carregar, alfabeto, caminho_modelo = FONTES[args.fonte]

    if args.varrer:
        X_img, y = carregar()
        if args.por_classe:
            X_img, y = subamostrar(X_img, y, args.por_classe)
        if X_img.shape[1] != LADO:
            import cv2
            X_img = np.array([cv2.resize(a, (LADO, LADO), interpolation=cv2.INTER_AREA) for a in X_img])
        X = X_img.reshape(len(X_img), -1).astype(np.float32)
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=SEMENTE, stratify=y)
        n = min(2000, len(y_te))
        print(f"fonte={args.fonte} peso={args.peso} | treino {len(y_tr)} | teste {n}\n")
        print(f"{'k':>3} {'holdout':>9} {'níveis de confiança':>20}")
        for k in (1, 3, 5, 7, 10, 15):
            m = KNeighborsClassifier(n_neighbors=k, weights=args.peso).fit(X_tr, y_tr)
            acc = m.score(X_te[:n], y_te[:n])
            niveis = len(np.unique(np.round(m.predict_proba(X_te[:n]).max(axis=1), 6)))
            print(f"{k:>3} {acc:>9.4f} {niveis:>20}")
        raise SystemExit

    t0 = dt.datetime.now()
    modelo, acuracia, n_teste = treinar(args.fonte, args.k, args.peso,
                                        por_classe=args.por_classe)
    joblib.dump(modelo, caminho_modelo)

    # O custo do KNN é de predição. Medir uma vez aqui evita descobrir na folha.
    amostra = np.zeros((1, LADO * LADO), dtype=np.float32)
    t1 = dt.datetime.now()
    for _ in range(20):
        modelo.predict(amostra)
    ms = (dt.datetime.now() - t1).total_seconds() / 20 * 1000

    n_ref = modelo.n_samples_fit_
    print(f"fonte={args.fonte} k={args.k} peso={args.peso} alfabeto={len(alfabeto)} classes"
          + (f" por_classe={args.por_classe}" if args.por_classe else ""))
    print(f"referências guardadas: {n_ref}")
    print(f"holdout {acuracia:.4f} em {n_teste} amostras  ({dt.datetime.now() - t0} no total)")
    print(f"predição: {ms:.1f} ms/célula -> {ms * 450 / 1000:.1f} s por folha de 450 células")
    print(f"modelo salvo em {caminho_modelo}")
    print("\nressalva: holdout mede mal o desempenho real. Confira com avaliar.py.")
