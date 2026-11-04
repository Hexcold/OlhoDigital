"""CNN leve inspirada em LeNet-5, para comparar com o KNN por campo.

É a desafiante do [[D06]]. O baseline é o KNN, e o princípio do projeto é que
complexidade só entra onde houver ganho **mensurável** — então este módulo
existe para ser medido contra `avaliar.py`, não para substituir nada de saída.

## O que se espera dela, e o que NÃO se espera

A justificativa mudou no meio do caminho. Antes de A19 as letras davam 54,3% e
parecia que a CNN era necessária para fechar 24,5 pontos; corrigido o bug de
normalização, o KNN dá 78,6% nas letras e 78,8% nos dígitos — empatados. Então
a CNN **não** entra para consertar um campo quebrado. Entra por:

  1. Objetivo específico (d) do TCC: a comparação KNN x CNN por campo.
  2. A lacuna que sobra: holdout 94,1% contra 78,6% na folha. O KNN generaliza
     mal do dataset para o papel, e é isso que a augmentation ataca.
  3. **Softmax contínua.** O k=3 admite 4 níveis de confiança e o k=21 admite
     22, mas ambos são degraus (A20). A abstenção do D02 precisa de um corte
     calibrável, e um corte precisa de escala contínua. Este é o ganho que não
     depende de a acurácia subir.
  4. Custo de predição constante, contra 151k comparações por célula do KNN.

## Protocolo, fixado pelo D06

Adam (lr 0,001), categorical cross-entropy, early stopping, batch 32, split
80/20. Augmentation: rotação ±10°, translação ≤10%, brilho/contraste, ruído
gaussiano, desfoque leve.

## Casamento de domínio, que não é opcional

O conjunto de treino passa pela MESMA `normalizar_mnist` que a célula da folha
percorre, via `normalizar_lote`. Foi esquecer isso que custou 22,9 pontos ao
KNN (A19). A CNN é menos sensível a escala que a distância euclidiana, mas não
é imune — e não há motivo para testar a sorte.

Uso:
    python treinar_cnn.py --campo nome
    python treinar_cnn.py --campo nome --epocas 40 --sem-augmentation
"""

from __future__ import annotations

# O backend tem que ser escolhido ANTES de importar keras. TensorFlow não
# publica para Python 3.14, que é o desta máquina; o Keras 3 é independente dele
# e roda sobre torch, então a API do D06 continua valendo sem tradução.
import os
os.environ.setdefault("KERAS_BACKEND", "torch")

import argparse
import datetime as dt
import numpy as np

from dados_treino import (
    carregar_mnist, carregar_emnist_maiusculas, normalizar_lote,
    ALFABETO_DIGITOS, ALFABETO_MAIUSCULAS,
)

SEMENTE = 42
LADO = 28

# campo -> (carregador, alfabeto, caminho do modelo)
CAMPOS = {
    "matricula": (carregar_mnist, ALFABETO_DIGITOS, "modelo/cnn_mnist_28x28.keras"),
    "nome": (carregar_emnist_maiusculas, ALFABETO_MAIUSCULAS,
             "modelo/cnn_emnist_maiusculas_28x28.keras"),
}


def construir(n_classes: int, lado: int = LADO):
    """LeNet-5 adaptada: duas convoluções, dois pooling, duas densas.

    Diferenças em relação à LeNet-5 de 1998, todas por motivo:
      - ReLU no lugar de tanh: converge mais rápido e não satura.
      - `padding='same'` na primeira conv: preserva a borda, que em letra de
        forma carrega traço (a haste do L, a barra do T).
      - Dropout antes da saída: o EMNIST tem 151k amostras para 26 classes e a
        rede tem folga para decorar; o que interessa é generalizar para a folha.
      - BatchNormalization: estabiliza o treino com augmentation agressiva.
    """
    from keras import layers, models

    return models.Sequential([
        layers.Input(shape=(lado, lado, 1)),
        layers.Conv2D(32, 5, padding="same", activation="relu"),
        layers.BatchNormalization(),
        layers.MaxPooling2D(2),

        layers.Conv2D(64, 5, padding="same", activation="relu"),
        layers.BatchNormalization(),
        layers.MaxPooling2D(2),

        layers.Flatten(),
        layers.Dense(128, activation="relu"),
        layers.Dropout(0.4),
        layers.Dense(n_classes, activation="softmax"),
    ])


