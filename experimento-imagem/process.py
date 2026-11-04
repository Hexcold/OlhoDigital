"""Pipeline de imagem: folha escaneada -> caracteres de uma coluna da grade.

Porte do `process.py` do protótipo anterior. Etapas, na ordem da Seção 4.4:
  1. detecção dos 4 marcadores ArUco e retificação de perspectiva (align.py)
  2. recorte da grade em células (sheet_cutter.py)
  3. binarização Otsu + inversão + corte de borda (image.py)
  4. análise de componentes conexos: descarta resíduo de grade (region.py)
  5. normalização MNIST: bounding box, escala para 20px e centralização pelo
     centro de massa numa tela 28x28 (dados_treino.normalizar_mnist, via ocr.py)
  6. classificação KNN (ocr.py)

Limitação herdada, registrada no vault: o reconhecedor CHUTA, não existe
abstenção. Emitir '?' quando a confiança for baixa é o M1.

Há um classificador POR CAMPO, escolhido pela coluna pedida: dígitos (MNIST, 10
classes) para a matrícula, maiúsculas (EMNIST ByClass, 26 classes) para o nome.
A matrícula não contém letra e o nome não contém dígito, então a restrição de
alfabeto entra na escolha do modelo em vez de virar correção posterior.

A segmentação da coluna de nome foi conferida contra o gabarito antes de haver
classificador: 522/522 células, 29/29 linhas com a máscara de ocupação exata,
separadores entre palavras inclusive. O gargalo da coluna de nome nunca foi a
análise de componentes conexos — era só a ausência do modelo de letras.

Uso:
    python process.py -i dados/m2-1.png
    python process.py -i dados/m3_1.png -m 3 --coluna nome
"""

import argparse
import datetime as dt
import json
import os

from image import Image
from align import Align
from ocr import OCR
from region import Decomposer
from sheet_cutter import ModeloFolha, SheetCutter, modelo as modelo_folha
from color import Color

# A resolução do warp mudou de casa: era a constante global `ALTURA, LARGURA` e
# virou campo do modelo de folha (`ModeloFolha.warp`), porque uma folha de layout
# diferente pede resolução diferente para chegar ao mesmo número de pixels por
# célula — e uma constante global só pode estar certa para uma delas. As folhas
# impressas continuam em 1140x1527, que é a resolução em que A07 mediu.
LADO_MODELO = 28               # entrada do KNN, fixada por `normalizar_mnist`
PASTA_CELULAS = "cache_temp"
VAZIO = "."   # célula sem escrita nenhuma
PERDIDA = "_"  # tinha escrita, mas nada sobreviveu à normalização
ANULADA = "X"  # célula pintada por inteiro: a pessoa anulou a linha

# Protocolo de anulação: quem errar preenche COMPLETAMENTE uma célula qualquer
# da linha e reescreve na linha de baixo.
#
# Por que a linha anulada importa: o caso fácil se resolve sozinho, porque a
# linha abandonada e a reescrita dão a mesma matrícula e duplicata é trivial de
# detectar. O caso que não se resolve é a pessoa escrever a matrícula de OUTRA
# pessoa real e reescrever embaixo — aí a linha abandonada vira presença falsa
# de terceiro, com score 1,000 e margem folgada. É o A15 por outro caminho, e
# nem theta nem delta o alcançam.
#
# O limiar veio de medição, não de chute. Nas 642 células com gabarito das três
# folhas de desenvolvimento, a escrita real ocupa no MÁXIMO 15,5% da célula
# (letras: média 6,8%, máx 14,7%; dígitos: média 7,6%, máx 15,5%). Uma célula
# pintada de propósito passa de 90%. Em 0,50 há 3,2x de folga sobre a célula
# mais densa já observada, e nenhuma das 642 seria lida como anulação.
#
# O risco é assimétrico e o limiar é conservador de propósito: NÃO detectar uma
# anulação é barato (a linha lê mal e cai em quarentena, que é o desfecho
# certo); detectar uma que não houve apaga a presença de quem estava lá. Por
# isso a linha anulada é REGISTRADA, nunca descartada em silêncio.
LIMIAR_ANULACAO = 0.50


