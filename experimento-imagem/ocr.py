"""Reconhecimento de um caractere por célula, via KNN.

Porte do `ocr.py` do protótipo anterior, que usava `cv2.ml.KNearest` — módulo
removido no OpenCV 5. O algoritmo continua sendo KNN com k=3 (Seção 4.5 do TCC);
mudou a biblioteca que o hospeda.

Ganho da troca: além do rótulo, o sklearn expõe a fração de votos entre os k
vizinhos (`predict_proba`) e as distâncias brutas (`kneighbors`). São os dois
candidatos a sinal de confiança para a abstenção — que ainda NÃO está
implementada aqui: esta etapa continua chutando, como o protótipo original.
Emitir '?' é o M1.

UM CLASSIFICADOR POR CAMPO. A matrícula nunca contém letra e o nome nunca contém
dígito, então cada campo carrega seu próprio modelo e seu próprio alfabeto. Um
classificador único de 36 classes admitiria as confusões 0/O, 1/I, 5/S, 2/Z e
8/B que aqui são impossíveis por construção — é a restrição de alfabeto aplicada
na origem, e não corrigida depois. Use `OCR.de_digitos()` e `OCR.de_letras()`.
"""

from __future__ import annotations
from dataclasses import dataclass
import argparse
import numpy as np
import joblib

from image import Image
from border import Border
from filter_abc import ImageFilter
from dados_treino import normalizar_mnist, ALFABETO_DIGITOS, ALFABETO_MAIUSCULAS

CAMINHO_MODELO = "modelo/knn_mnist_28x28.joblib"          # dígitos, 10 classes
CAMINHO_MODELO_LETRAS = "modelo/knn_emnist_maiusculas_28x28.joblib"
CAMINHO_MODELO_CNN_LETRAS = "modelo/cnn_emnist_maiusculas_28x28.keras"
CAMINHO_MODELO_CNN_DIGITOS = "modelo/cnn_mnist_28x28.keras"
LADO_MODELO = 28


@dataclass
class Predicao:
    """Resultado de uma célula. `confianca` e `margem` alimentam o M1."""
    caractere: str        # já mapeado pelo alfabeto do campo, não é índice de classe
    confianca: float      # KNN: fração dos k votos · CNN: probabilidade da softmax
    distancia: float      # KNN: distância ao vizinho mais próximo · CNN: 0
    imagem: Image
    # Quanto o vencedor sobra sobre o segundo colocado. É o teste de razão de
    # Lowe no nível do CARACTERE — o mesmo raciocínio do δ do D03 um nível
    # abaixo, e o que A11 pediu ao notar que a distância exposta é unária
    # ("quão parecido?") quando o que separa acerto de erro é relacional
    # ("exclui as outras classes?").
    margem: float = 0.0

    def __str__(self) -> str:
        return self.caractere


