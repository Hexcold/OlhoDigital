"""Impressão em lote: cabeçalho e planilha de chaves viram um PDF só.

A coleta imprime várias folhas por chave: uma sala cheia não cabe em 33 linhas,
então cada ocorrência pede a própria pilha (D15). Este módulo recebe a planilha
`chave × folhas`, desenha uma folha por chave e repete as páginas.

Três portões decidem se o PDF sai, todos antes de o papel ser gasto:

  1. a chave passa por `gerar_folha.validar_chave`, a mesma regra do evento (D14);
  2. os textos não podem mudar nenhum pixel fora de `areas_editaveis` (D16);
  3. os 4 marcadores têm que ser detectados, como manda D10.

A distância entre chaves é ALERTA, não portão. A45 a tornou requisito de quem
GERA a chave, mas aqui a chave vem de fora, e recusá-la exigiria um critério
calibrado que ainda não existe. Por isso gerar com alerta pendente pede
confirmação explícita, em vez de bloquear ou passar calado.

Nada aqui lê imagem de participante nem toca no banco: é função pura sobre
texto e geometria, e por isso roda sem `dados/` e sem modelo.
"""
from __future__ import annotations

from functools import lru_cache
import csv
import datetime as dt
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import cv2
import numpy as np
from openpyxl import Workbook, load_workbook
from rapidfuzz.distance import Levenshtein

import gerar_folha as gf

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "experimento-fuzzy"))
from casar_chave import MINIMO_LETRAS  # noqa: E402
from normalizacao import normalizar  # noqa: E402

# O versionado é o ponto de partida. A variável existe para o contêiner: o que a
# tela salva tem que sobreviver à troca da imagem, então vai para um volume.
CABECALHO_PADRAO = Path(__file__).resolve().with_name("cabecalho.json")
ARQUIVO_CABECALHO = Path(os.environ.get("OLHODIGITAL_CABECALHO", CABECALHO_PADRAO))

# O que o cabeçalho NÃO alcança. Mora aqui para a tela mostrar a mesma lista que
# o código garante, em vez de uma cópia que envelhece.
PARTES_TRAVADAS = ("marcadores ArUco", "grade de células", "células da chave",
                   "rótulos das colunas", "numeração das linhas",
                   "carimbo de versão")

# A45 mediu até 3 '?' numa chave impressa. Sob Levenshtein com curinga cada '?'
# absorve no máximo uma edição, então a chave A só deixa de casar com B a
# distância zero quando d(A, B) > 3. É dedução sobre o pior caso observado, não
# calibração: n=1, em raster.
DISTANCIA_MINIMA = 4
LIMITE_ALERTAS = 20

# Medido nas 26 letras, em 4.848 células (A49). V impresso é a única letra lida
# ERRADA, sempre como W: a folha cai na bandeja, e só iria para o evento errado se
# existisse a chave gêmea com W, que o alerta de distância já pega. As demais letras
# fracas viram `?`, o que não engana ninguém.
LETRAS_SEGURAS = frozenset("BDEFGHKLMPQRSTWZ")
LETRA_LIDA_ERRADA = "V"

# Teto contra digitação errada: 5000 no lugar de 50 viraria um PDF de gigabytes.
LIMITE_PAGINAS = 2000

LINHAS_PROCURADAS = 10   # planilha real costuma ter título acima do cabeçalho
SEM_CHAVES = "o lote não tem nenhuma chave"

# KEY, TAG e DAY são os nomes da planilha de controle dos Encontros, que não tem
# coluna de folhas: ver `ler_planilha`.
SINONIMOS = {
    "chave": {"CHAVE", "CHAVES", "CHAVE DO EVENTO", "CHAVE DA FOLHA", "KEY"},
    "folhas": {"FOLHAS", "FOLHA", "QUANTIDADE", "QUANT", "QTD", "QTDE",
               "QUANTIDADE DE FOLHAS", "NUMERO DE FOLHAS", "N DE FOLHAS",
               "TOTAL DE FOLHAS", "FOLHAS POR CHAVE"},
    "nome": {"NOME", "NOME DO EVENTO", "EVENTO", "ATIVIDADE", "TITULO", "TAG"},
    "local": {"LOCAL", "SALA"},
    "responsavel": {"RESPONSAVEL"},
    "dia": {"DIA", "DAY", "DATA", "DATA DO EVENTO", "DIA DO EVENTO"},
}
SEM_FOLHAS = "a planilha não tem a coluna 'folhas': sai uma folha por chave"


