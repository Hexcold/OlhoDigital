"""Recorte da grade da folha retificada em células.

Um **modelo de folha** é a geometria inteira de um layout impresso: onde estão as
linhas, onde estão as colunas, e em que resolução a folha é retificada. As três
coisas andam juntas porque descasá-las é bug silencioso — o cortador devolve
recorte torto e ninguém reclama.

A geometria de posição é dada em **fração do retângulo dos marcadores**, nunca em
pixel: o `align.py` leva os cantos externos dos 4 ArUco para as quinas da imagem
retificada, então é esse retângulo — e não a folha A4 inteira — que é o sistema de
coordenadas. Trocar a resolução do warp não muda fração nenhuma.

Duas famílias de modelo convivem, e por razões diferentes:

  - **impressas** (M1, M2): frações MEDIDAS nas cinco folhas reais em 2026-08-21,
    detectando as linhas da grade por projeção (ver A09 no vault). São as folhas
    de desenvolvimento, e todos os números de leitura do TCC 2 foram medidos
    nelas. Ficam congeladas.
  - **gerada**: frações DERIVADAS do `gerar_folha.LAYOUT`, isto é, das mesmas
    constantes que desenharam o PDF (ver D10). Aqui o descasamento entre o que
    foi impresso e o que é recortado deixa de existir como classe de bug.

Nas folhas impressas, as COLUNAS são as mesmas nas duas e as LINHAS não são: o
fim da matrícula caiu entre x=278 e x=282 e o da coluna de nome entre 1114 e 1115
nas cinco folhas — menos de 10% de célula, ruído de escaneamento —, enquanto a
grade do M1 começa e termina mais abaixo que a do M2, com passo de 45,6px contra
44,8px. A diferença é estável dentro de cada modelo: 45,56 / 45,60 / 45,56 nas
três folhas M1 e 44,83 / 44,76 nas duas M2.

> Por que o warp de 1140x1527 NÃO é isotrópico, e isso é de propósito.
>
> O retângulo dos marcadores foi medido nos scans: 189,99 x 275,08 mm nas folhas
> impressas (razão 0,6907, com desvio menor que 0,0002 entre as quatro folhas
> conferidas) e 195,30 x 282,30 mm na gerada (razão 0,6918). O warp tem razão
> 0,7466, logo estica a horizontal em 1,0810 na folha impressa e 1,0792 na
> gerada — os traços chegam ~8% mais largos do que foram escritos, nas duas.
>
> Isso alcança o classificador: `dados_treino.normalizar_mnist` escala os dois
> eixos por um fator só (`caixa / max(h, w)`), então o aspecto do traço é
> preservado, inclusive o aspecto distorcido. Mas o mesmo 8% está nas células em
> que a CNN foi afinada (A28), e a diferença entre as duas folhas é de 0,18%.
> Consertar o warp só na folha nova INTRODUZIRIA a mudança de domínio que
> pareceria estar removendo. A razão fica preservada até que alguém remeça as
> duas pontas juntas.

Era o descasamento entre geometria e resolução que o modelo antigo escondia:
`Model.M1` guardava coordenadas absolutas (12, 268, 246, 1312) enquanto `cut_info`
multiplicava tudo por `w`/`h`, então pedir a folha M1 recortava lixo — e o código
nunca reclamava.
"""

from dataclasses import dataclass, field

import cv2
import os
import shutil

from image import Image

# Razão largura/altura do warp herdado. Não é a razão física do papel; ver o
# aviso no cabeçalho. Toda folha nova nasce com esta razão para que o traço
# chegue ao classificador com a mesma distorção do conjunto em que ele foi
# afinado.
RAZAO_WARP = 1140 / 1527

# Frações medidas nas cinco folhas impressas (A09). Congeladas: são a geometria
# das folhas de desenvolvimento, e mexer aqui invalida toda medição já feita.
COLUNAS_IMPRESSAS: dict[str, dict[str, float]] = {
    "matricula": {"columns": 6, "x_begin": 0.0112, "x_end": 0.2456},
    "nome": {"columns": 18, "x_begin": 0.2847, "x_end": 0.9776},
}


