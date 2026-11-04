"""Esqueleto ponta a ponta: folha escaneada -> pessoa identificada -> JSON.

É a primeira vez que as duas metades do projeto se tocam. Até aqui,
`experimento-imagem/` parava na string lida e `experimento-fuzzy/` partia de
strings simuladas. Aqui a saída do KNN entra em `identificar()`.

O entregável é O FLUXO EXISTIR. A acurácia é o que for — n=40 não estabelece
taxa nenhuma (com ~20 aceites, a regra de três dá teto de erro perto de 15%).
A taxa de verdade vem da coleta dos Encontros.

## As duas variantes

Roda as duas na mesma passada, porque pontuar é barato (19,6 ms por
identificação na base de 1.334) e o pipeline de imagem é que custa:

  chuta  — argmax sempre. É o reconhecedor de hoje.
  abstem — emite o dígito só se os k vizinhos forem unânimes; senão '?'.

É a replicação do Experimento 01 (A01) com reconhecedor REAL no lugar do
simulador. Lá o '?' caía exatamente onde o erro estava, porque o simulador
sabia a verdade; aqui quem decide abster é a confiança do KNN — e A11 mostrou
que ela não separa acerto de erro. Este experimento mede o preço disso.

## alpha virou eixo

Enquanto o classificador de letras não existia, alpha=1,0 era a única leitura
honesta: com nome vazio o Jaro-Winkler dá 0 para todo candidato, o teto do
score vira 0,7 e tudo cai em quarentena por construção — mediria o alpha, não o
reconhecedor.

Agora as duas colunas são lidas, e alpha vira eixo do experimento. Roda-se 1,0
(só matrícula, como A13/A14/A15 foram medidos) e 0,7 (o D01) na mesma passada,
sobre exatamente as mesmas leituras. A diferença entre as duas linhas é o que o
canal do nome recupera — que é a pergunta que A15 deixou em aberto ao mostrar
que 62,6% da base tem vizinho a um dígito, indetectável só pela matrícula.

## Anulação de linha

Célula preenchida por inteiro anula a linha: a pessoa errou e reescreveu
embaixo. A linha anulada não vai ao matcher, mas fica registrada com
`decisao: "anulada"`. Ver LIMIAR_ANULACAO em `process.py`.

Saída em dados/, que o git ignora: contém matrícula e nome reais (LGPD).

Uso:
    python identificar_folha.py
    python identificar_folha.py --folha m3_2
"""

from __future__ import annotations
from dataclasses import dataclass, field
import argparse
import json
import os
import sys
import time

from image import Image
from align import Align
from ocr import OCR
from region import Decomposer
from sheet_cutter import SheetCutter
from process import (PASTA_CELULAS, folha as modelo_folha, carregar_gabarito,
                     celula_anulada)

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base                     # noqa: E402
from normalizacao import normalizar                       # noqa: E402
from fuzzy import (sim_levenshtein, identificar, SIM_NOME,     # noqa: E402
                   METRICA_NOME, ALPHA, THETA, DELTA)

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
SAIDA = "dados/identificacao_folhas.json"

# theta, delta, alpha e a métrica do nome vêm do `fuzzy`, que é onde o ponto de
# operação vigente mora. Hardcodar aqui foi o que deixou este módulo rodando
# theta=0,85 (calibrado no simulador do TCC 1) por três sessões depois de o
# pipeline real medir 0,60.

# alpha vira EIXO do experimento, não constante. Com o classificador de letras
# ligado, a pergunta que este experimento passa a responder é justamente quanto
# o segundo canal recupera:
#   1,0 = só matrícula. É como A13, A14 e A15 foram medidos, quando o nome não
#         era lido. Fica como linha de base para a comparação ser honesta.
#   0,7 = o D01, com o nome entrando de fato.
# theta e delta podem ser varridos depois sobre o JSON salvo; alpha não, porque
# muda o score — então tem que ser gravado em cada registro. A métrica do nome
# tampouco: ela também muda o score, e é por isso que ela entra como eixo aqui
# em vez de ficar escondida dentro do matcher.
ALPHAS = (1.0, ALPHA)
MODOS = ("chuta", "abstem")

# O corte acima do qual o caractere é emitido; abaixo dele vira '?'.
#
# Para o KNN, 0,999 significa "os k vizinhos foram unânimes" — não é escolha de
# projeto, é o único corte que a confiança de k vizinhos oferece (A11, A20).
#
# Para a CNN o corte é contínuo e pode ser calibrado. 0,95 vem da varredura
# sobre as 304 células da m3_2, onde entrega cobertura 0,678 com precisão 0,947
# — melhor que o KNN nas DUAS dimensões (0,592 e 0,939).
UNANIME = 0.999
CORTE_CNN = 0.95