def _construir_afim():
    """Camada de rotação + translação numa única transformação afim, na GPU.

    Existe por medição. As camadas `RandomRotation` e `RandomTranslation` do
    Keras custam 5,4x e 5,2x o tempo de uma época, e **não aceleram com a GPU** —
    juntas levam o treino de 33 min para 316 min. `RandomContrast` e
    `GaussianNoise`, medidas no mesmo teste, custam 1,1x e 1,2x, então o problema
    é específico das duas transformações geométricas.

    Aqui as duas viram UMA matriz afim por amostra, aplicada com `grid_sample`,
    que é operação nativa de GPU. Além de rodar no lugar certo, é um único warp
    em vez de dois — e dois warps sucessivos também interpolam duas vezes, o que
    borra o traço fino da letra sem necessidade.
    """
    import torch
    from keras import layers

    class AfimAleatoria(layers.Layer):
        def __init__(self, graus: float = 10.0, translacao: float = 0.10, **kw):
            super().__init__(**kw)
            self.graus = graus
            self.translacao = translacao

        def call(self, x, training=None):
            # no predict é identidade: augmentar a avaliação seria medir outra coisa
            if not training:
                return x

            t = x if torch.is_tensor(x) else torch.as_tensor(x)
            n = t.shape[0]
            # Keras entrega (N,H,W,C); grid_sample quer (N,C,H,W)
            t = t.permute(0, 3, 1, 2).contiguous()

            ang = (torch.rand(n, device=t.device) * 2 - 1) * (self.graus * torch.pi / 180)
            dx = (torch.rand(n, device=t.device) * 2 - 1) * (self.translacao * 2)
            dy = (torch.rand(n, device=t.device) * 2 - 1) * (self.translacao * 2)

            cos, sin = torch.cos(ang), torch.sin(ang)
            theta = torch.zeros(n, 2, 3, device=t.device, dtype=t.dtype)
            theta[:, 0, 0], theta[:, 0, 1], theta[:, 0, 2] = cos, -sin, dx
            theta[:, 1, 0], theta[:, 1, 1], theta[:, 1, 2] = sin, cos, dy

            grade = torch.nn.functional.affine_grid(theta, t.shape, align_corners=False)
            saida = torch.nn.functional.grid_sample(
                t, grade, mode="bilinear", padding_mode="zeros", align_corners=False)
            return saida.permute(0, 2, 3, 1).contiguous()

        def compute_output_shape(self, input_shape):
            return input_shape

    return AfimAleatoria()


def augmentar(lado: int = LADO):
    """As transformações do D06, como camadas de pré-processamento.

    Ficam DENTRO do grafo e só agem no treino — no `predict` são identidade.
    Isso evita a armadilha clássica de augmentar o conjunto de avaliação.

    Cada uma tem alvo declarado:
      rotação ±10°       a folha é escaneada com ArUco, então o desalinhamento
                         residual é pequeno; ±10° cobre a inclinação da letra.
      translação ≤10%    a célula é recortada por geometria fracionária, e o
                         glifo não fica sempre no centro.
      contraste/brilho   varia com a caneta e com o scanner.
      ruído gaussiano    resíduo da binarização Otsu.

    As duas primeiras são uma única afim na GPU — ver `_construir_afim`.
    """
    from keras import layers

    return [
        _construir_afim(),
        layers.RandomContrast(0.2),
        layers.GaussianNoise(0.03),
    ]


def preparar(campo: str, semente: int = SEMENTE):
    """Carrega, normaliza no espaço da folha e separa treino/validação 80/20."""
    from sklearn.model_selection import train_test_split

    carregar, alfabeto, _ = CAMPOS[campo]
    X, y = carregar()
    # o mesmo caminho que a célula percorre em OCR.preparar_amostra (A19)
    X = normalizar_lote(X, lado=LADO)
    X = (X.astype("float32") / 255.0)[..., None]

    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.2, random_state=semente, stratify=y)
    return X_tr, X_val, y_tr, y_val, alfabeto