@dataclass(frozen=True)
class ModeloFolha:
    """Geometria completa de um layout impresso, resolução inclusive.

    `warp` é (largura, altura) da folha retificada. Vive aqui, e não como
    constante de módulo do `process`, porque folhas de layout diferente pedem
    resoluções diferentes para chegar ao mesmo número de pixels por célula — e
    uma constante global só pode estar certa para uma delas.
    """

    rotulo: str
    warp: tuple[int, int]
    linhas: int
    y_begin: float
    y_end: float
    colunas: dict[str, dict[str, float]]

    # Campos de UMA linha só, fora da grade — hoje a CHAVE, que identifica o
    # evento e vive acima da tabela. Não são `colunas` porque não compartilham
    # o `y_begin`/`y_end`/`linhas` da grade: cada banda traz o próprio y.
    # Folhas antigas não têm nenhuma, e o dicionário vazio diz exatamente isso.
    bandas: dict[str, dict[str, float]] = field(default_factory=dict)

    def banda(self, campo: str) -> dict[str, float]:
        if campo not in self.bandas:
            raise KeyError(f"{self.rotulo}: banda desconhecida {campo!r} "
                           f"(tem {sorted(self.bandas) or 'nenhuma'})")
        return self.bandas[campo]

    def coluna(self, campo: str) -> dict[str, float]:
        if campo not in self.colunas:
            raise KeyError(f"{self.rotulo}: campo desconhecido {campo!r} "
                           f"(tem {sorted(self.colunas)})")
        return self.colunas[campo]

    def passo_y(self, altura_px: int) -> float:
        """Altura de uma linha da grade, em pixels da imagem retificada."""
        return (self.y_end - self.y_begin) * altura_px / self.linhas

    def faixa_y(self, altura_px: int, i: int, folga: int = 0) -> tuple[int, int]:
        """Topo e base da linha `i` (0-based), em pixels, com folga opcional."""
        y0 = self.y_begin * altura_px
        passo = self.passo_y(altura_px)
        return int(y0 + i * passo) - folga, int(y0 + (i + 1) * passo) + folga

    def faixa_x(self, largura_px: int) -> tuple[int, int]:
        """Da primeira célula da matrícula ao fim do nome, em pixels.

        É a unidade que o revisor da quarentena precisa ver inteira, e a mesma
        conta estava copiada em quatro módulos.
        """
        return (int(self.colunas["matricula"]["x_begin"] * largura_px),
                int(self.colunas["nome"]["x_end"] * largura_px))

    def px_por_celula(self) -> tuple[float, float]:
        """Largura e altura de uma célula na imagem retificada. Self-check."""
        largura_px, altura_px = self.warp
        col = self.colunas["nome"]
        return ((col["x_end"] - col["x_begin"]) * largura_px / col["columns"],
                self.passo_y(altura_px))


def _modelo_impresso(rotulo: str, y_begin: float, y_end: float) -> ModeloFolha:
    return ModeloFolha(rotulo=rotulo, warp=(1140, 1527), linhas=25,
                       y_begin=y_begin, y_end=y_end, colunas=COLUNAS_IMPRESSAS)


# Pixels por célula do domínio em que TODA a leitura foi medida: as folhas de
# desenvolvimento, com 18 células na fração 0,2847-0,9776 de um warp de 1140.
# É este número — não uma largura de warp — que A31 mandou preservar.
PX_POR_CELULA_DOMINIO = 1140 * (0.9776 - 0.2847) / 18   # 43,88 px


def _modelo_gerado() -> ModeloFolha:
    """Modelo da folha do `gerar_folha.py`, derivado das mesmas constantes.

    O import é local para que `sheet_cutter` continue barato de importar: quem
    trabalha só com as folhas impressas não paga o `gerar_folha`.

    A largura do warp é DERIVADA para que a célula caia em
    `PX_POR_CELULA_DOMINIO`, em vez de ser constante. Era constante (1536) e
    estava calibrada para um layout de 35 colunas; qualquer mudança na largura
    do nome a deixava errada em silêncio — que é a mesma classe de acoplamento
    escondido que fez a m4_01 ser recortada com o modelo errado hoje.
    """
    from gerar_folha import LAYOUT

    folha, mat, nome = LAYOUT.folha, LAYOUT.coluna_matricula, LAYOUT.coluna_nome
    largura = round(PX_POR_CELULA_DOMINIO * nome["columns"]
                    / (nome["x_end"] - nome["x_begin"]))
    return ModeloFolha(
        rotulo="gerada",
        warp=(largura, round(largura / RAZAO_WARP)),
        linhas=int(folha["lines"]),
        y_begin=folha["y_begin"],
        y_end=folha["y_end"],
        colunas={"matricula": mat, "nome": nome},
    )


# Largura do warp da folha gerada v1, CONGELADA junto com o resto da geometria
# dela. Não é mais um parâmetro do layout vigente: `_modelo_gerado()` deriva a
# largura de `PX_POR_CELULA_DOMINIO` a cada mudança de coluna, então trocar o
# nome de 28 para 18 já reposiciona o warp sozinho.
#
# 1536 foi o valor certo para as 35 colunas da v1: dava 43,88 px por célula,
# exatamente o que as folhas de desenvolvimento dão em 1140. Herdar o 1140
# custaria 32,6 px e 4,6 pontos de acurácia (258/304 -> 244/304, McNemar
# p=0,034), com degrau brusco logo abaixo. Ver A31.
LARGURA_WARP_V1 = 1536