# Folhas com gabarito, separadas pela PROCEDÊNCIA da verdade — o critério que
# A25 fixou e que `avaliar.ALVOS` já aplica desde 27/08.
#
#   verificadas   o rótulo veio de fonte que NÃO é a própria imagem: lista de
#                 participantes da m3_2 (22/22) e as identidades da m4_01 (7) e
#                 da m7_02 (16), confirmadas pelo autor. Sustentam TAXA.
#   transcritas   o gabarito foi lido da imagem, que é a mesma fonte que o
#                 sistema usa. Servem para mecanismo e direção, nunca para taxa.
#
# As duas listas existem porque os números do vault até 27/08 (36 aceitos,
# cobertura 0,900) foram medidos no conjunto de 40 linhas que MISTURA as duas.
# Trocar a lista em silêncio tornaria os números novos incomparáveis com os
# velhos sem que ninguém notasse.
FOLHAS_VERIFICADAS = [("m3_2", 3), ("m4_01", "gerada_v1"), ("m7_02", "gerada_v2")]
FOLHAS_TRANSCRITAS = [("m2-1", 2), ("m3_1", 3)]

# Gabarito construído em 30/08 pela revisão do autor na própria interface de
# quarentena — uso invertido dela: em vez de conferir uma leitura contra uma
# verdade existente, o export da revisão VIRA a verdade.
#
# **São VERIFICADAS**: o autor identificou as pessoas por reconhecimento, não
# decodificando a letra da imagem. É a mesma procedência das 7 identidades da
# m4_01, que já contam como verificadas desde 27/08 — isto aplica o critério
# existente, não cria um novo.
#
# Ficam em lista separada mesmo assim, e a razão é reprodutibilidade: os números
# do vault até 30/08 (44 aceitos, cobertura 0,978) foram medidos nas 45 linhas de
# `verificado`, e juntar tudo numa lista só tornaria os números velhos
# irrecuperáveis. `ampliado` é o conjunto que sustenta taxa daqui em diante.
#
# Todas as três são `gerada_v2` — as versões v2, v3 e v4 da folha dividem a
# mesma geometria, e só diferem em tinta (ver [[Folhas - linhagem das versoes]]).
FOLHAS_REVISADAS = [("m5_01", "gerada_v2"), ("m6_01", "gerada_v2"),
                    ("m7_01", "gerada_v2")]
CONJUNTOS = {
    # as 45 linhas de 28/08, mantidas para os números do vault reproduzirem
    "verificado": FOLHAS_VERIFICADAS,
    "historico": [("m3_2", 3), ("m2-1", 2), ("m3_1", 3)],
    "todas": FOLHAS_VERIFICADAS + FOLHAS_TRANSCRITAS,
    "revisado": FOLHAS_REVISADAS,
    # 67 linhas, todas com verdade independente — É O CONJUNTO QUE SUSTENTA TAXA
    "ampliado": FOLHAS_VERIFICADAS + FOLHAS_REVISADAS,
}
FOLHAS_GABARITO = CONJUNTOS["ampliado"]

# Folha -> modelo de recorte, para quem precisa do mapa e não da ordem. É a
# única fonte: `quarentena.py` mantinha uma cópia com fallback para o modelo 3,
# que nas folhas geradas recortaria com a geometria errada SEM ERRAR — o modo
# de falha que [[Folhas - linhagem das versoes]] existe para evitar.
MODELO_DA_FOLHA = dict(CONJUNTOS["todas"] + FOLHAS_REVISADAS)

ABSTENCAO = "?"

# Células ocupadas que um campo precisa ter para a linha contar como preenchida.
# Ver `Linha.em_branco`: linha real tem 6 na matrícula e 15+ no nome, risco
# perdido deixa 1. Três é longe dos dois, e ainda admite quem escreveu só a
# matrícula ou só um nome curto.
MINIMO_OCUPACAO = 3


@dataclass
class Celula:
    """Uma célula já reconhecida, de qualquer uma das duas colunas."""
    caractere: str | None   # None = não houve caractere para emitir
    confianca: float
    motivo: str             # 'lido' | 'vazia' | 'sem_caixa'