def densidade_tinta(binaria: Image) -> float:
    """Fração de pixels acesos da célula já binarizada e invertida.

    Medida ANTES do `Decomposer`: o filtro de componentes conexos existe para
    tirar resíduo de grade, e um bloco pintado não é resíduo. Medir depois
    dependeria de o bloco sobreviver a `erase_regions_outside_bounds`.
    """
    return float((binaria.data > 0).mean()) if binaria.data.size else 0.0


def celula_anulada(binaria: Image, limiar: float = LIMIAR_ANULACAO) -> bool:
    """A célula está preenchida por inteiro? Ver LIMIAR_ANULACAO."""
    return densidade_tinta(binaria) >= limiar

# As folhas impressas diferem nas LINHAS, não nas colunas (A09). A m3 usa a mesma
# grade de linhas da m1 — muda só o título e a instrução impressa.
FOLHAS = {1: "m1", 2: "m2", 3: "m1"}
CAMPOS = ("matricula", "nome")


def folha(ref: int | str) -> ModeloFolha:
    """Modelo de folha por referência.

    1/2/3 são os modelos impressos, herdados do `-m` da linha de comando;
    "gerada" é a folha do `gerar_folha.py`. Aceitar os dois evita renomear a
    referência em cinco módulos e nos gabaritos já escritos.
    """
    return modelo_folha(FOLHAS[ref] if isinstance(ref, int) else ref)


