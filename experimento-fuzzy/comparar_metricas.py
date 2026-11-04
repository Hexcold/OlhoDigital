"""Objetivo específico (e): comparar algoritmos de correspondência aproximada.

O TCC fixa uma métrica por canal em D01 — Levenshtein na matrícula, Jaro-Winkler
no nome — com justificativa **estrutural**, não empírica:

  matrícula  o erro de leitura cai em qualquer posição do identificador, então
             todas as posições pesam igual -> distância de edição.
  nome       o início do nome é garantido pelo protocolo e a divergência se
             concentra no fim -> viés prefixal do Jaro-Winkler é vantagem.

O argumento é bom, mas **nunca foi medido contra alternativa nenhuma**. Este
módulo mede.

## O que varia, e o que não

Varia só a métrica. As leituras são as MESMAS de
`experimento-imagem/dados/identificacao_folhas.json`, produzidas pelo pipeline
real, e a base é a mesma. Assim a diferença observada é da métrica, não do
reconhecedor nem do contradomínio.

## As duas semânticas do wildcard

Nenhuma métrica de biblioteca conhece o `?` de D02. Hoje o projeto resolve isso
de dois jeitos diferentes, um por canal: no Levenshtein o `?` casa com qualquer
caractere a **custo zero** (implementado à mão), e no Jaro-Winkler ele é
**removido** antes do cálculo.

Essa escolha nunca foi testada. Aqui cada métrica é medida nas duas semânticas:

  deleta   remove o '?' dos dois lados antes de comparar
  literal  mantém o '?' como caractere comum, que nunca casa

E o Levenshtein-wildcard do projeto entra como terceira variante, só na
matrícula, porque é o único que implementa custo zero.

## Métrica de comparação

`top1` — o campeão do ranking é a pessoa certa —, medido antes do gate, porque
gate é assunto de θ e δ. A precisão/cobertura sob o gate entra depois, para a
métrica vencedora.

Uso:
    python comparar_metricas.py
    python comparar_metricas.py --modo abstem
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time

from rapidfuzz import distance as D, fuzz

from base_alunos import carregar_base
from normalizacao import normalizar
from fuzzy import sim_levenshtein

BASE_XLSX = "dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "../experimento-imagem/dados/identificacao_folhas.json"
SAIDA = "resultados_metricas.json"
CURINGA = "?"


# --- as métricas, todas normalizadas para similaridade em [0,1] --------------

def _norm(f):
    """Envelopa uma classe do rapidfuzz na forma `sim(a, b) -> [0,1]`."""
    return lambda a, b: f.normalized_similarity(a, b)


METRICAS = {
    # distâncias de edição: erro em qualquer posição pesa igual
    "levenshtein":        _norm(D.Levenshtein),
    "damerau_lev":        _norm(D.DamerauLevenshtein),   # + transposição
    "osa":                _norm(D.OSA),                  # transposição restrita
    "indel":              _norm(D.Indel),                # só insere/remove
    "lcs":                _norm(D.LCSseq),               # maior subsequência
    # baseadas em correspondência aproximada de posição
    "jaro":               _norm(D.Jaro),
    "jaro_winkler":       _norm(D.JaroWinkler),          # + viés prefixal
    # sensíveis a palavra, não a caractere: relevantes para NOME, onde a ordem
    # das palavras e a omissão de nomes do meio são o erro humano típico
    "token_sort":         lambda a, b: fuzz.token_sort_ratio(a, b) / 100,
    "token_set":          lambda a, b: fuzz.token_set_ratio(a, b) / 100,
    "partial":            lambda a, b: fuzz.partial_ratio(a, b) / 100,
}

# Hamming só existe para strings de mesmo comprimento — vale na matrícula, que
# tem 6 células fixas, e não vale no nome.
METRICAS_MATRICULA = dict(METRICAS)
METRICAS_MATRICULA["hamming"] = lambda a, b: (
    D.Hamming.normalized_similarity(a, b) if len(a) == len(b) else 0.0)
# a variante do projeto: '?' casa com qualquer caractere a custo zero (D02)
METRICAS_MATRICULA["lev_wildcard"] = sim_levenshtein


def aplicar_semantica(s: str, semantica: str) -> str:
    """D02: o que fazer com o '?' antes de entregar à métrica."""
    if semantica == "deleta":
        # remover com "" e não com " ": " " quebraria a palavra em duas
        return " ".join(s.replace(CURINGA, "").split())
    return s


def top1(linhas: list[dict], base: list[dict], campo: str,
         metrica, semantica: str) -> tuple[int, int]:
    """Quantas linhas têm a pessoa CERTA no topo, usando só este canal.

    Um canal por vez, de propósito: misturar os dois com alpha mediria a
    combinação, não a métrica. O canal isolado responde "esta métrica ordena
    bem?", que é a pergunta do objetivo (e).
    """
    chave = "leitura" if campo == "matricula" else "leitura_nome"
    alvo = "matricula" if campo == "matricula" else "nome"
    acertos = 0
    for r in linhas:
        lido = aplicar_semantica(r[chave], semantica)
        if not lido:
            continue
        melhor, campeao = -1.0, None
        for al in base:
            s = metrica(lido, al[alvo])
            if s > melhor:
                melhor, campeao = s, al
        acertos += campeao["matricula"] == r["verdade"]
    return acertos, sum(1 for r in linhas if aplicar_semantica(r[chave], semantica))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Compara métricas de matching (objetivo e).")
    p.add_argument("--modo", default="chuta", choices=("chuta", "abstem"))
    p.add_argument("--registros", default=REGISTROS)
    args = p.parse_args()

    with open(args.registros) as f:
        regs = json.load(f)
    # uma leitura por (folha, linha): a string lida não depende do alpha
    # gravado. Deduplicar em vez de fixar alpha=0,7, que quebrava em silêncio
    # quando o alpha vigente mudava.
    vistas = set()
    linhas = []
    for r in regs:
        chave = (r["folha"], r["linha"])
        if r["modo"] != args.modo or not r["verdade"] or chave in vistas:
            continue
        vistas.add(chave)
        linhas.append(r)
    print(f"{len(linhas)} linhas com verdade conhecida, modo={args.modo}")

    base = carregar_base(BASE_XLSX)
    for a in base:
        a["nome"] = normalizar(a["nome"])       # uma vez, fora dos loops (D05)
    print(f"base vigente: {len(base)} pessoas\n")

    resultados = []
    for campo, tabela in (("matricula", METRICAS_MATRICULA), ("nome", METRICAS)):
        # no modo `chuta` não há '?' emitido pelo reconhecedor, então as duas
        # semânticas coincidem e medir as duas seria desperdício
        chave = "leitura" if campo == "matricula" else "leitura_nome"
        tem_curinga = any(CURINGA in r[chave] for r in linhas)
        semanticas = ("deleta", "literal") if tem_curinga else ("literal",)

        print(f"=== {campo.upper()} ===" +
              ("" if tem_curinga else "   (sem '?' nas leituras: semântica não importa)"))
        print(f"{'métrica':<16} " + " ".join(f"{s:>9}" for s in semanticas))

        t0 = time.perf_counter()
        for nome_m, metrica in tabela.items():
            celulas = []
            for sem in semanticas:
                ok, n = top1(linhas, base, campo, metrica, sem)
                celulas.append(f"{ok:>4}/{n:<4}")
                resultados.append({"campo": campo, "metrica": nome_m,
                                   "semantica": sem, "top1": ok, "n": n})
            marca = ""
            if (campo == "matricula" and nome_m == "lev_wildcard") or \
               (campo == "nome" and nome_m == "jaro_winkler"):
                marca = "  <- o que o D01 usa hoje"
            print(f"{nome_m:<16} " + " ".join(celulas) + marca)
        print(f"({time.perf_counter() - t0:.1f} s)\n")

    with open(SAIDA, "w") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    print(f"{len(resultados)} medições em {SAIDA}")
    print(f"ressalva: n={len(linhas)}. Diferenças de 1-2 linhas não significam nada.")
