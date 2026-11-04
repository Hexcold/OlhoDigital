"""Geração da folha de presença em PDF vetorial.

Por que gerar em vez de desenhar no editor de texto: a geometria da folha passa a
ser **conhecida por construção**. Hoje o `SheetCutter` guarda frações medidas a
régua nas folhas impressas (ver A07/A09), e toda reimpressão obriga a remedir. Com
a folha gerada aqui, o cortador importa `LAYOUT` e o descasamento deixa de existir.

Três coisas que a folha desenhada no editor errava, e que aqui são impossíveis por
construção (ver o achado sobre a folha de 24/08):

  1. ArUco sem zona de silêncio. Lá o marcador ficava DENTRO de uma célula preta e
     a borda dele fundia com o preenchimento: detectava em 1 de 4 resoluções, e um
     empurrão de 0,2mm mudava quais marcadores apareciam. Aqui a zona de silêncio
     é parte do desenho, e o `__main__` CONFERE a detecção antes de entregar.
  2. Coluna de nome dimensionada no chute. Aqui a largura do nome é a moeda com
     que se compra tamanho de célula — ver a nota em CELULAS_NOME.
  3. Grade preta grossa. O Cristiano pediu cinza claro — aqui é parâmetro.

**A célula é quadrada** (decisão do autor, 24/08/2026). Não é exigência do
reconhecedor: `dados_treino.normalizar_mnist` recorta na bounding box da tinta
ANTES de escalar, então a proporção da célula não chega ao classificador. O que
chega é a proporção do traço — e célula quadrada convida letra de proporção
parecida com a das folhas de desenvolvimento, que é onde os 90,8% foram medidos.

O sistema de coordenadas é o MILÍMETRO, sempre. A folha é um objeto físico; pixel
é detalhe de quem rasteriza.
"""

from dataclasses import dataclass
from functools import lru_cache
import os
import subprocess
import cv2
import numpy as np

# --- papel ------------------------------------------------------------------
A4_LARGURA_MM = 210.0
A4_ALTURA_MM = 297.0

# Área morta da impressora. 6,35mm = 0,25", o pior caso comum (jato de tinta
# antiga). Decisão do autor em 24/08/2026: desenhar para o pior caso, porque a
# folha vai rodar em impressora que não conhecemos.
AREA_MORTA_MM = 6.35
MARGEM_SEGURANCA_MM = 1.0

# --- marcadores -------------------------------------------------------------
# DICT_4X4_50 tem 4 módulos de dados + 1 de borda de cada lado = 6 de ponta a
# ponta. O align.py espera os ids 0=TL, 1=TR, 2=BL, 3=BR.
ARUCO_DICIONARIO = cv2.aruco.DICT_4X4_50
ARUCO_MODULOS = 6
ARUCO_LADO_MM = 12.0          # a folha anterior usava 7,5mm e não detectava
ARUCO_SILENCIO_MODULOS = 2.0  # o mínimo da especificação é 1; 2 é folga barata

# --- grade ------------------------------------------------------------------
# A célula é quadrada e UNIFORME: matrícula, numeração e nome usam o mesmo lado,
# derivado da largura útil. Isso é o que torna a grade um passo constante nos dois
# eixos, e o que faz `SheetCutter` precisar de um número só.
CELULAS_MATRICULA = 6         # a base tem uma matrícula de 5 dígitos, nunca 7
# A largura do nome é a MOEDA com que se compra tamanho de célula: a tabela
# ocupa a largura útil inteira, então cada célula tirada do nome engorda todas
# as outras. 27/08: 24 -> 18, o que leva a célula de 5,580mm para 6,734mm (+21%,
# 92% da célula das folhas de desenvolvimento) ao custo de 39 -> 32 linhas.
#
# O que autoriza gastar essa moeda é MEDIÇÃO, não conforto: truncando as
# leituras reais e reaplicando o matcher, o resultado não se move de 18 para 12
# células (37 aceitos, cobertura 0,902, e 20/0 na m3_2, a única com verdade
# independente). Só em 8 a cobertura cede. E as próprias folhas de
# desenvolvimento têm 18 células — é com elas que a cobertura de 0,900 foi
# medida, então 18 aqui não é aposta, é o mesmo que já funcionou.
#
# O mecanismo é dedutivo, o que ajuda com n=41: `partial_ratio` casa substring,
# então nome truncado continua casando com o nome inteiro na base (A24).
#
# O que se compra do outro lado: a m4_01 mostrou gente escrevendo fora dos
# quadrados numa célula de 5,34mm (5,58 impressa a 96%), e célula maior é o
# ataque direto a isso. Some a folga de captura — a 200 dpi, 6,73mm dá 53,0 px
# nativos contra os 43,9 que o warp pede, então a redução da impressora deixa
# de corroer a margem de A31.
CELULAS_NOME = 18
COLUNAS_NUMERACAO = 1