class AlertasPendentes(Exception):
    """Há alerta e ninguém confirmou. Separado de ValueError porque não é erro:
    o PDF sairia, e quem decide é a pessoa."""

    def __init__(self, alertas: list[str]):
        super().__init__("; ".join(alertas))
        self.alertas = alertas


@dataclass
class ItemLote:
    linha: int
    chave: str
    folhas: int
    nome: str | None = None
    local: str | None = None
    responsavel: str | None = None
    dia: dt.date | None = None          # vai no carimbo da folha


@dataclass
class Lote:
    itens: list[ItemLote]
    erros: list[str]
    alertas: list[str]

    @property
    def total_folhas(self) -> int:
        return sum(i.folhas for i in self.itens)


# --- cabeçalho ---------------------------------------------------------------

def carregar_cabecalho(caminho: Path = ARQUIVO_CABECALHO) -> gf.Cabecalho:
    """O cabeçalho salvo. Sem nada salvo, o versionado com o projeto.

    Campo desconhecido é ignorado: um arquivo salvo por versão anterior não pode
    travar a impressão.
    """
    origem = caminho if caminho.exists() else CABECALHO_PADRAO
    try:
        dados = json.loads(origem.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            raise ValueError("não é um objeto")
        textos = {c.name: dados[c.name] for c in fields(gf.Cabecalho) if c.name in dados}
        if not all(isinstance(v, str) for v in textos.values()):
            raise ValueError("campo que não é texto")
    except ValueError as erro:
        raise ValueError(f"o cabeçalho salvo em {origem.name} está inválido ({erro}). "
                         f"Apague o arquivo para voltar ao padrão") from erro
    return gf.Cabecalho(**textos)


def salvar_cabecalho(cabecalho: gf.Cabecalho,
                     caminho: Path = ARQUIVO_CABECALHO) -> None:
    """Confere antes e grava por troca atômica: o arquivo nunca fica pela metade."""
    conferir_cabecalho(cabecalho)
    # nome único: dois salvamentos ao mesmo tempo não podem dividir o temporário
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=caminho.parent,
                                     suffix=".tmp", delete=False) as temporario:
        temporario.write(json.dumps(asdict(cabecalho), ensure_ascii=False,
                                    indent=2) + "\n")
    Path(temporario.name).replace(caminho)