@dataclass
class Linha:
    folha: str
    numero: int
    celulas: list[Celula] = field(default_factory=list)       # matrícula
    celulas_nome: list[Celula] = field(default_factory=list)  # nome
    # corte de confiança POR CAMPO, conforme o reconhecedor de cada um: 0,999
    # para o KNN (o único que k vizinhos oferecem) e 0,95 para a CNN, que é
    # contínua. São dois porque D08 põe um classificador em cada campo, e nada
    # obriga os dois a serem do mesmo tipo — durante a migração para a CNN dos
    # dígitos o pipeline rodou justamente com um de cada.
    corte_nome: float = UNANIME
    corte_matricula: float = UNANIME

    def leitura(self, abster: bool) -> str:
        """A matrícula que vai para o matcher, sob cada política.

        Célula vazia ou perdida vira '?' nas DUAS variantes: não houve argmax
        para tomar, então nem chutar é possível ali. A diferença entre as
        variantes é só o que se faz com um voto não unânime.
        """
        out = []
        for c in self.celulas:
            if c.caractere is None:
                out.append(ABSTENCAO)
            elif abster and c.confianca < self.corte_matricula:
                out.append(ABSTENCAO)
            else:
                out.append(c.caractere)
        return "".join(out)

    def leitura_nome(self, abster: bool) -> str:
        """O nome que vai para o matcher. A semântica do vazio MUDA aqui.

        Na matrícula, célula vazia é ilegibilidade — vira '?'. No nome, não:
        as seis células da matrícula são todas obrigatórias, mas as 18 do nome
        não são. Uma célula vazia no meio do nome é o SEPARADOR entre palavras,
        e depois da última letra é só o fim da string.

        Confundir as duas coisas seria caro nos dois sentidos: tratar o
        separador como '?' inventaria um caractere que ninguém escreveu, e
        tratar como '?' as células além do fim encheria a leitura de curingas
        que, sob a regra de deleção do D02, sumiriam de qualquer forma — mas
        depois de já terem custado o percurso.

        A leitura para nesta interpretação porque ela foi conferida: a máscara
        de ocupação bateu 522/522 células e 29/29 linhas contra o gabarito, com
        os separadores nas posições certas.
        """
        fim = max((i for i, c in enumerate(self.celulas_nome)
                   if c.motivo != "vazia"), default=-1)
        out = []
        for c in self.celulas_nome[:fim + 1]:
            if c.motivo == "vazia":
                out.append(" ")                     # separador entre palavras
            elif c.caractere is None:
                out.append(ABSTENCAO)               # tinha tinta, nada legível
            elif abster and c.confianca < self.corte_nome:
                out.append(ABSTENCAO)
            else:
                out.append(c.caractere)
        # D02/D05: colapsar espaços depois de qualquer remoção de curinga
        return " ".join("".join(out).split())

    @property
    def em_branco(self) -> bool:
        """Linha que ninguém preencheu — ausência, não ilegibilidade.

        Não basta "toda célula vazia": um risco perdido atravessando a grade
        deixa UMA célula com tinta, e a linha inteira passava a ir ao matcher,
        virando um caractere solto que sempre cai em quarentena. Aconteceu nas
        quatro folhas do modelo gerado (m4_01/27, m5_01/23, m6_01/23, m7_01/06 e
        /23), e na m7_01 dá para ver a diagonal no papel cruzando as linhas
        vazias.

        A separação é folgada: linha preenchida tem 15 a 17 células de nome
        ocupadas e 6 de matrícula; o risco deixa 1. `MINIMO_OCUPACAO` corta bem
        longe das duas.

        A regra olha os CAMPOS separadamente, e não o total, para não descartar
        quem preencheu só um deles — matrícula sem nome, ou um nome muito curto.
        """
        uteis = lambda cs: sum(c.motivo != "vazia" for c in cs)
        return (uteis(self.celulas) < MINIMO_OCUPACAO
                and uteis(self.celulas_nome) < MINIMO_OCUPACAO)

    @property
    def anulada(self) -> bool:
        """A pessoa pintou uma célula por inteiro para descartar a linha.

        Basta UMA célula, em qualquer das duas colunas: quem erra o nome
        rabisca sobre o nome, quem erra a matrícula rabisca a matrícula. Ver
        LIMIAR_ANULACAO em `process.py` para a medição que sustenta o limiar.
        """
        return any(c.motivo == "anulada"
                   for c in (*self.celulas, *self.celulas_nome))


@dataclass
class Chave:
    """A CHAVE da banda do cabeçalho — o rótulo do evento a que a folha pertence.

    Não é `Linha` porque não é linha da grade: são 16 células numa faixa só,
    acima da tabela, e ninguém aqui é participante. É o único eixo de
    agrupamento do sistema — decisão do autor em 30/08: tudo se pendura na
    chave, nada na data. A banda DATA continua impressa e não é lida.
    """
    celulas: list[Celula] = field(default_factory=list)
    corte: float = CORTE_CNN

    @property
    def texto(self) -> str:
        """A chave que vai ao casamento. Sempre abstém — e isso não é um eixo.

        Na matrícula e no nome, chutar × abster é EIXO do experimento (A01,
        A13, A36). Aqui não é, e a razão é assimetria de custo: sob o
        Levenshtein com wildcard o '?' custa zero (D02), então abster nunca
        afasta a chave certa; mas chutar uma letra errada pode aproximá-la de
        OUTRA chave válida e mandar a folha para o grupo errado — que é o erro
        que D14 registra como invisível para precisão e cobertura. Abster
        domina por construção, então não há o que varrer.

        A semântica do vazio é a da MATRÍCULA, não a do nome. No nome, célula
        vazia no meio é separador entre palavras; a chave é uma palavra só
        (D14), então buraco no meio dela é ilegibilidade e vira '?'. Depois da
        última célula com tinta é só o fim da chave.
        """
        fim = max((i for i, c in enumerate(self.celulas) if c.motivo != "vazia"),
                  default=-1)
        return "".join(
            c.caractere if c.caractere is not None and c.confianca >= self.corte
            else ABSTENCAO
            for c in self.celulas[:fim + 1])

    @property
    def com_tinta(self) -> int:
        return sum(c.motivo != "vazia" for c in self.celulas)

    @property
    def em_branco(self) -> bool:
        """Folha que ninguém rotulou — ausência, não ilegibilidade.

        Não usa `MINIMO_OCUPACAO`: aquele limiar existe para separar linha
        preenchida de risco perdido atravessando a grade, e a banda da chave
        fica acima da tabela, fora do caminho do risco. Uma célula com tinta
        aqui é tentativa de chave, e mandá-la para a bandeja é o certo.
        """
        return self.com_tinta == 0