# Folhas GERADAS que já foram impressas ficam congeladas, pelo mesmo motivo que
# as m1/m2: papel é um instantâneo, e o código anda. A folha m4_01 foi impressa
# em 27/08 com 28 células de nome; horas depois a rubrica levou 4 delas e o
# `LAYOUT` passou a dizer 24. Recortar a m4_01 com o LAYOUT de hoje entrega
# recorte torto — foi o que aconteceu, e só apareceu porque a sobreposição da
# grade foi conferida a olho antes de confiar no número.
#
# Ou seja: `_modelo_gerado()` serve para a folha que AINDA VAI ser impressa;
# folha já impressa vira entrada aqui, com as frações que ela realmente tem.
GERADA_V1 = ModeloFolha(
    rotulo="gerada_v1",
    # o warp é o mesmo de A31: a razão e a largura não são propriedade da
    # versão do layout, são a escolha de amostragem, e valem para as duas
    warp=(LARGURA_WARP_V1, round(LARGURA_WARP_V1 / RAZAO_WARP)),
    linhas=39,
    y_begin=0.15756287637265318,
    y_end=0.9284449167552248,
    colunas={"matricula": {"columns": 6, "x_begin": 0.0, "x_end": 0.17142857142857143},
             "nome": {"columns": 28, "x_begin": 0.2, "x_end": 1.0}},
)

# A v2 estreou a coluna de rubrica e trocou largura de nome por tamanho de
# célula: 18 células de 6,734mm contra as 28 de 5,580mm da v1. A v3 (recuo das
# caixas) e a v4 (recuo removido, mais carimbo e data alinhada) NÃO mudaram
# fração nenhuma — só tinta —, então as três compartilham esta geometria, e as
# folhas m5_01, m6_01 e m7_01 são todas lidas por ela.
GERADA_V2 = ModeloFolha(
    rotulo="gerada_v2",
    warp=(1273, round(1273 / RAZAO_WARP)),
    linhas=33,
    y_begin=0.14677464668303464,
    y_end=0.9340149266493214,
    colunas={"matricula": {"columns": 6, "x_begin": 0.0, "x_end": 0.20689655172413793},
             "nome": {"columns": 18, "x_begin": 0.24137931034482754,
                      "x_end": 0.8620689655172413}},
    # A CHAVE identifica o evento e fica ACIMA da tabela, numa linha só de 16
    # células — daí ser banda e não coluna. Frações derivadas de
    # `gerar_folha.LAYOUT` (y_chave, tab_x0, lado_celula, CELULAS_CHAVE), como
    # as da grade. A célula sai a 43,9 x 40,7 px no warp, praticamente a mesma
    # dos 43,88 px do domínio em que o reconhecedor foi medido — então ela é
    # lida pelo classificador de letras que já existe, sem nada novo.
    bandas={"chave": {"columns": 16, "x_begin": 0.0,
                      "x_end": 0.5517241379310345,
                      "y_begin": 0.09989373007438893,
                      "y_end": 0.12374949613397336}},
)

MODELOS: dict[str, ModeloFolha] = {
    "m1": _modelo_impresso("m1", 0.1914, 0.9376),
    "m2": _modelo_impresso("m2", 0.1726, 0.9061),
    "gerada_v1": GERADA_V1,
    "gerada_v2": GERADA_V2,
}


def modelo(rotulo: str) -> ModeloFolha:
    """Modelo por rótulo. A folha gerada é construída sob demanda."""
    if rotulo == "gerada":
        return _modelo_gerado()
    if rotulo not in MODELOS:
        raise KeyError(f"modelo de folha desconhecido: {rotulo!r} "
                       f"(tem {sorted(MODELOS)} e 'gerada')")
    return MODELOS[rotulo]