# --- rubrica ----------------------------------------------------------------
# Espaço para a pessoa rubricar depois de escrever o nome. Alvo: dissuadir
# fraude — alguém assinar presença por outro.
#
# NÃO É CAMPO A RECONHECER. Nada a lê e nada a confere. Ela conta na LARGURA da
# tabela (`Layout.colunas`, senão a célula mudaria de tamanho), mas não é um
# CAMPO: fica fora de `coluna_matricula`/`coluna_nome`, que são as únicas coisas
# que viram `ModeloFolha.colunas` — o dicionário que o `SheetCutter` consome.
# Logo o cortador nunca a recorta e o reconhecedor nunca a vê. Isso importa mais
# do que parece: `process.LIMIAR_ANULACAO = 0,50` anula a linha quando uma
# célula passa de 50% de tinta, e rubrica é rabisco denso, feito para passar
# disso. Se a rubrica virasse coluna do modelo, anularia em silêncio toda linha
# preenchida corretamente — falha sistemática e a favor de quem fraudou.
#
# A largura é dada em colunas da grade só para a aritmética fechar em 35; o
# campo em si é UMA caixa por linha, sem divisões, e tracejada de propósito:
# não pode parecer a grade, ou alguém tenta escrever uma letra por quadrado.
COLUNAS_RUBRICA = 4           # 22,3mm; virar 3 devolve 5,6mm ao nome

# Largura total da tabela, em células. Fonte única: `Layout.colunas` e
# `calcular_layout` leem daqui, para não poderem discordar.
COLUNAS_TOTAIS = (CELULAS_MATRICULA + COLUNAS_NUMERACAO + CELULAS_NOME
                  + COLUNAS_RUBRICA)

# Bandas do cabeçalho, em células do mesmo lado.
CELULAS_CHAVE = 16
CELULAS_DATA = 8              # DDMMAAAA
GRUPOS_DATA = (2, 2, 4)       # desenhadas como DD / MM / AAAA
# O separador vale UM PASSO DE CÉLULA, não uma medida solta. Com 3,0mm os
# quadrados da data caíam entre colunas da grade; com um passo inteiro a banda
# ocupa 10 passos exatos e, ancorada na borda direita da tabela, todo quadrado
# da data cai sobre uma coluna da grade — em qualquer tamanho de célula.
SEPARADOR_DATA_CELULAS = 1

# --- tinta ------------------------------------------------------------------
# Cinza médio, não preto: pedido do Cristiano (feedback do TCC 1) para aumentar o
# contraste com a caneta e reduzir o resíduo de grade na segmentação.
COR_GRADE = "#8c8c8c"
COR_ROTULO = "#5a5a5a"
COR_TEXTO = "#000000"
ESPESSURA_GRADE_MM = 0.30

# Recuo da caixa dentro do passo da célula. ZERO desde 27/08, e o zero é
# resultado de medição, não de preguiça.
#
# A v3 usou 0,35mm com esta hipótese: `Image.cut_borders(2)` rapa 2 px de cada
# lado para tirar resíduo da grade impressa, e com as caixas encostadas ele leva
# traço junto; recuando, os 2 px cairiam sobre branco.
#
# A hipótese foi testada na m4_01, a única folha do modelo gerado com gabarito
# confirmado pelo autor, varrendo o corte de 0 a 4 px:
#
#   matricula (42 celulas): 0px 0,881 · 1px 0,786 · 2px 0,810 · 3px 0,810 · 4px 0,810
#   nome     (162 celulas): 0px 0,803 · 1px 0,796 · 2px 0,815 · 3px 0,770 · 4px 0,727
#
# Nenhuma diferenca entre 0 e 2 px e significativa (McNemar pareado, p entre
# 0,45 e 1,00). Se o corte estivesse custando traco, cortar ZERO teria que
# ganhar — e nao ganha; no nome ate perde. Logo nao ha mecanismo a explorar, e o
# recuo so cobra 0,7mm de caixa (10% do lado) num tamanho de celula que a
# m4 -> m5 mostrou importar. As duas pessoas que preencheram a v2 e a v3
# pioraram na v3, o que e consistente.
#
# Fica como parametro, e nao apagado, porque a hipotese pode voltar com celula
# menor ou grade mais grossa — e ai o numero acima e a linha de base.
RECUO_CELULA_MM = 0.0

# Carimbo de versão. A m4_01 foi impressa com 28 células de nome e horas depois
# o LAYOUT dizia 24: sem carimbo, uma pilha de folhas é indistinguível de outra,
# e recortar com o modelo errado não dá erro, dá recorte torto.
VERSAO_FOLHA = "v4 · 2026-08-27"

FONTE = "DejaVu Sans, Verdana, sans-serif"

# Decisão do autor, 27/08/2026: existe UMA folha, com preenchimento livre.
#
# O protocolo ORIENTADO de D04 ("primeiro nome, iniciais do meio, último
# sobrenome") foi considerado e descartado: é improvável de ser seguido, e A26
# mede o tamanho da aposta — sob preenchimento livre, só 7 de 40 pessoas
# (17,5%) escreveram na forma comprimida sem ninguém pedir.
#
# Isso encerra o item 0g do Backlog por decisão, não por medição: imprimir
# metade das folhas com cada instrução mediria quanto a instrução impressa move
# esses 17,5%, e essa medição não é reconstituível depois da coleta. O que não
# se perde é o resultado, porque o ponto de operação vigente (partial_ratio,
# alpha=0,5, theta=0,60, delta=0,05) não usa o protocolo nem a base reduzida —
# e foi medido em folhas preenchidas livremente.
INSTRUCOES = [
    "USE LETRA DE FORMA MAIÚSCULA, UMA LETRA POR QUADRADO.",
]

