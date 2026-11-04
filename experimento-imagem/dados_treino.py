"""Conjuntos de treino e normalização de célula.

Fontes de dígitos:
  - digits.png  : amostra do OpenCV, 5.000 dígitos 20x20. É o que o protótipo
                  anterior usava (apesar do modelo se chamar "mnist_...").
  - MNIST       : 60.000 dígitos 28x28, os arquivos idx originais.

Fonte de letras:
  - EMNIST ByClass : 814.255 amostras em 62 classes, mesmo formato idx. Filtrado
                     aqui para as 26 MAIÚSCULAS (rótulos 10-35).

Por que ByClass e não ByMerge ou Letters: os dois últimos fundem maiúscula com
minúscula na mesma classe, e a folha instrui letra de forma maiúscula (D04). Um
'a' minúsculo no treino não descreve o que a folha produz — só polui a vizinhança
do KNN. ByClass é o único que permite a filtragem pura.

Por que só maiúsculas, e separadas dos dígitos: a matrícula nunca contém letra e
o nome nunca contém dígito, então um classificador por campo elimina de saída as
confusões 0/O, 1/I, 5/S, 2/Z, 8/B. É a restrição de alfabeto aplicada na origem
em vez de corrigida depois.

E duas formas de encaixar a célula recortada no formato do modelo:
  - `normalizar_ingenuo`  : o que o protótipo faz. Centraliza a bounding box
                            numa tela 2*lado e reduz para lado x lado.
  - `normalizar_mnist`    : o procedimento do próprio MNIST. Escala o maior
                            lado da bounding box para 20px preservando a razão
                            de aspecto, e posiciona pelo CENTRO DE MASSA numa
                            tela 28x28.

A segunda existe porque KNN sobre pixels crus é sensível a escala: se o dígito
da folha ocupa uma fração do quadro diferente da que ocupa no treino, a
distância euclidiana compara coisas desalinhadas.
"""

from __future__ import annotations
import gzip
import struct
import cv2
import numpy as np
from numpy.typing import NDArray

CAMINHO_DIGITS = "modelo/digits.png"
CAMINHO_MNIST_IMG = "modelo/train-images-idx3-ubyte.gz"
CAMINHO_MNIST_LBL = "modelo/train-labels-idx1-ubyte.gz"
CAMINHO_EMNIST_IMG = "modelo/emnist-byclass-train-images-idx3-ubyte.gz"
CAMINHO_EMNIST_LBL = "modelo/emnist-byclass-train-labels-idx1-ubyte.gz"

# Mapa de índice de classe -> caractere. Cada fonte devolve rótulos já
# normalizados em 0..n-1, para que o alfabeto seja apenas indexação.
ALFABETO_DIGITOS = "0123456789"
ALFABETO_MAIUSCULAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def carregar_digits_png(caminho: str = CAMINHO_DIGITS) -> tuple[NDArray[np.uint8], NDArray[np.int32]]:
    """Grade 50x100 de dígitos 20x20; 5 linhas por classe, 500 amostras cada."""
    img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(caminho)
    celulas = np.array([np.hsplit(linha, 100) for linha in np.vsplit(img, 50)])
    X = celulas.reshape(-1, 20, 20).astype(np.uint8)
    y = np.repeat(np.arange(10), 500).astype(np.int32)
    return X, y


def _ler_idx(caminho_img: str, caminho_lbl: str) -> tuple[NDArray[np.uint8], NDArray[np.int32]]:
    """Lê um par de arquivos no formato idx do MNIST. EMNIST usa o mesmo formato."""
    with gzip.open(caminho_img, "rb") as f:
        _, n, linhas, colunas = struct.unpack(">IIII", f.read(16))
        X = np.frombuffer(f.read(), dtype=np.uint8).reshape(n, linhas, colunas)
    with gzip.open(caminho_lbl, "rb") as f:
        _, n = struct.unpack(">II", f.read(8))
        y = np.frombuffer(f.read(), dtype=np.uint8).astype(np.int32)
    return X.copy(), y


def carregar_mnist(
    caminho_img: str = CAMINHO_MNIST_IMG, caminho_lbl: str = CAMINHO_MNIST_LBL
) -> tuple[NDArray[np.uint8], NDArray[np.int32]]:
    """Lê os arquivos idx originais do MNIST: 60.000 imagens 28x28."""
    return _ler_idx(caminho_img, caminho_lbl)