def _reconhecer(celulas: list[Image], ocr: OCR) -> list[Celula]:
    """Binariza, filtra e classifica as células de uma coluna, em duas fases.

    A separação existe por custo: classificar uma célula por vez contra as 151k
    referências do EMNIST dá 204 ms cada; em lote, 3,0 ms. Então a primeira
    fase decide o que precisa de classificador, e a segunda chama o KNN uma vez
    só para todas — ver `OCR.prever_lote`.
    """
    saida: list[Celula | None] = []
    pendentes, posicoes = [], []

    for i, celula in enumerate(celulas):
        img = celula.binarize(gaussian=False).inversion().cut_borders(2)

        # a anulação é decidida antes do filtro de componentes conexos: um bloco
        # pintado não é resíduo de grade, e não deve depender de sobreviver a ele
        if celula_anulada(img):
            saida.append(Celula(None, 1.0, "anulada"))
            continue

        filtrada = Decomposer(img).erase_small_regions(0.2).erase_regions_outside_bounds()
        if filtrada.get_image().is_empty():
            saida.append(Celula(None, 0.0, "vazia"))
            continue

        saida.append(None)          # preenchida na segunda fase
        pendentes.append(filtrada)
        posicoes.append(i)

    for pos, pred in zip(posicoes, ocr.prever_lote(pendentes)):
        saida[pos] = (Celula(None, 0.0, "sem_caixa") if pred is None
                      else Celula(pred.caractere, pred.confianca, "lido"))

    # falhar alto: filtrar os None encolheria a linha em silêncio e desalinharia
    # a leitura com as posições da grade.
    assert all(c is not None for c in saida), "célula sem classificação"
    return saida  # type: ignore[return-value]


def ler_linha(celulas: list[Image], ocr: OCR, folha: str, numero: int,
              celulas_nome: list[Image] | None = None,
              ocr_letras: OCR | None = None,
              corte_nome: float = UNANIME,
              corte_matricula: float = UNANIME) -> Linha:
    """Reconhece a matrícula e, se houver classificador de letras, o nome."""
    linha = Linha(folha=folha, numero=numero, corte_nome=corte_nome,
                  corte_matricula=corte_matricula)
    linha.celulas = _reconhecer(celulas, ocr)
    if celulas_nome is not None and ocr_letras is not None:
        linha.celulas_nome = _reconhecer(celulas_nome, ocr_letras)
    return linha


def ler_chave(alinhada: Image, cortador: SheetCutter, folha_modelo,
              ocr_letras: OCR, corte: float = CORTE_CNN) -> Chave | None:
    """Lê a banda CHAVE. `None` quando a folha não tem uma.

    Devolver `None` em vez de erro é o que separa "folha sem chave" de "chave
    ilegível": as folhas anteriores à v2 não têm a banda impressa, e
    `ModeloFolha.bandas` vazio diz exatamente isso. Uma delas não pode aparecer
    como chave em branco, senão vai para a bandeja de folha sem evento junto
    com as que têm a banda e não foram preenchidas — que é outro problema.

    Reusa `_reconhecer`, o mesmo caminho de filtro da célula da grade, porque a
    célula da chave sai a 43,9 px no warp, praticamente a do domínio em que o
    reconhecedor foi medido (A44) — é isso que dispensa classificador novo.

    Efeito colateral herdado, e ele é inofensivo aqui: `_reconhecer` aplica a
    regra de anulação do D09, então célula da chave pintada por inteiro sai com
    motivo 'anulada'. Ela vira '?' e nada mais — `Linha.anulada` só olha as
    células da grade, então nenhuma linha é anulada por causa da chave.
    """
    if "chave" not in folha_modelo.bandas:
        return None
    celulas = cortador.cut_banda(alinhada, folha_modelo, "chave")
    return Chave(celulas=_reconhecer(celulas, ocr_letras), corte=corte)