class OCR:
    def __init__(self, caminho_modelo: str = CAMINHO_MODELO,
                 alfabeto: str = ALFABETO_DIGITOS, lado: int = LADO_MODELO):
        self.modelo = joblib.load(caminho_modelo)
        self.alfabeto = alfabeto
        self.lado = lado
        self.debug: bool = False

    @classmethod
    def de_digitos(cls) -> OCR:
        """Classificador da coluna de MATRÍCULA: 10 classes, MNIST."""
        return cls(CAMINHO_MODELO, ALFABETO_DIGITOS)

    @classmethod
    def de_letras(cls) -> OCR:
        """Classificador da coluna de NOME: 26 classes, EMNIST ByClass maiúsculas."""
        return cls(CAMINHO_MODELO_LETRAS, ALFABETO_MAIUSCULAS)

    def set_debug(self, debug: bool) -> OCR:
        self.debug = debug
        return self

    @property
    def k(self) -> int:
        return int(self.modelo.n_neighbors)

    def preparar_amostra(self, img: Image) -> tuple[np.ndarray, Image] | None:
        """Encaixa a célula no formato do treino pelo procedimento do MNIST.

        Substitui o `resize` direto do protótipo, que dependia de a chamadora ter
        feito bounding box e padding antes (`normalizar_ingenuo`). A troca não é
        cosmética: contra o gabarito da m2-1 ela vale 32/78 → 69/78 (0,410 →
        0,885). Ver A07 no vault. Reproduzir com:
            python avaliar.py --coluna matricula --folha m2-1

        Nota: o A07 registrou 32/77 e 69/77. Os acertos são os mesmos; o
        denominador subiu de 77 para 78 quando o cache foi recriado com o
        código atual — o `.npz` antigo vinha de uma versão anterior e nunca
        havia sido regerado. A conclusão não muda.

        Devolve None se não sobrou pixel aceso na célula.
        """
        normalizada = normalizar_mnist(img.data, self.lado)
        if normalizada is None:
            return None
        pronta = Image(img).set_data(normalizada)
        return pronta.get_sample(self.lado * self.lado), pronta

    def prever_de_imagem_filtrada(self, filtrada: ImageFilter) -> Predicao | None:
        preparada = self.preparar_amostra(filtrada.get_image())
        if preparada is None:
            return None
        amostra, img = preparada

        indice = int(self.modelo.predict(amostra)[0])
        caractere = self.alfabeto[indice]
        confianca = float(self.modelo.predict_proba(amostra).max())
        distancias, _ = self.modelo.kneighbors(amostra, n_neighbors=self.k)
        distancia = float(distancias[0][0])

        if self.debug:
            print(f"  previsto={caractere} confiança={confianca:.2f} dist={distancia:.1f}")

        return Predicao(caractere, confianca, distancia, img)

    def prever_lote(self, filtradas: list[ImageFilter]) -> list[Predicao | None]:
        """Classifica muitas células numa única varredura do conjunto de treino.

        O KNN não tem custo de treino, só de predição: cada célula é comparada
        contra todas as referências guardadas. Chamado uma célula por vez, o
        custo é dominado por overhead — com as 151k maiúsculas do EMNIST, dá
        204 ms por célula, ou 92 s para as 450 células de nome de uma folha, o
        que projeta 12,4 h para as 500 folhas da coleta.

        Em lote, a mesma conta vira uma multiplicação de matrizes e o BLAS
        toma conta: 3,0 ms por célula, 1,4 s por folha. Medido, 67x. Foi por
        isso que o conjunto ficou inteiro em vez de subamostrado — o gargalo
        era a forma da chamada, não o tamanho do conjunto.

        Devolve uma lista alinhada com a entrada, com None onde a normalização
        não achou caixa (célula sem pixel aceso sobrevivente).
        """
        amostras: list[np.ndarray] = []
        indices: list[int] = []
        imagens: list[Image] = []
        for i, filtrada in enumerate(filtradas):
            preparada = self.preparar_amostra(filtrada.get_image())
            if preparada is None:
                continue
            amostra, img = preparada
            amostras.append(np.asarray(amostra).reshape(-1))
            indices.append(i)
            imagens.append(img)

        saida: list[Predicao | None] = [None] * len(filtradas)
        if not amostras:
            return saida

        X = np.array(amostras, dtype=np.float32)
        rotulos = self.modelo.predict(X)
        confiancas = self.modelo.predict_proba(X).max(axis=1)
        distancias, _ = self.modelo.kneighbors(X, n_neighbors=self.k)

        for j, i in enumerate(indices):
            saida[i] = Predicao(self.alfabeto[int(rotulos[j])],
                                float(confiancas[j]), float(distancias[j][0]),
                                imagens[j])
        return saida

    def prever_de_imagem_crua(self, img: Image) -> Predicao | None:
        # só o corte de borda continua aqui; bounding box, escala e centralização
        # agora são responsabilidade de `normalizar_mnist`.
        borda = Border(img)
        borda.dynamic_cutter_while_visible_border()
        return self.prever_de_imagem_filtrada(borda)

    def prever_de_caminho(self, caminho: str) -> Predicao | None:
        img = Image().load_from_file(caminho).binarize().inversion().cut_borders(2)
        return self.prever_de_imagem_crua(img)