# A instrução do NOME saiu em 27/08: o rótulo da coluna já diz "NOME E
# SOBRENOME", e repetir custava 4,2mm de altura — que a essa altura do layout
# valem uma linha de tabela inteira.
#
# A instrução de ANULAÇÃO (pintar um quadrado para anular a linha) também ficou
# de fora, por decisão do autor: no lugar dela, REDUNDÂNCIA — se a mesma pessoa
# for reconhecida em mais de uma linha, vale uma vez. Quem errar simplesmente
# reescreve na linha de baixo, sem precisar aprender protocolo nenhum.
#
# Consequência a não esquecer: `process.LIMIAR_ANULACAO = 0,50` continua ATIVO.
# Ele não atrapalha a redundância (a linha rabiscada some, e a reescrita entra),
# mas passou a ser um mecanismo que ninguém documentou para o participante.


@dataclass(frozen=True)
class Layout:
    """Geometria da folha, em mm e em fração do retângulo dos marcadores.

    As frações são o que o `SheetCutter` consome: o warp do `align.py` leva os
    cantos EXTERNOS dos 4 marcadores para as quinas da imagem retificada, então é
    esse retângulo — não a folha inteira — que vira o sistema de coordenadas.
    """
    ref_x0: float          # retângulo de referência (cantos externos dos marcadores)
    ref_y0: float
    ref_larg: float
    ref_alt: float
    tab_x0: float          # tabela, em mm na folha
    tab_y0: float
    lado_celula: float     # quadrada: vale para os dois eixos
    linhas: int
    y_instrucoes: float
    y_chave: float
    y_rotulos: float

    @property
    def colunas(self) -> int:
        """Largura da tabela em unidades de célula. A rubrica ocupa espaço, então
        conta aqui — mas nunca vira campo; ver `coluna_matricula`/`coluna_nome`."""
        return COLUNAS_TOTAIS

    @property
    def tab_larg(self) -> float:
        return self.colunas * self.lado_celula

    @property
    def x_numeracao(self) -> float:
        return self.tab_x0 + CELULAS_MATRICULA * self.lado_celula

    @property
    def x_nome(self) -> float:
        return self.x_numeracao + COLUNAS_NUMERACAO * self.lado_celula

    @property
    def x_rubrica(self) -> float:
        return self.x_nome + CELULAS_NOME * self.lado_celula

    @property
    def larg_rubrica(self) -> float:
        return COLUNAS_RUBRICA * self.lado_celula

    def _fx(self, x_mm: float) -> float:
        return (x_mm - self.ref_x0) / self.ref_larg

    def _fy(self, y_mm: float) -> float:
        return (y_mm - self.ref_y0) / self.ref_alt

    @property
    def coluna_matricula(self) -> dict:
        return {"columns": CELULAS_MATRICULA,
                "x_begin": self._fx(self.tab_x0),
                "x_end": self._fx(self.tab_x0 + CELULAS_MATRICULA * self.lado_celula)}

    @property
    def coluna_nome(self) -> dict:
        return {"columns": CELULAS_NOME,
                "x_begin": self._fx(self.x_nome),
                "x_end": self._fx(self.x_nome + CELULAS_NOME * self.lado_celula)}

    @property
    def folha(self) -> dict:
        return {"lines": self.linhas,
                "y_begin": self._fy(self.tab_y0),
                "y_end": self._fy(self.tab_y0 + self.linhas * self.lado_celula)}