def carregar_gabarito(caminho: str) -> dict[int, tuple[str, str]]:
    """Lê o .json irmão da imagem: {"01": ["968334", "nome"], ...}."""
    caminho = os.path.splitext(caminho)[0] + ".json"
    if not os.path.exists(caminho):
        return {}
    with open(caminho) as f:
        dados = json.load(f)
    return {int(k): (v[0], v[1]) for k, v in dados.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa uma folha e lê uma coluna da grade.")
    parser.add_argument("--input", "-i", default="dados/m2-1.png", help="imagem da folha")
    parser.add_argument("--sheet_model", "-m", default="2",
                        choices=("1", "2", "3", "gerada"),
                        help="modelo da folha (define geometria e resolução do warp)")
    parser.add_argument("--coluna", "-c", default="matricula", choices=CAMPOS,
                        help="qual campo recortar")
    parser.add_argument("--align", "-a", default="", help="salvar a imagem alinhada aqui")
    parser.add_argument("--qtd", type=int, default=0, help="quantas linhas processar (0 = todas)")
    parser.add_argument("--preview", action="store_true", help="desenhar as células no terminal (kitty/wezterm)")
    parser.add_argument("--zoom", "-z", type=float, default=1.0)
    args = parser.parse_args()

    if args.preview:
        Image.preview_zoom = args.zoom

    gabarito = carregar_gabarito(args.input)

    modelo = folha(int(args.sheet_model) if args.sheet_model.isdigit()
                   else args.sheet_model)

    t0 = dt.datetime.now()
    imagem = Image().load_from_file(args.input)
    alinhador = Align(*modelo.warp)
    alinhada = Image().set_data(alinhador.aruco(imagem.data))
    if args.align:
        alinhada.save_to_file(args.align)

    cortador = SheetCutter(PASTA_CELULAS)
    celulas, _ = cortador.cut_info(alinhada, modelo, args.coluna)

    # Um classificador por campo: a matrícula não tem letra, o nome não tem
    # dígito. A restrição de alfabeto entra aqui, na escolha do modelo, e não
    # depois como correção — ver o cabeçalho de `ocr.py`.
    coluna_nome = args.coluna == "nome"
    ocr = OCR.de_letras() if coluna_nome else OCR.de_digitos()
    px_x, px_y = modelo.px_por_celula()
    print(f"carga e recorte: {dt.datetime.now() - t0}  |  folha={modelo.rotulo}"
          f"  warp={modelo.warp[0]}x{modelo.warp[1]}  célula={px_x:.1f}x{px_y:.1f}px")
    print(f"coluna={args.coluna}  k={ocr.k}  alfabeto={len(ocr.alfabeto)} classes")

    acertos = total = perdidas = 0
    confiancas: list[float] = []
    ocupacao: list[int] = []

    for i, linha in enumerate(celulas):
        if args.qtd and i >= args.qtd:
            break
        num = i + 1
        print(f"{num:02}: ", end="")

        # o esperado desta linha: matrícula ou nome, conforme a coluna pedida
        esperado = ""
        if num in gabarito:
            esperado = gabarito[num][1] if coluna_nome else gabarito[num][0]

        escritas = 0
        for j, celula in enumerate(linha):
            img = celula.binarize(gaussian=False).inversion().cut_borders(2)
            filtrada = Decomposer(img).erase_small_regions(0.2).erase_regions_outside_bounds()

            # Célula sem nenhuma região sobrevivente = nada escrito ali. Não é
            # ilegibilidade (isso é o '?' do M1), é ausência: a linha da folha
            # não foi preenchida. O protótipo anterior quebrava aqui.
            if filtrada.get_image().is_empty():
                print(VAZIO, end="")
                # uma célula em branco só conta como erro se o gabarito diz que
                # havia caractere ali. Sem isso a acurácia esconde a perda.
                if j < len(esperado) and esperado[j] != " ":
                    total += 1
                    perdidas += 1
                continue

            escritas += 1

            # bounding box, escala e centralização por centro de massa agora
            # vivem em `normalizar_mnist`, dentro do OCR. A célula filtrada vai
            # crua para lá — é exatamente a entrada que o `avaliar.py` mediu.
            pred = ocr.prever_de_imagem_filtrada(filtrada)
            if pred is None:
                # tinha tinta, mas a normalização não achou caixa: perda real
                print(PERDIDA, end="")
                if j < len(esperado):
                    total += 1
                    perdidas += 1
                continue

            confiancas.append(pred.confianca)
            texto = pred.caractere

            if j < len(esperado):
                total += 1
                if esperado[j] == texto:
                    acertos += 1
                    texto = Color.green(texto)
                else:
                    texto = Color.red(texto)

            if args.preview:
                print(f"{celula}{pred.imagem}{texto} ", end="")
            else:
                print(texto, end="")

        ocupacao.append(escritas)
        if coluna_nome and esperado:
            print(f"   ({escritas} células)  esperado: {esperado}", end="")
        elif coluna_nome:
            print(f"   ({escritas} células)", end="")
        print()

    if total:
        print(f"\nacurácia por caractere: {acertos}/{total} = {acertos / total * 100:.1f}%")
        if perdidas:
            print(f"  das quais {perdidas} foram perdidas antes de classificar "
                  f"(célula vazia ou sem caixa) — contam como erro")
    else:
        print("\n(sem gabarito para esta folha — nada a conferir)")

    if confiancas:
        media = sum(confiancas) / len(confiancas)
        maximos = sum(1 for c in confiancas if c >= 0.999)
        print(f"confiança média {media:.3f} | unânimes {maximos}/{len(confiancas)} "
              f"({100 * maximos / len(confiancas):.0f}%)")

    preenchidas = [n for n in ocupacao if n]
    if preenchidas and coluna_nome:
        print(f"\nocupação da coluna de nome: {len(preenchidas)} linhas preenchidas, "
              f"média {sum(preenchidas) / len(preenchidas):.1f} células, "
              f"máximo {max(preenchidas)} de {len(celulas[0])}")


if __name__ == "__main__":
    main()