class OCRCNN(OCR):
    """Mesma interface do OCR, com a CNN no lugar do KNN.

    Sobrescreve só o que muda de verdade — o formato da entrada e a origem da
    confiança. O caminho até a célula normalizada é idêntico, e tem que ser: é
    o que garante que a comparação KNN x CNN do objetivo (d) meça o modelo, e
    não dois pré-processamentos diferentes.

    Medido nas mesmas 304 células da m3_2: KNN(k=5) 0,786 e CNN 0,849, com
    McNemar p=0,018. E a predição cai de 1,4 s para 0,12 s por folha.

    O ganho que não está na acurácia é a **escala da confiança**. O KNN entrega
    k+1 valores possíveis, então o corte do `?` do D02 escolhe entre degraus
    (A20). A softmax é contínua:

        corte 0,90 -> cobertura 0,734 · precisão 0,924
        corte 0,95 -> cobertura 0,678 · precisão 0,947
        corte 0,99 -> cobertura 0,549 · precisão 0,970
    """

    def __init__(self, caminho_modelo: str = CAMINHO_MODELO_CNN_LETRAS,
                 alfabeto: str = ALFABETO_MAIUSCULAS, lado: int = LADO_MODELO):
        import os
        os.environ.setdefault("KERAS_BACKEND", "torch")
        import keras
        self.modelo = keras.saving.load_model(caminho_modelo)
        self.alfabeto = alfabeto
        self.lado = lado
        self.debug = False

    # As de `OCR` apontariam para os .joblib, e a herança as traria em silêncio —
    # carregando o KNN dentro de uma classe chamada OCRCNN.
    @classmethod
    def de_digitos(cls) -> OCRCNN:
        """MATRÍCULA com CNN: 0,896 contra 0,815 do melhor KNN nas mesmas 270
        células, McNemar p=0,00094, e vencendo nas três folhas separadamente."""
        return cls(CAMINHO_MODELO_CNN_DIGITOS, ALFABETO_DIGITOS)

    @classmethod
    def de_letras(cls) -> OCRCNN:
        """NOME com CNN: 0,849 contra 0,786 do KNN (p=0,018)."""
        return cls(CAMINHO_MODELO_CNN_LETRAS, ALFABETO_MAIUSCULAS)

    @property
    def k(self) -> int:
        return 0        # não há vizinhos; existe para a interface não quebrar

    def _prever_matriz(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Devolve (índices, confiança, margem sobre o segundo) de um lote."""
        entrada = (X.astype(np.float32) / 255.0)[..., None]
        probs = self.modelo.predict(entrada, verbose=0)
        ordenado = np.sort(probs, axis=1)
        margem = ordenado[:, -1] - ordenado[:, -2]
        return probs.argmax(axis=1), probs.max(axis=1), margem

    def prever_lote(self, filtradas: list[ImageFilter]) -> list[Predicao | None]:
        amostras, indices, imagens = [], [], []
        for i, filtrada in enumerate(filtradas):
            preparada = self.preparar_amostra(filtrada.get_image())
            if preparada is None:
                continue
            _, img = preparada
            amostras.append(img.data)
            indices.append(i)
            imagens.append(img)

        saida: list[Predicao | None] = [None] * len(filtradas)
        if not amostras:
            return saida

        rotulos, confs, margens = self._prever_matriz(np.array(amostras, dtype=np.uint8))
        for j, i in enumerate(indices):
            saida[i] = Predicao(self.alfabeto[int(rotulos[j])], float(confs[j]),
                                0.0, imagens[j], float(margens[j]))
        return saida

    def prever_de_imagem_filtrada(self, filtrada: ImageFilter) -> Predicao | None:
        return self.prever_lote([filtrada])[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconhece o caractere de uma imagem de célula.")
    parser.add_argument("imagem", type=str, help="caminho da imagem")
    parser.add_argument("--campo", default="matricula", choices=("matricula", "nome"),
                        help="qual alfabeto usar (matrícula = dígitos, nome = letras)")
    args = parser.parse_args()

    ocr = (OCR.de_digitos() if args.campo == "matricula" else OCR.de_letras()).set_debug(True)
    p = ocr.prever_de_caminho(args.imagem)
    if p is None:
        raise SystemExit(f"{args.imagem}: célula vazia, nada a reconhecer")
    print(f"{args.imagem}: '{p.caractere}' | confiança {p.confianca:.2f} | dist {p.distancia:.1f}")
    print(f"(k={ocr.k} → a confiança só assume {ocr.k + 1} valores distintos no máximo)")