def calcular_layout() -> Layout:
    """Resolve a geometria a partir das constantes. Nada aqui é medido: é derivado."""
    margem = AREA_MORTA_MM + MARGEM_SEGURANCA_MM
    ref_x0, ref_y0 = margem, margem
    ref_larg = A4_LARGURA_MM - 2 * margem
    ref_alt = A4_ALTURA_MM - 2 * margem

    # a mesma soma vive em `Layout.colunas`; duplicá-la aqui era como as duas
    # pontas da geometria se descasarem em silêncio quando uma banda mudasse
    lado = ref_larg / COLUNAS_TOTAIS   # quadrada: a largura manda, a altura segue

    silencio = ARUCO_SILENCIO_MODULOS * (ARUCO_LADO_MM / ARUCO_MODULOS)
    # o topo da tabela precisa ficar fora da zona de silêncio dos marcadores de cima
    y_instrucoes = ref_y0 + ARUCO_LADO_MM + silencio + 2.0
    # 6mm, não 3: com 3 a última linha de instrução encostava no rótulo CHAVE
    y_chave = y_instrucoes + len(INSTRUCOES) * 4.2 + 6.0
    y_rotulos = y_chave + lado
    tab_y0 = y_rotulos + 6.5

    # e o rodapé, fora da zona de silêncio dos de baixo
    y_limite = ref_y0 + ref_alt - ARUCO_LADO_MM - silencio - 2.0
    linhas = int((y_limite - tab_y0) // lado)

    return Layout(
        ref_x0=ref_x0, ref_y0=ref_y0, ref_larg=ref_larg, ref_alt=ref_alt,
        tab_x0=ref_x0, tab_y0=tab_y0, lado_celula=lado, linhas=linhas,
        y_instrucoes=y_instrucoes, y_chave=y_chave, y_rotulos=y_rotulos,
    )


LAYOUT = calcular_layout()


def _matriz_aruco(id_: int) -> np.ndarray:
    """Bits do marcador como matriz 6x6 (1 = preto).

    Rasteriza grande e amostra o centro de cada módulo, em vez de pedir 6px
    direto: assim não se depende de como o OpenCV arredonda em tamanho mínimo.
    """
    escala = 10
    dicionario = cv2.aruco.getPredefinedDictionary(ARUCO_DICIONARIO)
    img = cv2.aruco.generateImageMarker(dicionario, id_, ARUCO_MODULOS * escala)
    meio = escala // 2
    amostra = img[meio::escala, meio::escala][:ARUCO_MODULOS, :ARUCO_MODULOS]
    return (amostra < 128).astype(int)


def _svg_aruco(id_: int, x: float, y: float, lado: float) -> list[str]:
    """Marcador como quadrados vetoriais — sem raster, sem perda na impressão."""
    modulo = lado / ARUCO_MODULOS
    bits = _matriz_aruco(id_)
    partes = [f'<rect x="{x:.3f}" y="{y:.3f}" width="{lado:.3f}" '
              f'height="{lado:.3f}" fill="#ffffff"/>']
    for i in range(ARUCO_MODULOS):
        for j in range(ARUCO_MODULOS):
            if bits[i, j]:
                # +0,02mm de sobreposição: evita fio branco entre módulos vizinhos
                # quando o rasterizador arredonda para pixel.
                partes.append(
                    f'<rect x="{x + j * modulo:.3f}" y="{y + i * modulo:.3f}" '
                    f'width="{modulo + 0.02:.3f}" height="{modulo + 0.02:.3f}" '
                    f'fill="#000000"/>')
    return partes


def _texto(x, y, s, tam=3.2, cor=COR_TEXTO, peso="normal", ancora="start") -> str:
    escapado = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-family="{FONTE}" '
            f'font-size="{tam:.2f}" fill="{cor}" font-weight="{peso}" '
            f'text-anchor="{ancora}">{escapado}</text>')


def _tracejado(comprimento: float, alvo: float = 1.2) -> tuple[float, str]:
    """Período de traço que cabe INTEIRO em `comprimento`, começando e terminando
    em traço.

    Com k traços e k-1 vãos de mesmo tamanho d, o lado mede (2k-1)*d. Escolhe-se
    o k que deixa d mais perto de `alvo` e resolve-se d exato — assim o canto
    nunca cai no meio de um traço, e como todas as linhas têm a mesma medida, os
    traços de uma linha ficam alinhados com os da linha de baixo.

    Um `stroke-dasharray` fixo num `<rect>` não faz isso: o padrão corre o
    perímetro a partir de um canto, e cada lado começa numa fase diferente.
    """
    k = max(2, round((comprimento / alvo + 1) / 2))
    d = comprimento / (2 * k - 1)
    return d, f"{d:.4f} {d:.4f}"


def _caixa_tracejada(x, y, w, h) -> list[str]:
    """Retângulo tracejado com os traços alinhados nos quatro lados."""
    _, dh = _tracejado(w)
    _, dv = _tracejado(h)
    linha = (lambda x1, y1, x2, y2, dash:
             f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
             f'stroke="{COR_GRADE}" stroke-width="{ESPESSURA_GRADE_MM}" '
             f'stroke-dasharray="{dash}" stroke-linecap="butt"/>')
    return [linha(x, y, x + w, y, dh), linha(x, y + h, x + w, y + h, dh),
            linha(x, y, x, y + h, dv), linha(x + w, y, x + w, y + h, dv)]


def _celulas(x0, y, n, lado, recuo: float = RECUO_CELULA_MM) -> list[str]:
    """`n` caixas no passo `lado`, cada uma recuada `recuo` dentro do seu passo.

    O passo é a geometria; o recuo é só tinta. Ver RECUO_CELULA_MM.
    """
    l = lado - 2 * recuo
    return [f'<rect x="{x0 + k * lado + recuo:.3f}" y="{y + recuo:.3f}" '
            f'width="{l:.3f}" height="{l:.3f}" fill="none" stroke="{COR_GRADE}" '
            f'stroke-width="{ESPESSURA_GRADE_MM}"/>' for k in range(n)]