def carregar_emnist_maiusculas(
    caminho_img: str = CAMINHO_EMNIST_IMG, caminho_lbl: str = CAMINHO_EMNIST_LBL
) -> tuple[NDArray[np.uint8], NDArray[np.int32]]:
    """EMNIST ByClass filtrado para A-Z, com rótulos remapeados para 0..25.

    Duas armadilhas do dataset, ambas tratadas aqui:

    1. ORIENTAÇÃO. O EMNIST guarda as imagens transpostas em relação ao MNIST —
       o pipeline de digitalização do NIST escreve linha/coluna trocadas. Sem o
       `transpose`, toda letra chega espelhada na diagonal. A correção NÃO é
       assumida da convenção: o bloco `__main__` a verifica medindo a distância
       entre os centroides dos dígitos do EMNIST e os do MNIST, que são a mesma
       coisa e portanto têm que coincidir.

    2. RÓTULOS. ByClass numera 0-9 dígitos, 10-35 maiúsculas, 36-61 minúsculas.
       Subtrair 10 alinha as maiúsculas com ALFABETO_MAIUSCULAS.
    """
    X, y = _ler_idx(caminho_img, caminho_lbl)
    mascara = (y >= 10) & (y <= 35)
    X, y = X[mascara], y[mascara] - 10
    return np.ascontiguousarray(X.transpose(0, 2, 1)), y


def _bounding_box(img: NDArray[np.uint8]) -> NDArray[np.uint8] | None:
    """Recorta a menor caixa que contém pixel aceso. None se a imagem for vazia."""
    if img.size == 0 or img.ndim != 2:
        return None
    linhas = np.where(img.any(axis=1))[0]
    colunas = np.where(img.any(axis=0))[0]
    if linhas.size == 0 or colunas.size == 0:
        return None
    return img[linhas[0]: linhas[-1] + 1, colunas[0]: colunas[-1] + 1]


def normalizar_ingenuo(img: NDArray[np.uint8], lado: int = 20) -> NDArray[np.uint8] | None:
    """O que o protótipo faz: centraliza numa tela 2*lado e reduz."""
    recorte = _bounding_box(img)
    if recorte is None:
        return None
    tela = np.zeros((lado * 2, lado * 2), dtype=np.uint8)
    h, w = recorte.shape
    if h > lado * 2 or w > lado * 2:
        recorte = cv2.resize(recorte, (min(w, lado * 2), min(h, lado * 2)), interpolation=cv2.INTER_AREA)
        h, w = recorte.shape
    y0, x0 = (lado * 2 - h) // 2, (lado * 2 - w) // 2
    tela[y0:y0 + h, x0:x0 + w] = recorte
    return cv2.resize(tela, (lado, lado), interpolation=cv2.INTER_AREA)


def normalizar_lote(X: NDArray[np.uint8], norm=None, lado: int = 28) -> NDArray[np.uint8]:
    """Aplica a MESMA normalização de célula a um conjunto de treino inteiro.

    Existe para fechar uma classe de bug que já custou 22,9 pontos: treinar com a
    imagem crua do dataset e testar com a célula normalizada. O KNN só tem
    distância — se o glifo de treino ocupa 89,5% do quadro e o de teste ocupa
    71,4%, ele compara coisas de tamanhos diferentes.

    O erro passou despercebido nos dígitos porque o MNIST **já nasce** neste
    formato (caixa de 20px, centralizada pelo centro de massa), então reaplicar
    é idempotente: 0,714 antes e depois. O EMNIST foi construído por outro
    processo e ocupa 0,895 do quadro — nele o descasamento é real.

    Amostra que não sobrevive à normalização vira quadro vazio, para preservar o
    alinhamento com os rótulos.
    """
    if norm is None:
        norm = normalizar_mnist
    vazio = np.zeros((lado, lado), dtype=np.uint8)
    return np.stack([(a if a is not None else vazio)
                     for a in (norm(x, lado) for x in X)])