class SheetCutter:
    def __init__(self, cache_folder: str):
        self.cache_folder = cache_folder
        # a limpeza acontece UMA vez, na construção. Antes vivia dentro de
        # `cut_info`, e recortar a segunda coluna apagava a primeira.
        shutil.rmtree(self.cache_folder, ignore_errors=True)
        os.makedirs(self.cache_folder, exist_ok=True)

    def cut_info(self, img: Image, modelo: ModeloFolha, campo: str
                 ) -> tuple[list[list[Image]], list[list[str]]]:
        """Recorta o campo pedido em `linhas × columns` células.

        `campo` é "matricula" ou "nome", e nomeia tanto a geometria quanto os
        arquivos do cache. Antes o prefixo do cache era INFERIDO da contagem de
        colunas (`"nome" if colunas == 18`), o que passava a gravar as células
        de nome como `matricula_*` em silêncio assim que a coluna de nome
        deixasse de ter 18 células — que é exatamente o que a folha gerada faz,
        com 28.
        """
        coluna = modelo.coluna(campo)
        h, w = img.get_h_w()

        linhas = modelo.linhas
        colunas = int(coluna["columns"])
        x_ini: float = coluna["x_begin"] * w
        x_end: float = coluna["x_end"] * w
        y_ini: float = modelo.y_begin * h
        y_end: float = modelo.y_end * h
        largura_celula: float = (x_end - x_ini) / colunas
        altura_celula: float = (y_end - y_ini) / linhas

        celulas: list[list[Image]] = [[] for _ in range(linhas)]
        caminhos: list[list[str]] = [[] for _ in range(linhas)]
        for i in range(linhas):
            for j in range(colunas):
                x1 = int(x_ini + j * largura_celula)
                y1 = int(y_ini + i * altura_celula)
                x2 = x1 + int(largura_celula)
                y2 = y1 + int(altura_celula)
                celula = Image(img).set_data(img.data[y1:y2, x1:x2])
                celulas[i].append(celula)
                caminho = f"{self.cache_folder}/{campo}_{i}_{j}.png"
                cv2.imwrite(caminho, celula.data)
                caminhos[i].append(caminho)

        return celulas, caminhos

    def cut_banda(self, img: Image, modelo: ModeloFolha, campo: str
                  ) -> list[Image]:
        """Recorta uma banda de UMA linha — hoje só a CHAVE.

        Separado de `cut_info` porque a banda não pertence à grade: ela tem o
        próprio y e uma linha só. Forçá-la no mesmo caminho exigiria fingir que
        `modelo.linhas` vale 1 aqui, que é o tipo de gambiarra que faz o
        cortador devolver recorte torto sem reclamar.
        """
        geo = modelo.banda(campo)
        h, w = img.get_h_w()
        colunas = int(geo["columns"])
        x_ini, x_end = geo["x_begin"] * w, geo["x_end"] * w
        y1, y2 = int(geo["y_begin"] * h), int(geo["y_end"] * h)
        largura = (x_end - x_ini) / colunas

        celulas = []
        for j in range(colunas):
            a = int(x_ini + j * largura)
            celula = Image(img).set_data(img.data[y1:y2, a:a + int(largura)])
            celulas.append(celula)
            cv2.imwrite(f"{self.cache_folder}/{campo}_{j}.png", celula.data)
        return celulas


if __name__ == "__main__":
    print("=== modelos de folha ===")
    for rotulo in (*sorted(MODELOS), "gerada"):
        m = modelo(rotulo)
        px_x, px_y = m.px_por_celula()
        print(f"{m.rotulo:8s} warp {m.warp[0]}x{m.warp[1]}  "
              f"razao {m.warp[0] / m.warp[1]:.4f}  "
              f"{m.linhas} linhas x {m.colunas['nome']['columns']} celulas de nome")
        print(f"{'':8s} celula de nome na imagem retificada: "
              f"{px_x:.1f} x {px_y:.1f} px (razao {px_x / px_y:.3f})")

    # o prefixo do cache tem que vir do campo pedido, não da contagem de
    # colunas: era a inferência que este self-check existe para travar.
    gerada = modelo("gerada")
    # Esta asserção já foi "== 28" e depois "!= 18", e as duas estavam erradas
    # pelo mesmo motivo: espelhavam uma constante de projeto que muda por
    # decisão (28 -> 24 pela rubrica, 24 -> 18 por tamanho de célula). Contagem
    # de coluna não é invariante.
    #
    # A invariante é a AMOSTRAGEM: mudar a largura do nome tem que reposicionar
    # o warp sozinho, de modo que a célula continue caindo no domínio em que a
    # leitura foi medida (A31). Se alguém quebrar a derivação de
    # `_modelo_gerado`, é aqui que aparece — e o modo de falha é silencioso, do
    # tipo que só apareceria como acurácia pior sem causa aparente.
    px_x, _ = gerada.px_por_celula()
    assert abs(px_x - PX_POR_CELULA_DOMINIO) < 0.5, (
        f"a celula gerada saiu com {px_x:.2f}px, fora do dominio de "
        f"{PX_POR_CELULA_DOMINIO:.2f}px em que a leitura foi medida")
    assert gerada.coluna("matricula")["columns"] == 6
    # a rubrica ocupa largura na folha mas NÃO é campo: se ela aparecer aqui, o
    # cortador passa a recortá-la e a regra de anulação a enxerga — que anularia
    # toda linha bem preenchida, já que rubrica é rabisco denso de propósito.
    assert set(gerada.colunas) == {"matricula", "nome"}, (
        f"a folha gerada só pode ter campos reconhecíveis, tem {sorted(gerada.colunas)}")
    try:
        gerada.coluna("assinatura")
    except KeyError as e:
        print(f"\ncampo desconhecido falha alto: {e}")