def ranquear(matricula_lida: str, nome_lido: str, base: list[dict],
             alpha: float, n: int = 3, sim_nome=None) -> list[tuple[float, dict]]:
    """Pontua a base inteira e devolve os n melhores.

    `identificar()` só devolve o campeão; a interface de revisão de quarentena
    precisa dos candidatos ranqueados (`29467?` é compatível com 10 pessoas).
    O gate é reaplicado aqui e conferido contra `identificar()` em `main`.

    `sim_nome` vem de `fuzzy.SIM_NOME` e já carrega a semântica do '?' junto —
    ver o cabeçalho do `fuzzy`. Não montar o par aqui.
    """
    if sim_nome is None:
        sim_nome = SIM_NOME[METRICA_NOME]
    pontuados = []
    for aluno in base:
        s_mat = sim_levenshtein(matricula_lida, aluno["matricula"])
        s_nome = sim_nome(nome_lido, aluno["nome"]) if alpha < 1.0 else 0.0
        pontuados.append((alpha * s_mat + (1 - alpha) * s_nome, aluno))
    pontuados.sort(key=lambda t: -t[0])
    return pontuados[:n]


def avaliar_linha(linha: Linha, modo: str, alpha: float, base: list[dict],
                  verdade: str | None, na_base: bool, sim_nome=None) -> dict:
    """Um registro cru, no contrato que `analise_limiar.reaplicar_gate` lê."""
    abster = modo == "abstem"
    lida = linha.leitura(abster)
    nome_lido = linha.leitura_nome(abster)
    topo = ranquear(lida, nome_lido, base, alpha, sim_nome=sim_nome)

    score = topo[0][0]
    margem = score - topo[1][0] if len(topo) > 1 else score
    aceito = score >= THETA and margem >= DELTA
    campeao = topo[0][1]

    return {
        "folha": linha.folha,
        "linha": linha.numero,
        "modo": modo,
        "alpha": alpha,
        "leitura": lida,
        "leitura_nome": nome_lido,
        "verdade": verdade,
        "verdade_na_base": na_base,
        "confiancas": [round(c.confianca, 4) for c in linha.celulas],
        "motivos": [c.motivo for c in linha.celulas],
        # o nome também, para a interface de revisão mostrar onde a leitura é
        # frágil — é o canal que deixa o humano notar a troca de pessoa (A15)
        "confiancas_nome": [round(c.confianca, 4) for c in linha.celulas_nome],
        "motivos_nome": [c.motivo for c in linha.celulas_nome],
        "leitura_exata": verdade is not None and lida == verdade,
        "score": score,
        "margem": margem,
        "decisao": "aceito" if aceito else "quarentena",
        # `acertou` = o campeão do ranking é a pessoa certa, antes do gate.
        # Para quem está fora da base não existe campeão certo: é False por
        # construção, e o acerto do SISTEMA nessas linhas é cair em quarentena.
        "acertou": verdade is not None and campeao["matricula"] == verdade,
        "top3": [{"matricula": a["matricula"], "nome": a["nome"],
                  "curso": a["curso"], "score": s} for s, a in topo],
    }


def processar_folha(nome_folha: str, modelo: int | str, base: list[dict], ocr: OCR,
                    ocr_letras: OCR | None = None,
                    corte_nome: float = UNANIME,
                    sim_nome=None,
                    corte_matricula: float = UNANIME) -> tuple[list[dict], int, int]:
    caminho = f"dados/{nome_folha}.png"
    if not os.path.exists(caminho):
        print(f"  {nome_folha}: imagem ausente, pulando")
        return [], 0, 0

    gabarito = carregar_gabarito(caminho)
    folha_modelo = modelo_folha(modelo)
    alinhada = Image().set_data(Align(*folha_modelo.warp).aruco(
        Image().load_from_file(caminho).data))
    # um cortador só: ele limpa a pasta de cache na construção, e construir de
    # novo apagaria as células da primeira coluna
    cortador = SheetCutter(PASTA_CELULAS)
    celulas, _ = cortador.cut_info(alinhada, folha_modelo, "matricula")
    celulas_nome, _ = cortador.cut_info(alinhada, folha_modelo, "nome")

    matriculas_base = {a["matricula"] for a in base}
    registros, n_linhas, n_anuladas = [], 0, 0

    for i, celulas_linha in enumerate(celulas):
        numero = i + 1
        linha = ler_linha(celulas_linha, ocr, nome_folha, numero,
                          celulas_nome[i], ocr_letras, corte_nome,
                          corte_matricula)
        if linha.em_branco:
            continue

        n_linhas += 1
        verdade = gabarito.get(numero, ("", ""))[0] or None
        na_base = verdade in matriculas_base if verdade else False

        # Linha anulada não vai ao matcher: a pessoa declarou que ela não vale.
        # Mas fica REGISTRADA, com um registro por modo para não furar o
        # contrato que `resumir` e `reaplicar_gate` esperam. Se a regra de
        # anulação disparar por engano, o rastro é este.
        if linha.anulada:
            n_anuladas += 1
            for modo, alpha in ((m, a) for a in ALPHAS for m in MODOS):
                registros.append({
                    "folha": nome_folha, "linha": numero, "modo": modo,
                    "alpha": alpha, "leitura": linha.leitura(modo == "abstem"),
                    "leitura_nome": linha.leitura_nome(modo == "abstem"),
                    "verdade": verdade, "verdade_na_base": na_base,
                    "confiancas": [], "motivos": [c.motivo for c in linha.celulas],
                    "leitura_exata": False, "score": 0.0, "margem": 0.0,
                    "decisao": "anulada", "acertou": False, "top3": [],
                })
            continue

        for alpha in ALPHAS:
            for modo in MODOS:
                registros.append(
                    avaliar_linha(linha, modo, alpha, base, verdade, na_base,
                                  sim_nome=sim_nome))

    return registros, n_linhas, n_anuladas