def normalizar_mnist(img: NDArray[np.uint8], lado: int = 28, caixa: int = 20) -> NDArray[np.uint8] | None:
    """O procedimento do MNIST: escala para caixa 20x20 preservando aspecto,
    centraliza pelo centro de massa numa tela 28x28."""
    recorte = _bounding_box(img)
    if recorte is None:
        return None

    h, w = recorte.shape
    escala = caixa / max(h, w)
    novo_h, novo_w = max(1, int(round(h * escala))), max(1, int(round(w * escala)))
    recorte = cv2.resize(recorte, (novo_w, novo_h), interpolation=cv2.INTER_AREA)

    tela = np.zeros((lado, lado), dtype=np.uint8)
    y0, x0 = (lado - novo_h) // 2, (lado - novo_w) // 2
    tela[y0:y0 + novo_h, x0:x0 + novo_w] = recorte

    # deslocar para que o centro de massa caia no centro da tela
    total = tela.sum()
    if total > 0:
        ys, xs = np.mgrid[0:lado, 0:lado]
        cy, cx = (ys * tela).sum() / total, (xs * tela).sum() / total
        dy, dx = int(round(lado / 2 - cy)), int(round(lado / 2 - cx))
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        tela = cv2.warpAffine(tela, M, (lado, lado), flags=cv2.INTER_NEAREST, borderValue=0)
    return tela


if __name__ == "__main__":
    Xd, yd = carregar_digits_png()
    Xm, ym = carregar_mnist()
    print(f"digits.png: {Xd.shape} rótulos {np.bincount(yd)}")
    print(f"MNIST     : {Xm.shape} rótulos {np.bincount(ym)}")

    # quanto do quadro o dígito ocupa em cada fonte? é a hipótese da escala.
    def ocupacao(X: NDArray[np.uint8]) -> float:
        fracoes = []
        for img in X[:500]:
            bb = _bounding_box(img)
            if bb is not None:
                fracoes.append(max(bb.shape) / img.shape[0])
        return float(np.mean(fracoes))

    print(f"\nfração do quadro ocupada pelo dígito (média de 500):")
    print(f"  digits.png: {ocupacao(Xd):.3f}")
    print(f"  MNIST     : {ocupacao(Xm):.3f}")

    # --- EMNIST: verificar a orientação em vez de confiar na convenção -------
    import os
    if not os.path.exists(CAMINHO_EMNIST_IMG):
        print(f"\nEMNIST ausente em {CAMINHO_EMNIST_IMG} — nada a verificar.")
        raise SystemExit

    Xr, yr = _ler_idx(CAMINHO_EMNIST_IMG, CAMINHO_EMNIST_LBL)
    print(f"\nEMNIST ByClass: {Xr.shape} em {len(np.unique(yr))} classes")
    print(f"  dígitos {(yr <= 9).sum()} | maiúsculas {((yr >= 10) & (yr <= 35)).sum()}"
          f" | minúsculas {(yr >= 36).sum()}")

    # Os dígitos do EMNIST e os do MNIST vêm da MESMA base do NIST, então os
    # centroides por classe têm que coincidir. Se a transposição for necessária,
    # ela aparece como uma queda grande na distância entre centroides.
    amostra = yr <= 9
    Xe, ye = Xr[amostra][:40000], yr[amostra][:40000]
    cent_mnist = np.stack([Xm[ym == c].mean(axis=0) for c in range(10)])

    print("\n  distância média entre centroides EMNIST e MNIST, por orientação:")
    resultados = {}
    for rotulo, Xv in (("como veio  ", Xe), ("transposto ", Xe.transpose(0, 2, 1))):
        cent = np.stack([Xv[ye == c].mean(axis=0) for c in range(10)])
        d = float(np.linalg.norm(cent - cent_mnist, axis=(1, 2)).mean())
        resultados[rotulo.strip()] = d
        print(f"    {rotulo} {d:>8.1f}")

    vencedor = min(resultados, key=resultados.get)
    razao = max(resultados.values()) / min(resultados.values())
    print(f"  -> '{vencedor}' vence por {razao:.2f}x."
          f" `carregar_emnist_maiusculas` aplica transpose: "
          f"{'COERENTE' if vencedor == 'transposto' else 'INCOERENTE, revisar!'}")

    # mosaico para conferência visual: 26 maiúsculas, uma por classe
    Xu, yu = carregar_emnist_maiusculas()
    tira = np.hstack([Xu[yu == c][0] for c in range(26)])
    cv2.imwrite("modelo/emnist_alfabeto.png", tira)
    print(f"\n  maiúsculas carregadas: {Xu.shape}, rótulos {yu.min()}..{yu.max()}")
    print("  mosaico A-Z salvo em modelo/emnist_alfabeto.png — confira a olho.")