def _raster(svg: str, dpi: int) -> np.ndarray:
    png = subprocess.run(["rsvg-convert", "--format=png", f"--dpi-x={dpi}",
                          f"--dpi-y={dpi}"], input=svg.encode("utf-8"),
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


@lru_cache(maxsize=16)
def _folha_congelada(dpi: int, carimbo: str | None = None) -> np.ndarray:
    """A v4 nunca muda, e rasterizá-la era metade do tempo de cada prévia.

    Com `carimbo`, é a v4 com aquele carimbo: um por dia de evento no lote.
    """
    return _raster(gf.desenhar_svg(carimbo=carimbo), dpi)


def _conferir_svg(svg: str, dpi: int = 200, carimbo: str | None = None,
                  areas: list | None = None) -> dict:
    """Compara com a folha congelada, pixel a pixel, fora das áreas editáveis.

    A referência é desenhada com o mesmo `carimbo` da folha conferida: assim o
    carimbo segue travado, e qualquer outro desenho sobre ele é recusado.
    """
    referencia = _folha_congelada(dpi, carimbo)
    nova = _raster(svg, dpi)
    if nova.shape != referencia.shape:
        raise ValueError("os textos mudaram o tamanho da folha")
    mudou = referencia != nova
    dentro = int(mudou.sum())
    for area in gf.areas_editaveis() if areas is None else areas:
        x0, y0, x1, y1 = (round(v * dpi / 25.4) for v in area)
        mudou[y0:y1, x0:x1] = False
    fora = int(mudou.sum())
    if fora:
        raise ValueError(f"os textos alteraram {fora} pixels fora das áreas "
                         f"editáveis. Troque ou encurte o texto")
    return {"pixels_mudados": dentro, "pixels_fora": fora}


def conferir_cabecalho(cabecalho: gf.Cabecalho, dpi: int = 200,
                       carimbo: str | None = None) -> dict:
    """Portão de D16: o que o sistema lê tem que sair idêntico à v4.

    Compara o que foi desenhado em vez de confiar na conta de largura: a garantia
    passa a vir da folha, e pega também o defeito que ninguém previu. A 200 dpi,
    que é a resolução do escâner da coleta.

    Com `carimbo`, confere antes que ele só mexe na caixa dele. É o único texto da
    parte travada que muda de uma folha para outra (o dia do evento), e mora ao
    lado do marcador 1.
    """
    if carimbo is not None:
        try:
            _conferir_svg(gf.desenhar_svg(carimbo=carimbo), dpi,
                          areas=[gf.area_carimbo()])
        except ValueError as erro:
            raise ValueError(f"o carimbo {carimbo!r} sai da caixa dele: "
                             f"{erro}") from erro
    return _conferir_svg(gf.desenhar_svg(cabecalho=cabecalho, carimbo=carimbo),
                         dpi, carimbo)


def previa_png(cabecalho: gf.Cabecalho, chave: str = "", dpi: int = 110) -> bytes:
    """O topo da folha, do jeito que vai ser impresso.

    Usa o renderizador da impressão, não HTML: a prévia precisa mostrar o que
    vai sair no papel, inclusive quando o texto encolhe para caber.
    """
    # sem dia, o carimbo que a folha avulsa leva; o do lote só muda a data
    img = _raster(gf.desenhar_svg(chave=chave, cabecalho=cabecalho,
                                  carimbo=gf.texto_carimbo()), dpi)
    corte = round((gf.LAYOUT.tab_y0 + 3 * gf.LAYOUT.lado_celula) * dpi / 25.4)
    return cv2.imencode(".png", img[:corte])[1].tobytes()


# --- planilha ----------------------------------------------------------------

def _tabelas(nome_arquivo: str, conteudo: bytes) -> list[tuple[str, list[list]]]:
    """[(rótulo, linhas)], uma entrada por aba. Célula crua, sem interpretar."""
    extensao = Path(nome_arquivo).suffix.lower()
    if extensao == ".xlsx":
        try:
            wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        except Exception as erro:                            # noqa: BLE001
            raise ValueError(f"não consegui abrir a planilha: {erro}") from erro
        return [(ws.title, [list(l) for l in ws.iter_rows(values_only=True)])
                for ws in wb.worksheets]
    if extensao == ".csv":
        try:
            texto = conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = conteudo.decode("latin-1")       # CSV salvo pelo Excel antigo
        # o Excel em português separa por ';', porque ',' é a vírgula decimal, e
        # copiar de uma planilha para o editor separa por tabulação. Conta nas
        # primeiras linhas, e não só na primeira, que pode ser um título.
        amostra = "\n".join(texto.splitlines()[:LINHAS_PROCURADAS])
        separador = max(",;\t", key=amostra.count)
        return [("csv", list(csv.reader(io.StringIO(texto), delimiter=separador)))]
    raise ValueError(f"formato {extensao or 'desconhecido'}: envie .xlsx ou .csv")


def _achar_cabecalho(linhas: list[list], exigidos: set[str]
                     ) -> tuple[int, dict[str, int]] | None:
    """(índice da primeira linha que tem os campos `exigidos`, coluna de cada
    campo), ou None.

    Colunas pelo nome, não pela posição, como em `base_alunos`: quem monta a
    planilha acrescenta coluna sem avisar.
    """
    for i, linha in enumerate(linhas[:LINHAS_PROCURADAS]):
        mapa: dict[str, int] = {}
        for j, celula in enumerate(linha):
            if celula is None:
                continue
            texto = normalizar(str(celula))
            for campo, nomes in SINONIMOS.items():
                if texto in nomes and campo not in mapa:
                    mapa[campo] = j
        if exigidos <= set(mapa):
            return i, mapa
    return None


def _data(valor) -> dt.date | None:
    """O dia do evento, que vai no carimbo. O Excel entrega datetime; o CSV, texto."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, dt.datetime):      # antes de `date`, que é a classe-mãe
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    if isinstance(valor, str):
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(valor.strip(), formato).date()
            except ValueError:
                pass
    raise ValueError(f"dia inválido: {valor!r}. Use DD/MM/AAAA")


def _inteiro(valor) -> int | None:
    """Inteiro exato ou None. O Excel entrega 12.0; 12.5 folhas é erro de digitação."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str):
        limpo = valor.strip()
        # `isdigit` sozinho aceita ² e quebraria no int()
        if limpo.isascii() and limpo.isdigit():
            return int(limpo)
    return None


def _texto_opcional(linha: list, coluna: int | None) -> str | None:
    if coluna is None or coluna >= len(linha) or linha[coluna] is None:
        return None
    return str(linha[coluna]).strip() or None


def ler_planilha(nome_arquivo: str, conteudo: bytes,
                 ate_linha: int | None = None) -> Lote:
    """Lê `chave × folhas`. Linha ruim vira erro com número da linha.

    Falha só quando não há o que ler. Fora disso devolve tudo que achou, para a
    pessoa corrigir os problemas de uma vez e não um por envio.

    Sem a coluna de folhas, como na planilha de controle dos Encontros, sai uma
    folha por chave, e um alerta diz isso. `ate_linha` é a última linha que entra
    no lote, contada como o Excel mostra: o que vem depois nem é lido.
    """
    abas = _tabelas(nome_arquivo, conteudo)
    # Primeiro o formato completo, depois só a chave: um título "Chave do evento"
    # acima de `chave | folhas` não pode fazer a coluna de folhas sumir da leitura.
    for exigidos in ({"chave", "folhas"}, {"chave"}):
        tabelas = [(rotulo, linhas, achado) for rotulo, linhas in abas
                   if (achado := _achar_cabecalho(linhas, exigidos))]
        if tabelas:
            break
    if not tabelas:
        raise ValueError("não achei a coluna 'chave' nas primeiras "
                         f"{LINHAS_PROCURADAS} linhas da planilha")
    if len(tabelas) > 1:
        # ambíguo é erro, não chute: imprimir a aba errada desperdiça o lote
        raise ValueError(f"mais de uma aba tem a coluna 'chave': "
                         f"{', '.join(t[0] for t in tabelas)}. Deixe só uma")

    _, linhas, (cabecalho, col) = tabelas[0]
    sem_folhas = "folhas" not in col
    itens: list[ItemLote] = []
    erros: list[str] = []
    for i, linha in enumerate(linhas[cabecalho + 1:], start=cabecalho + 2):
        if ate_linha is not None and i > ate_linha:
            break
        bruta = linha[col["chave"]] if col["chave"] < len(linha) else None
        vazia = bruta is None or not str(bruta).strip()
        if sem_folhas:
            if vazia:
                continue
            qtd = 1
        else:
            qtd = linha[col["folhas"]] if col["folhas"] < len(linha) else None
            if vazia and (qtd is None or not str(qtd).strip()):
                continue
            if vazia:
                erros.append(f"linha {i}: tem quantidade, mas não tem chave")
                continue
        try:
            chave = gf.validar_chave(str(bruta))
        except ValueError as erro:
            erros.append(f"linha {i}: {erro}")
            continue
        folhas = _inteiro(qtd)
        if folhas is None or folhas < 1:
            erros.append(f"linha {i}: quantidade de folhas inválida para "
                         f"{chave}: {qtd!r}")
            continue
        coluna_dia = col.get("dia")
        try:
            dia = _data(linha[coluna_dia] if coluna_dia is not None
                        and coluna_dia < len(linha) else None)
        except ValueError as erro:
            erros.append(f"linha {i}: {erro}")
            continue
        itens.append(ItemLote(i, chave, folhas,
                              _texto_opcional(linha, col.get("nome")),
                              _texto_opcional(linha, col.get("local")),
                              _texto_opcional(linha, col.get("responsavel")),
                              dia))

    erros_lote, alertas = validar_lote(itens)
    if erros and not itens:
        # toda linha já tem o seu erro: "o lote não tem chave" só repetiria
        erros_lote = [e for e in erros_lote if e != SEM_CHAVES]
    if sem_folhas and itens:
        # Uma folha por chave é um padrão, e padrão não pode passar despercebido:
        # a tela pede confirmação de todo alerta antes de gerar.
        alertas.insert(0, SEM_FOLHAS)
    return Lote(itens, erros + erros_lote, alertas)


def validar_lote(itens: list[ItemLote]) -> tuple[list[str], list[str]]:
    """(erros, alertas). Roda de novo na geração: o cliente pode ter mudado o lote."""
    erros: list[str] = []
    primeira: dict[str, int] = {}
    for item in itens:
        try:
            chave = gf.validar_chave(item.chave)
        except ValueError as erro:
            erros.append(f"linha {item.linha}: {erro}")
            continue
        if not chave:
            erros.append(f"linha {item.linha}: chave vazia")
        elif chave in primeira:
            # D15: duas ocorrências com a mesma chave são indistinguíveis. Mais
            # folhas para a mesma sala é a coluna de quantidade, não outra linha.
            erros.append(f"linha {item.linha}: a chave {chave} repete a da "
                         f"linha {primeira[chave]}")
        else:
            primeira[chave] = item.linha
        if item.folhas < 1:
            erros.append(f"linha {item.linha}: {item.chave} pede {item.folhas} "
                         f"folhas")
    if not itens:
        erros.append(SEM_CHAVES)
    total = sum(max(i.folhas, 0) for i in itens)
    if total > LIMITE_PAGINAS:
        erros.append(f"o lote soma {total} folhas, o limite é {LIMITE_PAGINAS}")

    proximas = sorted((Levenshtein.distance(a, b), a, b)
                      for a, b in itertools.combinations(primeira, 2))
    proximas = [p for p in proximas if p[0] < DISTANCIA_MINIMA]
    alertas = [f"{a} e {b} estão a {d} de distância: com '?' na leitura, as "
               f"duas podem empatar" for d, a, b in proximas[:LIMITE_ALERTAS]]
    if len(proximas) > LIMITE_ALERTAS:
        alertas.append(f"e mais {len(proximas) - LIMITE_ALERTAS} pares de "
                       f"chaves parecidas")
    for chave in primeira:
        if LETRA_LIDA_ERRADA in chave:
            alertas.append(f"{chave} tem {LETRA_LIDA_ERRADA}: impresso, ele é lido "
                           f"como W, e a folha cai na bandeja")
        if len(chave) < MINIMO_LETRAS:
            alertas.append(f"{chave} tem menos de {MINIMO_LETRAS} letras: a folha "
                           f"nunca liga sozinha ao evento")
    return erros, alertas


def planilha_modelo() -> bytes:
    """Um .xlsx de exemplo. Gerado aqui porque o repositório não guarda .xlsx."""
    wb = Workbook()
    ws = wb.active
    ws.title = "chaves"
    ws.append(["chave", "folhas", "nome", "local", "responsavel"])
    for chave, folhas, nome, local in (
            ("JABUTICABA", 12, "Exemplo: apague esta linha", "Sala 1, Bloco 1"),
            ("GOIABA", 8, "Exemplo: apague esta linha", "Sala 2, Bloco 1"),
            ("CUPUACU", 5, "Exemplo: apague esta linha", "Auditório")):
        ws.append([chave, folhas, nome, local, None])
    for coluna, largura in zip("ABCDE", (20, 8, 34, 20, 20)):
        ws.column_dimensions[coluna].width = largura
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


# --- PDF ---------------------------------------------------------------------

def _montar_pdf(pedidos: list[tuple[str, int, dt.date | None]],
                cabecalho: gf.Cabecalho, saida: str) -> dict:
    """Desenha uma folha por chave, confere, e repete as páginas em sequência.

    A detecção completa, nas 5 resoluções de D10, roda na primeira folha de cada
    carimbo, porque ele mora ao lado do marcador 1 e muda com o dia do evento.
    Nas demais roda só a 200 dpi: a chave é escrita dentro da banda, longe dos
    marcadores, e isso deixa o lote em segundos em vez de minutos.
    """
    # Todo portão antes da primeira folha: um dia que não passa não deixa sair
    # meio lote.
    for carimbo in sorted({gf.texto_carimbo(dia) for _, _, dia in pedidos}):
        conferir_cabecalho(cabecalho, carimbo=carimbo)
    completos: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        paginas: list[str] = []
        for i, (chave, folhas, dia) in enumerate(pedidos):
            carimbo = gf.texto_carimbo(dia)
            nome = f"{i:04d}.pdf"
            pdf = os.path.join(tmp, nome)
            gf.gerar(pdf, chave, cabecalho, carimbo)
            dpis = (200,) if carimbo in completos else (150, 200, 300, 400, 600)
            completos.add(carimbo)
            for dpi, ids in gf.conferir_deteccao(pdf, dpis).items():
                if not set(ids) >= {0, 1, 2, 3}:
                    raise ValueError(f"a folha de {chave or 'chave em branco'} "
                                     f"não detecta os 4 marcadores a {dpi} dpi")
            paginas += [nome] * folhas
        # Uma entrada por página. No Windows a linha de comando tem 32.767
        # caracteres: com o caminho inteiro, o lote estourava perto de 500 folhas,
        # que é o tamanho da coleta. Com nome curto cabem as 2.000 de LIMITE_PAGINAS.
        subprocess.run(["pdfunite", *paginas, "lote.pdf"], cwd=tmp, check=True,
                       capture_output=True)
        shutil.move(os.path.join(tmp, "lote.pdf"), saida)
    return {"chaves": len(pedidos), "paginas": len(paginas),
            "bytes": os.path.getsize(saida)}


def gerar_pdf(itens: list[ItemLote], cabecalho: gf.Cabecalho, saida: str,
              aceitar_alertas: bool = False) -> dict:
    """O PDF de um lote vindo da planilha."""
    erros, alertas = validar_lote(itens)
    if erros:
        raise ValueError("; ".join(erros))
    if alertas and not aceitar_alertas:
        raise AlertasPendentes(alertas)
    return _montar_pdf([(i.chave, i.folhas, i.dia) for i in itens], cabecalho,
                       saida)


def gerar_avulsas(chave: str, folhas: int, cabecalho: gf.Cabecalho,
                  saida: str) -> dict:
    """Uma ou várias folhas de uma chave só, sem planilha.

    A chave em branco é legítima: é a folha de estoque, rotulada à mão (D14).
    """
    chave = gf.validar_chave(chave)
    if not 1 <= folhas <= LIMITE_PAGINAS:
        raise ValueError(f"a quantidade de folhas tem que ficar entre 1 e "
                         f"{LIMITE_PAGINAS}")
    return _montar_pdf([(chave, folhas, None)], cabecalho, saida)


if __name__ == "__main__":
    import time

    def titulo(s: str) -> None:
        print(f"\n--- {s} ---")

    def falha_com(trecho: str, f, *args) -> None:
        try:
            f(*args)
        except ValueError as erro:
            assert trecho in str(erro), f"mensagem inesperada: {erro}"
            print(f"  recusado, como deve: {erro}")
            return
        raise AssertionError(f"deveria ter recusado ({trecho})")

    eu = gf.Cabecalho("ENCONTROS UNIVERSITÁRIOS 2026", "FOLHA DE PRESENÇA",
                      "Campus de Quixadá")

    titulo("a folha congelada não mudou")
    v4 = Path(__file__).with_name("dados") / "folha_v4.svg"
    if v4.exists():
        assert gf.desenhar_svg() == v4.read_text(encoding="utf-8")
        print("  sem cabeçalho, o SVG é idêntico a dados/folha_v4.svg")
    else:
        print("  dados/folha_v4.svg ausente, comparação pulada")

    titulo("portão do cabeçalho")
    print(" ", conferir_cabecalho(eu))
    svg = gf.desenhar_svg(cabecalho=eu)
    assert ">DATA<" not in svg and gf.VERSAO_FOLHA in svg
    print("  sem banda DATA, com o carimbo inteiro")

    # O teste que importa: desenhar por cima do que o sistema lê tem que ser
    # recusado. Um ponto de 0,5 mm em cada região travada.
    L = gf.LAYOUT
    alvos = {
        "marcador": (L.ref_x0 + 3, L.ref_y0 + 3),
        "zona de silêncio": (L.ref_x0 + 14, L.ref_y0 + 6),
        "carimbo": (L.ref_x0 + L.ref_larg - 10, L.y_instrucoes + 2.5),
        "rótulo da chave": (L.tab_x0 + 3, L.y_chave - 2.5),
        "rótulo da coluna": (L.tab_x0 + 5, L.y_rotulos + 3),
        "célula da chave": (L.tab_x0 + 2.5 * L.lado_celula, L.y_chave + 3),
        "grade": (L.x_nome + 3.5 * L.lado_celula, L.tab_y0 + 10 * L.lado_celula + 3),
        "numeração": (L.x_numeracao + 2, L.tab_y0 + 2),
        "fim da grade": (L.x_nome + 3, L.tab_y0 + 32.5 * L.lado_celula),
        "marcador de baixo": (L.ref_x0 + 3, L.ref_y0 + L.ref_alt - 6),
    }
    for nome, (x, y) in alvos.items():
        ponto = f'<rect x="{x}" y="{y}" width="0.5" height="0.5" fill="#777777"/>'
        falha_com("fora das áreas", _conferir_svg,
                  svg.replace("</svg>", ponto + "</svg>"))
    print(f"  {len(alvos)} regiões travadas recusaram um ponto de 0,5 mm")

    # e cada campo editável tem que passar pelo portão
    editado = gf.Cabecalho("A", "B", "C", "ASSINE COM CANETA PRETA.")
    print("  todos os campos editados:", conferir_cabecalho(editado))
    print("  sem instrução:", conferir_cabecalho(gf.Cabecalho(instrucao="")))
    falha_com("não cabe", conferir_cabecalho, gf.Cabecalho(instrucao="W" * 90))
    longo = gf.Cabecalho("ENCONTROS UNIVERSITÁRIOS 2026 " * 3)
    curto_demais = gf.Cabecalho("W" * 60)
    falha_com("limite", conferir_cabecalho, gf.Cabecalho("X" * 91))
    falha_com("não cabe", conferir_cabecalho, curto_demais)
    falha_com("não pode ficar vazio", conferir_cabecalho, gf.Cabecalho("   "))
    falha_com("imprimir", conferir_cabecalho, gf.Cabecalho("A\x07B"))
    falha_com("não cabe", conferir_cabecalho, longo)
    print(" ", conferir_cabecalho(gf.Cabecalho("A & B <teste>")))
    # o pior caso real em português: maiúscula acentuada e cedilha nas três
    # linhas, no corpo nominal. Foi o agudo que estourou a área na primeira versão.
    estresse = gf.Cabecalho("ÁÉÍÓÚ ÂÊÔ ÃÕ ÇÀ", "ÁÉÍÓÚ ÂÊÔ ÃÕ Çgjpqy", "ÁÉÍÓÚ Çgjpqy")
    print("  estresse de acento:", conferir_cabecalho(estresse))

    titulo("planilha")
    csv_bom = "Evento;Chave;Nº de folhas\nAbertura;banana;3\n\n;;\nOficina;GOIABA;2\n"
    lote = ler_planilha("x.csv", csv_bom.encode("latin-1"))
    assert not lote.erros and lote.total_folhas == 5, lote
    assert [i.chave for i in lote.itens] == ["BANANA", "GOIABA"]
    assert lote.itens[0].nome == "Abertura" and lote.itens[1].linha == 5
    print("  csv em latin-1 com ';', sinônimos e linha vazia: ok")

    ruim = ("chave,folhas\nBANANA,3\nBANANA,2\nSALA2,1\nGOIABA,0\n"
            "MANGA,1.5\n,4\nBANANO,1\n")
    lote = ler_planilha("x.csv", ruim.encode())
    for e in lote.erros + lote.alertas:
        print("   ", e)
    assert len(lote.erros) == 5 and len(lote.alertas) == 1, lote

    modelo = ler_planilha("m.xlsx", planilha_modelo())
    assert not modelo.erros and not modelo.alertas, modelo
    assert modelo.total_folhas == 25
    print("  planilha modelo: 3 chaves, 25 folhas, sem alerta")
    falha_com("não achei", ler_planilha, "x.csv", b"a,b\n1,2\n")
    falha_com("formato", ler_planilha, "x.xls", b"\xd0\xcf")

    titulo("PDF")
    with tempfile.TemporaryDirectory() as tmp:
        saida = os.path.join(tmp, "lote.pdf")
        t0 = time.time()
        resumo = gerar_pdf(modelo.itens, eu, saida)
        duracao = time.time() - t0
        info = subprocess.run(["pdfinfo", saida], capture_output=True,
                              text=True, check=True).stdout
        paginas = int(next(l for l in info.splitlines()
                           if l.startswith("Pages:")).split()[1])
        assert paginas == resumo["paginas"] == 25, (paginas, resumo)
        print(f"  {resumo}, {duracao:.1f} s")

        # a ordem e a contagem por chave, lidas do próprio PDF
        texto = subprocess.run(["pdftotext", "-layout", saida, "-"],
                               capture_output=True, text=True, check=True).stdout
        por_pagina = texto.split("\f")
        for pagina, (chave, n) in zip(
                (0, 12, 20), (("JABUTICABA", 12), ("GOIABA", 8), ("CUPUACU", 5))):
            letras = " ".join(chave)
            assert letras in por_pagina[pagina] and letras in por_pagina[pagina + n - 1]
        print("  12 + 8 + 5 páginas, cada chave na sua sequência")

        um = os.path.join(tmp, "um.pdf")
        gerar_pdf([ItemLote(2, "BANANA", 1)], eu, um)
        print("  lote de uma página só: ok")

        for chave, folhas in (("BANANA", 3), ("", 2)):
            resumo = gerar_avulsas(chave, folhas, eu, um)
            texto = subprocess.run(["pdftotext", "-layout", um, "-"],
                                   capture_output=True, text=True, check=True).stdout
            assert resumo["paginas"] == folhas and ("B A N A N A" in texto) == bool(chave)
            # avulsa não tem dia: o carimbo é só a geometria, nunca a data da versão
            assert "DATA" not in texto and gf.VERSAO_FOLHA not in texto
            assert texto.count(gf.texto_carimbo()) == folhas
        print("  folhas avulsas, com chave e em branco: ok")

        gerar_pdf([ItemLote(2, "BANANA", 1, dia=dt.date(2026, 11, 11)),
                   ItemLote(3, "GOIABA", 1)], eu, um)
        texto = subprocess.run(["pdftotext", "-layout", um, "-"],
                               capture_output=True, text=True, check=True).stdout
        com_dia, sem_dia = texto.split("\f")[:2]
        assert "v4 · 11/11/2026" in com_dia and "/2026" not in sem_dia, texto
        print("  carimbo com o dia do evento, e só v4 sem ele: ok")
        falha_com("só A-Z", gerar_avulsas, "SALA2", 1, eu, um)
        falha_com("quantidade", gerar_avulsas, "BANANA", 0, eu, um)

        proximas = [ItemLote(2, "BANANA", 1), ItemLote(3, "BANANO", 1)]
        try:
            gerar_pdf(proximas, eu, um)
        except AlertasPendentes as a:
            print(f"  alerta sem confirmação segura o PDF: {a.alertas[0]}")
        else:
            raise AssertionError("alerta não segurou o PDF")
        gerar_pdf(proximas, eu, um, aceitar_alertas=True)
        print("  com confirmação, sai")

    print("\nself-check concluído")
