"""Adaptador entre a API e o pipeline de imagem que já existe.

Este módulo é uma **casca fina**. Ele não reimplementa nada: chama
`identificar_folha.processar_folha`, que é o código medido e registrado no
vault. Reescrever o pipeline por trás de uma API sem suíte de testes é como o
θ=0,85 sobreviveu três sessões — a refatoração para pacote está adiada de
propósito, e ela quer testes antes.

## Duas restrições que vêm do código existente, não de escolha nossa

**1. O pipeline não é reentrante.** `SheetCutter.__init__` faz `rmtree` numa
pasta de cache FIXA e compartilhada, então duas folhas processadas ao mesmo
tempo apagam as células uma da outra — em silêncio, produzindo leitura errada
em vez de erro. Por isso existe `_TRAVA`: uma folha por vez, sempre. Com 9 s por
folha e 500 folhas isso dá ~75 min de fila, o que é aceitável para o uso; se um
dia deixar de ser, a saída é tornar a pasta de cache única por processamento —
não paralelizar por cima.

**2. Ele resolve caminhos relativos ao próprio diretório.** Os modelos estão em
`modelo/`, a base em `../experimento-fuzzy/dados/`, a imagem em `dados/`. Então
o processo inteiro roda com o cwd apontado para `experimento-imagem/`
(`preparar()`), e todo o resto da API usa caminho absoluto. É um remendo, e ele
sai junto com a refatoração.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from .config import config

# Uma folha por vez — ver o cabeçalho.
_TRAVA = threading.Lock()
_RECURSOS: dict[str, Any] = {}


def preparar() -> None:
    """Põe o pipeline no `sys.path` e o processo no cwd que ele espera.

    Chamado uma vez, no startup. Idempotente.
    """
    caminho = str(config.pasta_pipeline)
    if caminho not in sys.path:
        sys.path.insert(0, caminho)
    if Path.cwd() != config.pasta_pipeline:
        os.chdir(config.pasta_pipeline)


def carregar_recursos() -> dict[str, Any]:
    """Base e classificadores, carregados UMA vez.

    Carregar um modelo Keras leva segundos; fazer isso por requisição somaria
    mais tempo que o próprio reconhecimento. A base também: `identificar` faz
    milhares de comparações por chamada e normalizar 1.334 nomes dentro do laço
    é o erro que as convenções do projeto proíbem explicitamente.
    """
    if _RECURSOS:
        return _RECURSOS

    preparar()
    from identificar_folha import BASE_XLSX, CORTE_CNN            # noqa: E402
    from base_alunos import carregar_base                          # noqa: E402
    from normalizacao import normalizar                            # noqa: E402
    from fuzzy import SIM_NOME, METRICA_NOME, ALPHA, THETA, DELTA  # noqa: E402
    from ocr import OCRCNN                                         # noqa: E402
    from dados_treino import ALFABETO_MAIUSCULAS                   # noqa: E402

    # A chave segue na CNN base mesmo com o afinado ligado: foi nela que A48
    # mediu, e afinar desloca a escala da softmax que o corte de 0,95 usa.
    ocr_chave = OCRCNN.de_letras()

    base = carregar_base(BASE_XLSX)
    for pessoa in base:
        pessoa["nome"] = normalizar(pessoa["nome"])   # uma vez, fora dos laços

    # D12: a CNN base é o padrão. `modelo_letras` só é lido se alguém o definir
    # de propósito — o afinado viu todas as células de dev, e ligá-lo por
    # acidente tornaria qualquer medição nelas uma medição no treino.
    ocr_letras = (OCRCNN(config.modelo_letras, ALFABETO_MAIUSCULAS)
                  if config.modelo_letras else ocr_chave)

    _RECURSOS.update(
        base=base,
        ocr_matricula=OCRCNN.de_digitos(),
        ocr_letras=ocr_letras,
        ocr_chave=ocr_chave,
        corte=CORTE_CNN,
        sim_nome=SIM_NOME[METRICA_NOME],
        ponto={"alpha": ALPHA, "theta": THETA, "delta": DELTA,
               "metrica_nome": METRICA_NOME},
        modelo_matricula="cnn base (mnist)",
        modelo_letras=config.modelo_letras or "cnn base (emnist)",
    )
    return _RECURSOS


def marcar_duplicadas(linhas: list[dict]) -> int:
    """A40: a mesma matrícula duas vezes na mesma folha.

    Não é erro de identificação — as duas linhas podem apontar para a pessoa
    certa —, então nenhuma métrica de precisão ou cobertura a enxerga. O
    estrago é na FREQUÊNCIA, que é o produto. Marca as duas e deixa a decisão
    para o humano, que é o que D03 manda fazer com ambiguidade.

    A regra é *dentro da mesma folha*. A mesma pessoa em folhas diferentes é
    normal e correto: são eventos distintos.
    """
    vistas: dict[str, list[dict]] = {}
    for r in linhas:
        if r["decisao"] == "anulada" or not r.get("matricula_sugerida"):
            continue
        vistas.setdefault(r["matricula_sugerida"], []).append(r)

    marcadas = 0
    for repetidas in vistas.values():
        if len(repetidas) > 1:
            for r in repetidas:
                r["duplicada_na_folha"] = True
                marcadas += 1
    return marcadas


def linhas_da_grade(modelo_folha: str) -> int:
    """Quantas linhas a grade deste modelo tem. 0 se o modelo for desconhecido."""
    preparar()
    from sheet_cutter import MODELOS                              # noqa: E402
    modelo = MODELOS.get(modelo_folha)
    return modelo.linhas if modelo else 0


class SemMarcadores(RuntimeError):
    """O arquivo não tem os 4 ArUco — provavelmente não é folha de presença."""


def conferir_marcadores(caminho_imagem: Path) -> int:
    """Quantos dos 4 marcadores aparecem. Roda ANTES do pipeline.

    Serve para separar "não é folha de presença" de "é folha e a leitura deu
    errado", que são coisas muito diferentes para quem enviou 500 arquivos e
    precisa saber quais reenviar.

    O sinal é confiável nos dois sentidos: toda folha do projeto tem os quatro
    marcadores, e `gerar_folha.conferir_deteccao` é o portão que impede uma
    folha que não detecta de ir para a impressora (D10) — ela detecta de 150 a
    600 dpi. Ausência de marcador é, portanto, ausência de folha; o que o teste
    NÃO distingue é folha de presença de uma digitalização muito ruim de uma,
    e por isso a mensagem diz "não parece" em vez de afirmar.
    """
    import cv2

    preparar()
    from image import Image                                       # noqa: E402

    dados = Image().load_from_file(str(caminho_imagem)).data
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
        cv2.aruco.DetectorParameters())
    _, ids, _ = detector.detectMarkers(dados)
    return 0 if ids is None else len(ids)


def pasta_recortes(caminho_imagem: Path) -> Path:
    return caminho_imagem.parent / f"{caminho_imagem.stem}_linhas"


def recortar_para_revisao(caminho_imagem: Path, modelo_folha: str) -> int:
    """Grava em disco a faixa horizontal de cada linha, para a tela de revisão.

    Feito **uma vez, no processamento**, e não a cada requisição: recortar exige
    realinhar a folha inteira (detecção de ArUco + warp de perspectiva), o que
    custa segundos. Servir imagem tem que ser leitura de arquivo.

    Reusa `quarentena.recortar_linhas`, que é onde o formato foi decidido e
    justificado — JPEG q=82 e não PNG, porque a tira é foto de papel e em PNG
    ela custaria 111 KB, ou mais de 1 GB nas 12.500 linhas de 500 folhas.
    """
    import base64

    preparar()
    from quarentena import recortar_linhas                        # noqa: E402

    destino = pasta_recortes(caminho_imagem)
    destino.mkdir(parents=True, exist_ok=True)

    relativo = caminho_imagem.relative_to(config.pasta_pipeline / "dados")
    with _TRAVA:
        tiras = recortar_linhas(str(relativo.with_suffix("")), modelo_folha)

    for numero, b64 in tiras.items():
        (destino / f"{numero:02d}.jpg").write_bytes(base64.b64decode(b64))
    return len(tiras)


def caminho_tira_chave(caminho_imagem: Path) -> Path:
    return pasta_recortes(caminho_imagem) / "chave.jpg"


def ler_chave(caminho_imagem: Path, modelo_folha: str) -> str | None:
    """A chave da folha, com '?' no que não leu. None se a geometria não a tem.

    Grava também o recorte da banda: quem decide a bandeja de folha sem evento
    precisa ver a chave, e realinhar a folha a cada abertura custaria segundos.
    """
    import cv2

    recursos = carregar_recursos()
    preparar()
    from identificar_folha import (PASTA_CELULAS, SheetCutter,      # noqa: E402
                                   alinhar, ler_chave as ler_banda,
                                   modelo_folha as geometria)

    modelo = geometria(modelo_folha)
    with _TRAVA:
        alinhada = alinhar(str(caminho_imagem), modelo)
        chave = ler_banda(alinhada, SheetCutter(PASTA_CELULAS), modelo,
                          recursos["ocr_chave"], recursos["corte"])
    if chave is None:
        return None

    banda = modelo.banda("chave")
    altura, largura = alinhada.get_h_w()
    destino = caminho_tira_chave(caminho_imagem)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destino), alinhada.data[
        int(banda["y_begin"] * altura):int(banda["y_end"] * altura),
        int(banda["x_begin"] * largura):int(banda["x_end"] * largura)])
    return chave.texto


def casar_chave(lida: str | None, chaves: list[str]):
    """Ver `casar_chave.py`, que é função pura e tem o self-check."""
    preparar()
    from casar_chave import casar_chave as casar                  # noqa: E402
    return casar(lida, chaves)


def processar(caminho_imagem: Path, modelo_folha: str) -> list[dict]:
    """Uma folha -> uma lista de linhas, no ponto de operação vigente.

    `processar_folha` devolve QUATRO registros por linha (dois α × dois modos),
    porque o experimento varre esses eixos. A API quer um: o ponto de operação
    que o `fuzzy` declara. Os outros três não se perdem — quem quiser varrer
    limiar reprocessa pelo script, que é onde varredura mora.
    """
    recursos = carregar_recursos()
    preparar()

    # `processar_folha` monta o caminho como `dados/{nome}.png`, então o nome
    # carrega o subdiretório. Isso evita mexer no módulo só para a API.
    relativo = caminho_imagem.relative_to(config.pasta_pipeline / "dados")
    nome = str(relativo.with_suffix(""))

    from identificar_folha import processar_folha, ALPHAS  # noqa: E402
    ponto = recursos["ponto"]
    assert ponto["alpha"] in ALPHAS, (
        f"o ponto de operação (α={ponto['alpha']}) não está entre os α que "
        f"`processar_folha` calcula ({ALPHAS}) — a filtragem abaixo devolveria "
        "zero linhas em silêncio")

    with _TRAVA:
        registros, n_linhas, n_anuladas = processar_folha(
            nome, modelo_folha, recursos["base"], recursos["ocr_matricula"],
            recursos["ocr_letras"], recursos["corte"], recursos["sim_nome"],
            recursos["corte"])

    linhas = []
    for r in registros:
        if r["alpha"] != ponto["alpha"] or r["modo"] != "chuta":
            continue
        topo = r["top3"][0]["matricula"] if r["top3"] else None
        linhas.append({
            "numero": r["linha"],
            "leitura_matricula": r["leitura"],
            "leitura_nome": r["leitura_nome"],
            "confiancas_matricula": r["confiancas"],
            "confiancas_nome": r.get("confiancas_nome", []),
            "motivos_matricula": r["motivos"],
            "motivos_nome": r.get("motivos_nome", []),
            "score": r["score"],
            "margem": r["margem"],
            "decisao": r["decisao"],
            "matricula_sugerida": topo,
            "candidatos": r["top3"],
            "duplicada_na_folha": False,
        })

    marcar_duplicadas(linhas)
    assert len(linhas) == n_linhas, (
        f"{len(linhas)} linhas filtradas contra {n_linhas} preenchidas — a "
        "filtragem por (α, modo) perdeu ou duplicou linha")
    return linhas