def validar_chave(chave: str) -> str:
    """Normaliza e confere a chave a imprimir. Vazio = folha em branco.

    A–Z apenas, por D14: é o que deixa a chave ser lida pelo classificador de
    26 classes que já existe, sem trazer de volta as confusões 0/O, 5/S, 2/Z e
    1/I que D08 elimina restringindo o alfabeto na origem.

    Falha alto de propósito. Chave inválida impressa em 500 folhas não tem
    conserto depois — e o modo de falha é o pior possível, porque folha no
    grupo errado não aparece em precisão nem em cobertura (D14).
    """
    chave = chave.strip()
    if not chave:
        return ""
    # Confere ANTES de `upper()`, que transforma ß em SS e ﬁ em FI: a chave impressa
    # deixaria de ser a que foi digitada.
    if not chave.isascii() or not chave.isalpha():
        raise ValueError(f"chave {chave!r}: só A-Z (D14)")
    chave = chave.upper()
    if len(chave) > CELULAS_CHAVE:
        raise ValueError(f"chave {chave!r}: {len(chave)} letras, "
                         f"cabem {CELULAS_CHAVE}")
    return chave


# --- textos editáveis --------------------------------------------------------
# D16: os textos da folha podem mudar de uma impressão para outra porque nada no
# pipeline os lê. O que o sistema lê não tem parâmetro: marcadores, grade, células
# da chave, rótulos e numeração ficam fora de alcance, e `Layout` nem sabe que estes textos
# existem, então trocá-los não tem como mexer em `sheet_cutter.GERADA_V2`. O
# carimbo também fica travado: é ele que diz com qual geometria recortar uma
# pilha de folhas (A43).

# (campo, rótulo, baseline em mm abaixo do topo dos marcadores, corpo nominal,
# corpo mínimo, peso). O corpo mínimo existe para texto comprido falhar alto em
# vez de sair minúsculo em 500 folhas. As baselines deixam folga para maiúscula
# acentuada: com o título em 5,6 mm, o agudo de UNIVERSITÁRIOS passava 0,1 mm do
# topo da área, e foi o portão de `lote_folhas.conferir_cabecalho` que mostrou.
CAMPOS_CABECALHO = (
    ("titulo", "título", 6.2, 6.0, 3.6, "bold"),
    ("subtitulo", "subtítulo", 11.4, 4.0, 2.8, "normal"),
    ("complemento", "complemento", 15.4, 3.2, 2.6, "normal"),
)
CORPO_MINIMO_INSTRUCAO = 2.4
LIMITE_CARACTERES_CABECALHO = 90
# O carimbo mora à direita da instrução. Esta é a largura que fica reservada para
# ele, com folga para uma fonte mais larga que a desta máquina.
RESERVA_CARIMBO_MM = 26.0


@dataclass(frozen=True)
class Cabecalho:
    """Os textos editáveis da folha. Sem campo de geometria, de propósito."""
    titulo: str = "FOLHA DE PRESENÇA"
    subtitulo: str = ""
    complemento: str = ""
    instrucao: str = INSTRUCOES[0]