def treinar(campo: str, epocas: int = 30, batch: int = 32,
            com_augmentation: bool = True, semente: int = SEMENTE):
    import keras
    from keras import layers, models, callbacks

    keras.utils.set_random_seed(semente)     # reprodutibilidade (objetivo i)

    X_tr, X_val, y_tr, y_val, alfabeto = preparar(campo, semente)
    n_classes = len(alfabeto)
    print(f"campo={campo} | {len(y_tr)} treino, {len(y_val)} validação, "
          f"{n_classes} classes | augmentation={'sim' if com_augmentation else 'não'}")

    nucleo = construir(n_classes)
    if com_augmentation:
        modelo = models.Sequential([layers.Input(shape=(LADO, LADO, 1)),
                                    *augmentar(), nucleo])
    else:
        modelo = nucleo

    modelo.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001),
                   loss="sparse_categorical_crossentropy",
                   metrics=["accuracy"])

    parar = callbacks.EarlyStopping(monitor="val_accuracy", patience=5,
                                    restore_best_weights=True, verbose=1)
    reduzir = callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5,
                                          patience=3, min_lr=1e-5, verbose=1)

    # Log por época em ARQUIVO, não só no stdout. Duas razões, e nenhuma é
    # estética: um treino longo cujo progresso não se vê é indistinguível de um
    # travado — e a curva de treino é figura pedida para o TCC.
    os.makedirs("modelo", exist_ok=True)
    caminho_log = f"modelo/log_cnn_{campo}{'' if com_augmentation else '_sem_aug'}.csv"
    registrar = callbacks.CSVLogger(caminho_log)

    t0 = dt.datetime.now()
    hist = modelo.fit(X_tr, y_tr, validation_data=(X_val, y_val),
                      epochs=epocas, batch_size=batch,
                      callbacks=[parar, reduzir, registrar], verbose=2)
    tempo = dt.datetime.now() - t0
    print(f"histórico por época em {caminho_log}")

    # o modelo salvo é o NÚCLEO: augmentation é de treino, não de inferência
    holdout = float(max(hist.history["val_accuracy"]))
    return nucleo, holdout, tempo, len(hist.history["loss"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Treina a CNN de um campo.")
    p.add_argument("--campo", default="nome", choices=list(CAMPOS))
    p.add_argument("--epocas", type=int, default=30)
    p.add_argument("--batch", type=int, default=32, help="32, como fixa o D06")
    p.add_argument("--sem-augmentation", action="store_true",
                   help="ablação: mede quanto a augmentation vale na folha real")
    args = p.parse_args()

    import keras
    print(f"keras {keras.__version__} | backend {keras.backend.backend()}")
    try:
        import torch
        print(f"CUDA: {torch.cuda.is_available()}"
              + (f" ({torch.cuda.get_device_name(0)})" if torch.cuda.is_available() else ""))
    except Exception:
        pass

    modelo, holdout, tempo, epocas = treinar(
        args.campo, args.epocas, args.batch, not args.sem_augmentation)

    _, alfabeto, caminho = CAMPOS[args.campo]
    if args.sem_augmentation:
        caminho = caminho.replace(".keras", "_sem_aug.keras")
    modelo.save(caminho)

    # o custo que interessa é o de predição, e o da CNN é constante — ao
    # contrário do KNN, que compara contra todas as referências guardadas
    amostra = np.zeros((1, LADO, LADO, 1), dtype="float32")
    modelo.predict(amostra, verbose=0)                     # aquecer
    t1 = dt.datetime.now()
    for _ in range(20):
        modelo.predict(amostra, verbose=0)
    ms = (dt.datetime.now() - t1).total_seconds() / 20 * 1000

    lote = np.zeros((450, LADO, LADO, 1), dtype="float32")
    t2 = dt.datetime.now()
    modelo.predict(lote, verbose=0)
    s_folha = (dt.datetime.now() - t2).total_seconds()

    print(f"\nholdout {holdout:.4f} em {epocas} épocas ({tempo})")
    print(f"parâmetros: {modelo.count_params():,}")
    print(f"predição: {ms:.1f} ms/célula avulsa · {s_folha:.2f} s por folha (lote de 450)")
    print(f"modelo salvo em {caminho}")
    print("\nressalva: holdout mede mal o desempenho real — o KNN tinha 94,1% de")
    print("holdout e 78,6% na folha. Confira com: python avaliar.py --coluna nome")