def conferir_contra_identificar(registros: list[dict], base: list[dict],
                                sim_nome=None) -> None:
    """O gate reaplicado em `ranquear` tem que bater com `fuzzy.identificar()`.

    `ranquear` recalcula score, margem e decisão para poder expor o top-3. Se
    divergir do módulo validado, o experimento estaria medindo outra coisa.

    Também é o que pega descasamento de métrica: se `ranquear` usar um par
    (métrica, semântica) e `identificar()` outro, os scores não batem e a
    asserção estoura aqui, em vez de virar resultado errado lá na frente.
    """
    conferidos = 0
    for r in registros:
        if r["decisao"] == "anulada":
            continue        # não passou pelo matcher, não há o que conferir
        ref = identificar(r["leitura"], r["leitura_nome"], base,
                          alpha=r["alpha"], theta=THETA, delta=DELTA,
                          sim_nome=sim_nome)
        assert abs(ref["score"] - r["score"]) < 1e-12, (r, ref)
        assert abs(ref["margem"] - r["margem"]) < 1e-12, (r, ref)
        assert ref["decisao"] == r["decisao"], (r, ref)
        conferidos += 1
    print(f"conferência: {conferidos} registros batem com identificar() ✓")


def resumir(registros: list[dict], modo: str, alpha: float = 1.0,
            theta: float = THETA, delta: float = DELTA) -> dict:
    """Precisão e cobertura sobre TODA linha com verdade conhecida.

    Aceitar alguém que não está na base é erro, não caso à parte: o sistema
    afirmou uma identidade e a afirmação é falsa. Manter essas linhas fora da
    precisão infla o número — e participante externo é cenário previsto (D07),
    não anomalia. Elas seguem contadas em separado, porque só ali o acerto é
    quarentena.

    theta/delta são parâmetros para permitir varredura sobre o JSON salvo,
    sem repagar o pipeline de imagem — 'pontue uma vez, limiarize muitas'.
    """
    do_modo = [r for r in registros
               if r["modo"] == modo and r["alpha"] == alpha and r["verdade"]
               and r["decisao"] != "anulada"]
    dentro = [r for r in do_modo if r["verdade_na_base"]]
    fora = [r for r in do_modo if not r["verdade_na_base"]]

    def passa(r: dict) -> bool:
        return r["score"] >= theta and r["margem"] >= delta

    aceitos = [r for r in do_modo if passa(r)]
    # acerto só existe para quem está na base: para os de fora, qualquer
    # aceite é uma identidade afirmada falsamente.
    certos = sum(1 for r in aceitos if r["verdade_na_base"] and r["acertou"])
    return {
        "n": len(do_modo),
        "n_dentro": len(dentro),
        "leitura_exata": sum(r["leitura_exata"] for r in do_modo),
        "top1": sum(r["acertou"] for r in do_modo),
        "n_aceitos": len(aceitos),
        "certos": certos,
        "errados": len(aceitos) - certos,
        "precisao": certos / len(aceitos) if aceitos else float("nan"),
        "cobertura": len(aceitos) / len(do_modo) if do_modo else float("nan"),
        "fora_n": len(fora),
        "fora_aceitos": sum(1 for r in fora if passa(r)),
    }


def alinhar(caminho: str, folha_modelo) -> Image:
    """Retifica a folha pelos marcadores, na resolução que a geometria pede."""
    return Image().set_data(Align(*folha_modelo.warp).aruco(
        Image().load_from_file(caminho).data))


def medir_chaves(alvos: list[tuple[str, int | str]], ocr_letras: OCR,
                 corte: float = CORTE_CNN) -> list[dict]:
    """Lê só a banda CHAVE de cada folha, sem tocar na grade.

    Existe separado do pipeline porque é barato: 16 células contra as ~600 da
    grade, e o `Decomposer` é 95% do custo de uma folha. Medir a chave não
    precisa repagar a leitura dos participantes.
    """
    saida = []
    for nome_folha, modelo in alvos:
        caminho = f"dados/{nome_folha}.png"
        if not os.path.exists(caminho):
            print(f"  {nome_folha}: imagem ausente, pulando")
            continue

        folha_modelo = modelo_folha(modelo)
        alinhada = alinhar(caminho, folha_modelo)
        chave = ler_chave(alinhada, SheetCutter(PASTA_CELULAS), folha_modelo,
                          ocr_letras, corte)

        if chave is None:
            print(f"  {nome_folha:8s} [{folha_modelo.rotulo}] sem banda de chave")
            continue

        estado = ("em branco" if chave.em_branco else
                  "ilegível" if ABSTENCAO in chave.texto else "lida")
        conf = " ".join(f"{c.confianca:.2f}" for c in chave.celulas
                        if c.motivo != "vazia")
        print(f"  {nome_folha:8s} [{folha_modelo.rotulo}] "
              f"{chave.texto or '(vazia)':18s} {chave.com_tinta:2d} com tinta  "
              f"{estado}")
        if conf:
            print(f"  {'':8s} confianças: {conf}")
        saida.append({"folha": nome_folha, "modelo": folha_modelo.rotulo,
                      "chave": chave.texto, "com_tinta": chave.com_tinta,
                      "estado": estado,
                      "confiancas": [c.confianca for c in chave.celulas],
                      "motivos": [c.motivo for c in chave.celulas]})
    return saida