def area_cabecalho(layout: Layout = LAYOUT) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) em mm: a faixa do título, entre os marcadores de cima.

    Fora da zona de silêncio deles, com 2 mm de folga (A30), e acima da instrução.
    """
    silencio = ARUCO_SILENCIO_MODULOS * (ARUCO_LADO_MM / ARUCO_MODULOS)
    recuo = ARUCO_LADO_MM + silencio + 2.0
    return (layout.ref_x0 + recuo, layout.ref_y0,
            layout.ref_x0 + layout.ref_larg - recuo, layout.y_instrucoes - 1.0)


def _x_data(layout: Layout) -> float:
    """Início da banda DATA, alinhada à direita da tabela."""
    separador = SEPARADOR_DATA_CELULAS * layout.lado_celula
    larg_data = (CELULAS_DATA * layout.lado_celula
                 + (len(GRUPOS_DATA) - 1) * separador)
    return layout.tab_x0 + layout.tab_larg - larg_data


def areas_editaveis(layout: Layout = LAYOUT
                    ) -> list[tuple[float, float, float, float]]:
    """Os retângulos, em mm, onde a folha pode diferir da v4 congelada.

    Fora deles fica o que o sistema lê, mais os rótulos e o carimbo, e o portão
    de `lote_folhas` confere isso por pixel.
    """
    L = layout
    x0, x1 = L.ref_x0 - 0.5, L.ref_x0 + L.ref_larg + 0.5
    fim_instrucao = L.y_instrucoes + 5.0
    fim_banda = L.y_chave + L.lado_celula + ESPESSURA_GRADE_MM / 2 + 0.05
    return [
        area_cabecalho(L),
        (x0, L.y_instrucoes, x1 - RESERVA_CARIMBO_MM, fim_instrucao),
        (_x_data(L) - 0.5, fim_instrucao, x1, fim_banda),  # a banda DATA, removida
    ]


@lru_cache(maxsize=512)
def _largura_por_corpo(texto: str, peso: str) -> float:
    """Largura da tinta em mm para cada 1 mm de corpo, medida no renderizador.

    Medida em vez de tabelada porque a fonte depende da máquina: `FONTE` pede
    DejaVu e, sem ela, o fontconfig entrega outra, de largura diferente. O
    número vem do mesmo `rsvg-convert` que desenha a folha, com a mesma fonte.
    """
    corpo, dpi = 20.0, 100
    largura = corpo * (len(texto) + 2)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}mm" '
           f'height="40mm" viewBox="0 0 {largura} 40">'
           f'<rect width="{largura}" height="40" fill="#ffffff"/>'
           f'{_texto(corpo / 2, 28, texto, corpo, "#000000", peso)}</svg>')
    png = subprocess.run(["rsvg-convert", "--format=png", f"--dpi-x={dpi}",
                          f"--dpi-y={dpi}"], input=svg.encode("utf-8"),
                         capture_output=True, check=True).stdout
    cinza = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    colunas = np.flatnonzero((cinza < 128).any(axis=0))
    if colunas.size == 0:
        return 0.0
    return float(colunas[-1] - colunas[0] + 1) * 25.4 / dpi / corpo


def _limpar(texto: str, rotulo: str, exigido: bool = False) -> str:
    """Normaliza o espaço e recusa o que não pode ir para o papel."""
    texto = " ".join(texto.split())
    if exigido and not texto:
        raise ValueError(f"{rotulo}: não pode ficar vazio")
    if len(texto) > LIMITE_CARACTERES_CABECALHO:
        raise ValueError(f"{rotulo}: {len(texto)} caracteres, o limite é "
                         f"{LIMITE_CARACTERES_CABECALHO}")
    if not texto.isprintable():
        raise ValueError(f"{rotulo}: tem caractere que não dá para imprimir")
    return texto


def _corpo(texto: str, rotulo: str, nominal: float, minimo: float, peso: str,
           largura: float) -> float:
    """Encolhe o texto até caber; passando do mínimo, falha alto."""
    por_corpo = _largura_por_corpo(texto, peso)
    # 2% de folga: a medida tem a resolução de um pixel a 100 dpi
    corpo = min(nominal, largura * 0.98 / por_corpo) if por_corpo else nominal
    if corpo < minimo:
        raise ValueError(f"{rotulo}: não cabe na folha, mesmo no menor tamanho. "
                         f"Encurte o texto")
    return corpo


def _svg_titulo(cabecalho: Cabecalho, layout: Layout) -> list[str]:
    x0, _, x1, _ = area_cabecalho(layout)
    saida = []
    for campo, rotulo, dy, nominal, minimo, peso in CAMPOS_CABECALHO:
        texto = _limpar(getattr(cabecalho, campo), rotulo, campo == "titulo")
        if texto:
            corpo = _corpo(texto, rotulo, nominal, minimo, peso, x1 - x0)
            saida.append(_texto((x0 + x1) / 2, layout.ref_y0 + dy, texto, corpo,
                                COR_TEXTO, peso, "middle"))
    return saida


def _svg_instrucao(cabecalho: Cabecalho, layout: Layout, y: float) -> list[str]:
    """A instrução ao participante. Vazia é legítima: a linha simplesmente some."""
    texto = _limpar(cabecalho.instrucao, "instrução")
    if not texto:
        return []
    corpo = _corpo(texto, "instrução", 3.1, CORPO_MINIMO_INSTRUCAO, "normal",
                   layout.ref_larg - RESERVA_CARIMBO_MM)
    return [_texto(layout.ref_x0, y, texto, corpo, COR_ROTULO)]


def desenhar_svg(layout: Layout = LAYOUT, chave: str = "",
                cabecalho: Cabecalho | None = None) -> str:
    """Monta o SVG da folha.

    `chave` vazia deixa as células da banda em branco, para alguém escrever à
    mão — que é como a m7_01 e a m7_02 foram rotuladas. Preenchida, a folha sai
    da impressora já pertencendo a um evento. As duas formas convivem por
    decisão do autor em 30/08: nem todo evento é cadastrado antes.

    `cabecalho` None mantém a folha v4 congelada, byte a byte (D11). Dado, ele
    troca os textos e tira a banda DATA, que nada lê (D15, D16).
    """
    L = layout
    lado = L.lado_celula
    chave = validar_chave(chave)
    p: list[str] = []

    x_dir = L.ref_x0 + L.ref_larg - ARUCO_LADO_MM
    y_bai = L.ref_y0 + L.ref_alt - ARUCO_LADO_MM
    for id_, (mx, my) in {0: (L.ref_x0, L.ref_y0), 1: (x_dir, L.ref_y0),
                          2: (L.ref_x0, y_bai), 3: (x_dir, y_bai)}.items():
        p += _svg_aruco(id_, mx, my, ARUCO_LADO_MM)

    # título entre os marcadores de cima: aproveita a faixa que eles já ocupam, sem
    # gastar altura de tabela
    centro = L.ref_x0 + L.ref_larg / 2
    if cabecalho is None:
        p.append(_texto(centro, L.ref_y0 + 7.5, "FOLHA DE PRESENÇA", 6.0,
                        COR_TEXTO, "bold", "middle"))
    else:
        p += _svg_titulo(cabecalho, L)

    # carimbo de versão, na sobra à direita da instrução: não custa linha de
    # tabela, e é o que torna uma pilha de folhas identificável depois
    p.append(_texto(L.ref_x0 + L.ref_larg, L.y_instrucoes + 3.0, VERSAO_FOLHA,
                    2.6, COR_ROTULO, "normal", "end"))

    y = L.y_instrucoes + 3.0
    for linha in INSTRUCOES:
        if cabecalho is None:
            p.append(_texto(L.ref_x0, y, linha, 3.1, COR_ROTULO))
        else:
            p += _svg_instrucao(cabecalho, L, y)
        y += 4.2

    # banda CHAVE + DATA, nas mesmas células quadradas. As duas bandas começam
    # onde a coluna de MATRÍCULA começa: o rótulo fica ACIMA das células, não à
    # esquerda, então não há o que recuar — o recuo de 11mm que existia aqui só
    # desalinhava a CHAVE da grade, e a DATA logo abaixo já fazia o certo.
    x_chave = L.tab_x0
    p.append(_texto(x_chave, L.y_chave - 1.2, "CHAVE", 3.2, COR_ROTULO, "bold"))
    p += _celulas(x_chave, L.y_chave, CELULAS_CHAVE, lado)
    # Uma letra por célula, centrada — a mesma convenção que o participante
    # segue na grade, para que a célula impressa chegue ao reconhecedor com a
    # mesma forma da manuscrita. Traço grosso pelo mesmo motivo: caneta tem
    # corpo, e o filtro `erase_small_regions` come componente fino.
    for k, letra in enumerate(chave):
        p.append(_texto(x_chave + (k + 0.5) * lado, L.y_chave + lado * 0.78,
                        letra, lado * 0.68, COR_TEXTO, "bold", "middle"))
    # A DATA sai agrupada como a pessoa já sabe escrever data: DD / MM / AAAA.
    # As barras não são célula, são só desenho entre os grupos — a contagem de
    # células (CELULAS_DATA) não muda, e nada aqui é campo reconhecível.
    # alinhada à DIREITA da tabela, isto é, ao fim da coluna de rubrica: a banda
    # de cima passa a fechar no mesmo prumo que a grade, dos dois lados.
    #
    # Só existe na folha congelada. Quem imprime por cabeçalho não leva a banda:
    # o evento cadastrado já diz a data, e nada a lê (D15, D16).
    if cabecalho is None:
        separador = SEPARADOR_DATA_CELULAS * lado
        x_data = _x_data(L)
        p.append(_texto(x_data, L.y_chave - 1.2, "DATA", 3.2, COR_ROTULO, "bold"))
        x = x_data
        for k, n in enumerate(GRUPOS_DATA):
            p += _celulas(x, L.y_chave, n, lado)
            x += n * lado
            if k < len(GRUPOS_DATA) - 1:
                p.append(_texto(x + separador / 2, L.y_chave + lado * 0.72,
                                "/", 3.8, COR_ROTULO, "bold", "middle"))
                x += separador

    # rótulos das colunas
    p.append(_texto(L.tab_x0, L.y_rotulos + 4.0, "MATRÍCULA", 3.4, COR_ROTULO, "bold"))
    p.append(_texto(L.x_nome, L.y_rotulos + 4.0, "NOME E SOBRENOME", 3.4,
                    COR_ROTULO, "bold"))
    p.append(_texto(L.x_rubrica, L.y_rotulos + 4.0, "RUBRICA", 3.4,
                    COR_ROTULO, "bold"))

    # grade
    for i in range(L.linhas):
        y_l = L.tab_y0 + i * lado
        p += _celulas(L.tab_x0, y_l, CELULAS_MATRICULA, lado)
        p.append(_texto(L.x_numeracao + lado / 2, y_l + lado / 2 + 1.0,
                        f"{i + 1:02d}", 2.6, COR_ROTULO, "normal", "middle"))
        p += _celulas(L.x_nome, y_l, CELULAS_NOME, lado)
        # a rubrica é UMA caixa, tracejada: sem divisões para ninguém tentar uma
        # letra por quadrado, e visualmente distinta da grade que é reconhecida
        # recuada como as células, senão a borda dela encosta na borda da última
        # célula de nome e as duas verticais ficam a 0,35mm uma da outra
        p += _caixa_tracejada(L.x_rubrica + RECUO_CELULA_MM,
                              y_l + RECUO_CELULA_MM,
                              L.larg_rubrica - 2 * RECUO_CELULA_MM,
                              lado - 2 * RECUO_CELULA_MM)

    corpo = "\n".join(p)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{A4_LARGURA_MM}mm" height="{A4_ALTURA_MM}mm" '
            f'viewBox="0 0 {A4_LARGURA_MM} {A4_ALTURA_MM}">\n'
            f'<rect width="{A4_LARGURA_MM}" height="{A4_ALTURA_MM}" fill="#ffffff"/>\n'
            f'{corpo}\n</svg>\n')


def gerar(caminho_pdf: str, chave: str = "",
          cabecalho: Cabecalho | None = None) -> str:
    """Escreve o SVG ao lado e converte para PDF com rsvg-convert.

    O programa recebe a pasta em `cwd` e só o nome do arquivo: no Windows, um
    caminho com acento (`C:\\Users\\João\\...`) não chega inteiro a todo programa.
    """
    pasta, nome_pdf = os.path.split(os.path.abspath(caminho_pdf))
    nome_svg = os.path.splitext(nome_pdf)[0] + ".svg"
    with open(os.path.join(pasta, nome_svg), "w", encoding="utf-8") as f:
        f.write(desenhar_svg(chave=chave, cabecalho=cabecalho))
    subprocess.run(["rsvg-convert", "--format=pdf", "--output", nome_pdf, nome_svg],
                   cwd=pasta, check=True)
    return caminho_pdf


PASTA_SAIDA = "dados"
PADRAO_SAIDA = "folha_gerada"


def resolver_saida(alvo: str) -> str:
    """Nome curto ou caminho -> caminho do PDF a escrever.

        gerar_folha.py folha1        -> <este diretório>/dados/folha1.pdf
        gerar_folha.py /tmp/x.pdf    -> /tmp/x.pdf

    O nome curto resolve RELATIVO AO MÓDULO, não ao diretório de trabalho. Sem
    isso, `python experimento-imagem/gerar_folha.py` rodado da raiz do repo
    criava um `dados/` novo na raiz e escrevia a folha lá — não falhava, só
    escrevia no lugar errado, que é a pior das duas coisas.
    """
    if os.sep in alvo or alvo.endswith(".pdf"):
        return alvo if alvo.endswith(".pdf") else alvo + ".pdf"
    aqui = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(aqui, PASTA_SAIDA, alvo + ".pdf")


def conferir_deteccao(caminho_pdf: str, dpis=(150, 200, 300, 400, 600)) -> dict:
    """Rasteriza o PDF e roda o MESMO detector do align.py em cada resolução.

    Existe porque a folha anterior detectava em 1 de 4 resoluções e ninguém
    tinha conferido. Uma folha que não passa aqui não vai para a impressora.
    """
    import tempfile
    import glob
    import os

    dicionario = cv2.aruco.getPredefinedDictionary(ARUCO_DICIONARIO)
    detector = cv2.aruco.ArucoDetector(dicionario, cv2.aruco.DetectorParameters())
    resultado = {}
    pasta, nome_pdf = os.path.split(os.path.abspath(caminho_pdf))
    with tempfile.TemporaryDirectory(dir=pasta) as tmp:
        for dpi in dpis:
            base = os.path.join(os.path.basename(tmp), f"r{dpi}")
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile",
                            nome_pdf, base], cwd=pasta, check=True)
            # `cv2.imread` não abre caminho com acento no Windows, e devolve None
            # em vez de erro. Quem lê o arquivo é o Python.
            png = np.fromfile(os.path.join(pasta, base + ".png"), np.uint8)
            img = cv2.imdecode(png, cv2.IMREAD_GRAYSCALE)
            _, ids, _ = detector.detectMarkers(img)
            achados = sorted(ids.flatten().tolist()) if ids is not None else []
            resultado[dpi] = achados
    return resultado


if __name__ == "__main__":
    import os
    import sys

    L = LAYOUT
    print("=== geometria derivada ===")
    print(f"área morta {AREA_MORTA_MM}mm + segurança {MARGEM_SEGURANCA_MM}mm")
    print(f"retângulo dos marcadores: {L.ref_larg:.2f} x {L.ref_alt:.2f} mm")
    print(f"célula QUADRADA de {L.lado_celula:.3f} mm")
    print(f"tabela: {L.colunas} colunas ({CELULAS_MATRICULA} matrícula + "
          f"{COLUNAS_NUMERACAO} numeração + {CELULAS_NOME} nome + "
          f"{COLUNAS_RUBRICA} rubrica) x {L.linhas} linhas")
    print(f"tabela: {L.tab_larg:.2f} x {L.linhas * L.lado_celula:.2f} mm")
    print(f"marcador: {ARUCO_LADO_MM}mm, zona de silêncio "
          f"{ARUCO_SILENCIO_MODULOS * ARUCO_LADO_MM / ARUCO_MODULOS:.1f}mm")
    print()
    print("=== para o SheetCutter ===")
    print(f"  MATRICULA = {L.coluna_matricula}")
    print(f"  NOME      = {L.coluna_nome}")
    print(f"  FOLHA     = {L.folha}")
    print()

    caminho = resolver_saida(sys.argv[1] if len(sys.argv) > 1 else PADRAO_SAIDA)
    # Segundo posicional: a chave a imprimir. Sem ela a banda sai em branco,
    # que é a folha de estoque — o professor imprime 100 e rotula no dia.
    chave = validar_chave(sys.argv[2] if len(sys.argv) > 2 else "")
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    gerar(caminho, chave)
    if chave:
        print(f"chave impressa: {chave} ({len(chave)}/{CELULAS_CHAVE} células)")
    deteccao = conferir_deteccao(caminho)
    ok = all(set(v) >= {0, 1, 2, 3} for v in deteccao.values())
    print(f"gerado: {caminho}")
    for dpi, ids in deteccao.items():
        print(f"   {dpi:4d} dpi -> {ids if ids else 'NADA'}")
    print(f"   deteccao dos 4 marcadores em todas as resolucoes: "
          f"{'SIM' if ok else 'NAO'}")
    # A folha só sai se detectar. Ver o achado sobre a folha de 24/08: a
    # anterior detectava em 1 de 4 resolucoes e ninguem tinha conferido.
    if not ok:
        sys.exit("ABORTADO: folha que nao detecta nao vai para a impressora.")