def main() -> None:
    p = argparse.ArgumentParser(description="Pipeline -> identificação -> JSON.")
    p.add_argument("--folha", default="", help="processar só esta folha")
    p.add_argument("--saida", default=SAIDA)
    p.add_argument("--reler", action="store_true",
                   help="ler o JSON salvo e só resumir (não roda o pipeline)")
    p.add_argument("--letras", default="cnn", choices=("knn", "cnn"),
                   help="reconhecedor da coluna de nome (objetivo específico d)")
    p.add_argument("--matricula", default="cnn", choices=("knn", "cnn"),
                   help="reconhecedor da coluna de matrícula (objetivo específico d): "
                        "CNN 0,896 x KNN 0,815 em 270 células, McNemar p=0,00094")
    p.add_argument("--modelo-letras", default="",
                   help="caminho do .keras das letras. Pode trazer '{folha}' e "
                        "virar UM MODELO POR FOLHA — é como se mede o afinado "
                        "sem vazamento: cada folha lida pelo modelo held-out "
                        "que não a viu (A37)")
    p.add_argument("--corte-letras", type=float, default=None,
                   help="corte de confiança das letras (padrão: 0,999 knn · 0,95 cnn)")
    p.add_argument("--corte-matricula", type=float, default=None,
                   help="corte de confiança dos dígitos (padrão: 0,999 knn · 0,95 cnn)")
    p.add_argument("--conjunto", default="verificado", choices=list(CONJUNTOS),
                   help="quais folhas: 'verificado' = só as de verdade independente "
                        "(A25) · 'historico' = as 40 linhas do vault até 27/08")
    p.add_argument("--metrica", default=METRICA_NOME, choices=tuple(SIM_NOME),
                   help="métrica do canal do nome, com a semântica do '?' junto "
                        "(objetivo específico e)")
    p.add_argument("--so-chave", action="store_true",
                   help="lê só a banda CHAVE das folhas e sai — não roda a "
                        "grade nem o matcher")
    args = p.parse_args()
    sim_nome = SIM_NOME[args.metrica]

    if args.so_chave:
        if args.letras != "cnn" or args.modelo_letras:
            raise SystemExit("--so-chave usa o classificador de letras padrão")
        alvos = [(n, m) for n, m in CONJUNTOS[args.conjunto]
                 if not args.folha or n == args.folha]
        if not alvos:
            raise SystemExit(f"folha desconhecida: {args.folha}")
        from ocr import OCRCNN
        medir_chaves(alvos, OCRCNN.de_letras(),
                     CORTE_CNN if args.corte_letras is None else args.corte_letras)
        return

    t0 = time.perf_counter()

    if args.reler:
        with open(args.saida) as f:
            registros = json.load(f)
        total_linhas = len({(r["folha"], r["linha"]) for r in registros})
        print(f"relendo {args.saida}: {len(registros)} registros, {total_linhas} linhas")
    else:
        alvos = [(n, m) for n, m in CONJUNTOS[args.conjunto]
                 if not args.folha or n == args.folha]
        if not alvos:
            raise SystemExit(f"folha desconhecida: {args.folha}")

        base = carregar_base(BASE_XLSX)
        for a in base:
            a["nome"] = normalizar(a["nome"])   # uma vez, fora dos loops (D05)
        print(f"base vigente: {len(base)} pessoas | "
              f"alphas={ALPHAS} theta={THETA} delta={DELTA} "
              f"nome={args.metrica}")

        # Um classificador por campo (D08), e o corte de cada um conforme o
        # tipo do reconhecedor — não conforme o campo.
        if args.matricula == "cnn":
            from ocr import OCRCNN
            ocr = OCRCNN.de_digitos()
            corte_mat = CORTE_CNN if args.corte_matricula is None else args.corte_matricula
        else:
            ocr = OCR.de_digitos()
            corte_mat = UNANIME if args.corte_matricula is None else args.corte_matricula

        # Um modelo de letras por folha, quando o caminho traz `{folha}`. Os
        # modelos são carregados AQUI, antes do pipeline: arquivo que falta tem
        # que derrubar a execução na primeira linha, não depois de trinta
        # minutos de leitura.
        letras_por_folha: dict[str, OCR] = {}
        if args.letras == "cnn":
            from ocr import OCRCNN
            from dados_treino import ALFABETO_MAIUSCULAS
            if not args.modelo_letras:
                ocr_letras = OCRCNN.de_letras()
            elif "{folha}" not in args.modelo_letras:
                ocr_letras = OCRCNN(args.modelo_letras, ALFABETO_MAIUSCULAS)
            else:
                ocr_letras = None
                for nome_folha, _ in alvos:
                    caminho = args.modelo_letras.format(folha=nome_folha)
                    letras_por_folha[nome_folha] = OCRCNN(caminho,
                                                          ALFABETO_MAIUSCULAS)
                    print(f"  letras de {nome_folha}: {caminho}")
            corte = CORTE_CNN if args.corte_letras is None else args.corte_letras
        else:
            if args.modelo_letras:
                raise SystemExit("--modelo-letras só vale com --letras cnn")
            ocr_letras = OCR.de_letras()
            corte = UNANIME if args.corte_letras is None else args.corte_letras
        print(f"matrícula: {args.matricula} (corte {corte_mat}) | "
              f"letras: {args.letras} (corte {corte})")
        print(f"conjunto: {args.conjunto} "
              f"({', '.join(n for n, _ in alvos)})")

        registros = []
        total_linhas = total_anuladas = 0
        for nome_folha, modelo in alvos:
            letras_desta = letras_por_folha.get(nome_folha, ocr_letras)
            assert letras_desta is not None, \
                f"sem modelo de letras para {nome_folha}"
            regs, n, anul = processar_folha(nome_folha, modelo, base, ocr,
                                            letras_desta, corte, sim_nome,
                                            corte_mat)
            print(f"  {nome_folha}: {n} linhas preenchidas"
                  + (f", {anul} anuladas" if anul else ""))
            registros.extend(regs)
            total_linhas += n
            total_anuladas += anul

        conferir_contra_identificar(registros, base, sim_nome)

    sem_verdade = {(r["folha"], r["linha"]) for r in registros if not r["verdade"]}
    fora = {(r["folha"], r["linha"], r["verdade"])
            for r in registros if r["verdade"] and not r["verdade_na_base"]}
    anuladas = {(r["folha"], r["linha"]) for r in registros
                if r["decisao"] == "anulada"}

    print(f"\n{total_linhas} linhas | {len(sem_verdade)} sem matrícula no gabarito "
          f"| {len(fora)} fora da base vigente | {len(anuladas)} anuladas")
    for f, l in sorted(anuladas):
        print(f"    anulada pelo protocolo: {f} linha {l:02d}")
    for f, l, v in sorted(fora):
        print(f"    fora do contradomínio: {f} linha {l:02d} — {v}")

    alphas = sorted({r["alpha"] for r in registros}, reverse=True)

    cab = (f"{'alpha':>5} {'modo':<8} {'n':>3} {'exatas':>7} {'top1':>5} {'aceitos':>8} "
           f"{'errados':>8} {'precis':>7} {'cobert':>7} {'fora ok':>8}")
    print(f"\ntheta={THETA} delta={DELTA}\n{cab}")
    for alpha in alphas:
        for modo in MODOS:
            m = resumir(registros, modo, alpha)
            fora_ok = f"{m['fora_n'] - m['fora_aceitos']}/{m['fora_n']}"
            print(f"{alpha:>5.1f} {modo:<8} {m['n']:>3} {m['leitura_exata']:>7} {m['top1']:>5} "
                  f"{m['n_aceitos']:>8} {m['errados']:>8} {m['precisao']:>7.3f} "
                  f"{m['cobertura']:>7.3f} {fora_ok:>8}")

    # varredura de delta sobre os mesmos scores: delta é o que deveria barrar o
    # impostor admitido pelo wildcard, então vale ver o preço de endurecê-lo.
    print(f"\nvarredura de delta (theta={THETA})")
    print(f"{'alpha':>5} {'modo':<8} {'delta':>5} {'aceitos':>8} {'errados':>8} "
          f"{'precis':>7} {'cobert':>7}")
    for alpha in alphas:
        for modo in MODOS:
            for delta in (0.00, 0.05, 0.10, 0.17, 0.20, 0.34):
                m = resumir(registros, modo, alpha, THETA, delta)
                print(f"{alpha:>5.1f} {modo:<8} {delta:>5.2f} {m['n_aceitos']:>8} "
                      f"{m['errados']:>8} {m['precisao']:>7.3f} {m['cobertura']:>7.3f}")

    if not args.reler:
        os.makedirs(os.path.dirname(args.saida), exist_ok=True)
        with open(args.saida, "w") as f:
            json.dump(registros, f, ensure_ascii=False, indent=1)
        print(f"\n{len(registros)} registros crus em {args.saida} "
              f"(fora do git — dado pessoal)")
    print(f"tempo total: {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
