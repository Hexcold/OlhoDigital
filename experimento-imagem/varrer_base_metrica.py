"""Itens 0d e 0e do backlog: A23 e A24 testadas JUNTAS, e theta recalibrado.

A23 e A24 descobriram o mesmo problema por caminhos independentes — a forma
como a pessoa escreve o nome na folha nao e a forma que a base guarda:

    escrito na folha   DAVID S OLIVEIRA
    guardado na base   DAVID SENA OLIVEIRA      Jaro-Winkler 0,918, estando CERTO

e propuseram remedios opostos:

    A23  muda a BASE     reduz o nome da base a forma que o protocolo pede
    A24  muda a METRICA  partial_ratio procura o melhor trecho, nao o prefixo

Nunca foram testadas juntas. Podem somar, podem ser redundantes (a reducao
tirando do partial_ratio exatamente o desalinhamento que o justificava), e ate
podem brigar. Este modulo cruza as duas.

## O que varia, e o que nao

Varia a forma da base, a metrica do nome, a semantica do '?', alpha, theta e
delta. NAO varia o reconhecedor: as leituras sao as mesmas de
`dados/identificacao_folhas.json`, produzidas pelo pipeline real com CNN nas
letras. E nao varia o canal da matricula, que fica em `lev_wildcard` — A24 ja
mediu doze metricas ali e confirmou o D01, entao move-lo so somaria ruido.

## O que a estrutura de cache economiza

`s_mat` nao depende de alpha, nem da forma da base, nem da metrica do nome.
O `varrer_alpha_theta.py` a recalcula uma vez por alpha; aqui ela e calculada
uma vez por modo (1,3 s de Wagner-Fischer puro) e reaproveitada. Depois disso
alpha vira `alpha*M + (1-alpha)*N` sobre matrizes em cache, e theta e delta sao
um corte sobre o resultado. Pontue uma vez, limiarize muitas.

## As tres leituras de saida

1. **Canal do nome isolado** (sem alpha, sem gate). Extende a tabela de A24 com
   o eixo da forma da base. Reporta tambem a SEPARACAO — a distancia entre o
   score do nome certo e o do melhor impostor —, que e o que distingue "separa
   os grupos" (o que A23 alega) de "so desloca o limiar". Com n=40 esse
   diagnostico diz mais do que a taxa.
2. **Ranking do score composto** por alpha. E a metrica de A24 (top1), agora com
   os dois canais.
3. **O gate**: precisao/cobertura, e as combinacoes que zeram o erro. E o item
   0e — a distribuicao de score do partial_ratio e outra, entao o theta de hoje
   nao transfere.

## A ressalva de desenho, que nao pode sumir do relato

A23 supoe que a pessoa ESCREVE na forma reduzida. Nas folhas de desenvolvimento
ela nao escreveu: o preenchimento foi livre, e A12 mostra o quanto. Entao aqui
mede-se a versao fraca — "reduzir a base ajuda mesmo sob preenchimento livre?".
A versao forte so e testavel depois de imprimir a folha orientada. Resultado
nulo ou negativo continua informativo, e e a evidencia que o item 1 do backlog
(decidir 28 celulas e imprimir) precisa.

Uso:
    python varrer_base_metrica.py
    python varrer_base_metrica.py --modo abstem
    python varrer_base_metrica.py --modo ambos --top 20
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import statistics
import time

import numpy as np
from rapidfuzz.distance import JaroWinkler
from rapidfuzz import fuzz

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base                     # noqa: E402
from normalizacao import normalizar                       # noqa: E402
from fuzzy import sim_levenshtein                         # noqa: E402

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "dados/identificacao_folhas.json"
SAIDA = "dados/varredura_base_metrica.json"
CURINGA = "?"

# A folha de desenvolvimento tem 18 celulas de nome. A06 recomenda 28, mas as
# folhas ja impressas tem 18, entao a forma que a pessoa PODERIA escrever nelas
# e a reduzida truncada em 18.
CELULAS_NOME = 18

ALPHAS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
# grade de theta estendida nos dois sentidos: o partial_ratio pontua mais alto
# que o Jaro-Winkler e a base reduzida sobe o score dos acertos, entao o theta
# de hoje (0,85) pode ficar baixo demais, nao so alto demais.
THETAS = (0.98, 0.95, 0.92, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60)
DELTAS = (0.00, 0.05, 0.10, 0.17)


# --- as formas da base -------------------------------------------------------
# Cada uma e uma funcao deterministica do nome completo normalizado, aplicada
# identicamente a todo candidato. E isso que mantem a comparacao honesta: muda o
# contradominio, nao o tratamento de um candidato em relacao a outro.

def _completa(n: str) -> str:
    return n


def _prim_ini_ult(n: str) -> str:
    """Primeiro + iniciais do meio + ultimo. A recomendacao de A06:
    media 17,5 caracteres, maximo 27, cabe 100% da base em 28 celulas."""
    p = n.split()
    if len(p) <= 2:
        return n
    return " ".join([p[0]] + [w[0] for w in p[1:-1]] + [p[-1]])


def _prim_ult(n: str) -> str:
    """Primeiro + ultimo. Mais curto, mas A06 mediu 1165 colisoes contra 443."""
    p = n.split()
    return n if len(p) <= 2 else f"{p[0]} {p[-1]}"


def _truncar(f, k: int):
    """Corta em k caracteres. Corta no meio da palavra de proposito: e o que a
    folha faz fisicamente (A12, linha 21: 'MARIA A B C SOUS')."""
    return lambda n: f(n)[:k].rstrip()


FORMAS_BASE = {
    "completa":         _completa,                            # o que se usa hoje
    "completa_18":      _truncar(_completa, CELULAS_NOME),
    "prim_ini_ult":     _prim_ini_ult,                        # recomendacao A06
    "prim_ini_ult_18":  _truncar(_prim_ini_ult, CELULAS_NOME),
    "prim_ult":         _prim_ult,
}


# --- as metricas do nome -----------------------------------------------------

METRICAS = {
    "jaro_winkler": lambda a, b: JaroWinkler.normalized_similarity(a, b),  # D01
    "partial":      lambda a, b: fuzz.partial_ratio(a, b) / 100,           # A24
}


def aplicar_semantica(s: str, semantica: str) -> str:
    """D02: o que fazer com o '?' antes de entregar a metrica.

    A24 achou que a resposta certa depende da METRICA, nao do canal: o JW
    prefere `deleta` (21 x 19) e o partial_ratio prefere `literal` (26 x 15).
    Por isso a semantica entra como fator, e nao como constante.
    """
    if semantica == "deleta":
        # remover com "" e nao com " ": " " quebraria a palavra em duas (D02)
        return " ".join(s.replace(CURINGA, "").split())
    return s


# --- as matrizes de score ----------------------------------------------------

def matriz_matricula(linhas: list[dict], matriculas: list[str]) -> np.ndarray:
    """s_mat[i][j]: Levenshtein-wildcard da leitura i contra a matricula j.

    Independe de alpha, da forma da base e da metrica do nome — por isso e
    calculada uma vez so. E o unico pedaco caro (Wagner-Fischer em Python puro).
    """
    return np.array([[sim_levenshtein(r["leitura"], m) for m in matriculas]
                     for r in linhas], dtype=np.float64)


def matriz_nome(linhas: list[dict], nomes: list[str],
                metrica, semantica: str) -> np.ndarray:
    """s_nome[i][j]: similaridade do nome lido i contra o nome-da-base j."""
    lidos = [aplicar_semantica(r["leitura_nome"], semantica) for r in linhas]
    return np.array([[metrica(lido, n) for n in nomes] for lido in lidos],
                    dtype=np.float64)


def top2(S: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Campeao, seu score e o score do segundo, por linha.

    Desempate pelo primeiro da base, igual ao `if s > melhor` do
    `fuzzy.identificar()` — se dois candidatos empatam no topo, a margem sai
    zero e o gate manda para a quarentena, que e o comportamento correto.
    """
    idx = S.argmax(axis=1)                      # argmax devolve o PRIMEIRO maximo
    ordenado = np.sort(S, axis=1)               # crescente
    s1 = ordenado[:, -1]
    s2 = ordenado[:, -2] if S.shape[1] >= 2 else np.full(S.shape[0], -1.0)
    return idx, s1, s2


def resumir(s1: np.ndarray, s2: np.ndarray, acertou: np.ndarray,
            na_base: np.ndarray, theta: float, delta: float) -> dict:
    """Aplica o gate. Aceitar quem esta fora da base e erro, nao caso a parte."""
    aceitos = (s1 >= theta) & ((s1 - s2) >= delta)
    n_ac = int(aceitos.sum())
    certos = int((aceitos & acertou & na_base).sum())
    return {
        "n": int(len(s1)),
        "top1": int((acertou & na_base).sum()),
        "aceitos": n_ac,
        "certos": certos,
        "errados": n_ac - certos,
        "precisao": certos / n_ac if n_ac else float("nan"),
        "cobertura": n_ac / len(s1) if len(s1) else float("nan"),
    }


def separacao(N: np.ndarray, idx_verdade: np.ndarray) -> dict:
    """O poder discriminante do canal do nome, sem alpha e sem gate.

    Para cada linha: o score do nome contra a pessoa CERTA, e o melhor score
    contra qualquer OUTRA. A23 alega que reduzir a base *separa os grupos* em
    vez de deslocar o limiar; a diferenca entre as duas medianas e exatamente
    essa alegacao, e ela nao depende de alpha nem de theta.

    Linhas cuja verdade nao esta na base ficam de fora: nao ha "score do certo".
    """
    certos, impostores = [], []
    for i, j in enumerate(idx_verdade):
        if j < 0:
            continue
        linha = N[i].copy()
        certos.append(float(linha[j]))
        linha[j] = -np.inf                      # o melhor entre os OUTROS
        impostores.append(float(linha.max()))
    if not certos:
        return {"n": 0}
    m_certo, m_imp = statistics.median(certos), statistics.median(impostores)
    return {
        "n": len(certos),
        "certo": m_certo,
        "impostor": m_imp,
        "sep": m_certo - m_imp,
        # em quantas linhas a pessoa certa GANHA de todos os outros so pelo nome
        "vence": sum(c > i for c, i in zip(certos, impostores)),
    }


def carregar_linhas(caminho: str, modo: str) -> list[dict]:
    """Uma leitura por (folha, linha) neste modo.

    O JSON tem um registro por alpha, mas a STRING LIDA nao depende de alpha —
    so o score depende, e o score aqui e recalculado do zero. Entao deduplica-se
    por (folha, linha) em vez de filtrar por um alpha fixo: filtrar por 0,7
    quebrava silenciosamente toda vez que o alpha vigente mudava.
    """
    with open(caminho) as f:
        regs = json.load(f)
    vistas, saida = set(), []
    for r in regs:
        chave = (r["folha"], r["linha"])
        if r["modo"] != modo or not r["verdade"] or chave in vistas:
            continue
        vistas.add(chave)
        saida.append(r)
    return saida


def rodar_modo(modo: str, linhas: list[dict], base: list[dict],
               top: int) -> list[dict]:
    """Cruza forma-da-base x metrica x semantica x alpha x theta x delta."""
    matriculas = [a["matricula"] for a in base]
    verdades = [r["verdade"] for r in linhas]
    na_base = np.array([bool(r["verdade_na_base"]) for r in linhas])

    # posicao da pessoa certa na base, -1 se ela nao esta la
    pos = {m: j for j, m in enumerate(matriculas)}
    idx_verdade = np.array([pos.get(v, -1) for v in verdades])

    print(f"\n{'='*78}\nMODO {modo.upper()}  —  {len(linhas)} linhas, "
          f"{int(na_base.sum())} com a verdade na base\n{'='*78}")

    t0 = time.perf_counter()
    M = matriz_matricula(linhas, matriculas)
    print(f"s_mat {M.shape} calculada uma vez: {time.perf_counter() - t0:.2f} s")

    # no modo `chuta` o reconhecedor nao emite '?', entao as duas semanticas
    # coincidem e medir as duas seria desperdicio. Conferido, nao suposto.
    tem_curinga = any(CURINGA in r["leitura_nome"] for r in linhas)
    semanticas = ("deleta", "literal") if tem_curinga else ("literal",)
    if not tem_curinga:
        print("nenhum '?' nas leituras de nome: a semantica do D02 nao morde aqui")

    # --- bloco 1: o canal do nome isolado ------------------------------------
    print(f"\n1. CANAL DO NOME ISOLADO (sem alpha, sem gate)\n"
          f"   top1 = a pessoa certa vence so pelo nome | sep = mediana(certo) - mediana(melhor impostor)\n")
    print(f"   {'forma da base':<17} {'metrica':<13} {'sem':<8} "
          f"{'top1':>7} {'certo':>7} {'impost':>7} {'sep':>7} {'empate':>7}")

    cache_nome: dict[tuple, np.ndarray] = {}
    t0 = time.perf_counter()
    for forma, fn in FORMAS_BASE.items():
        nomes = [fn(a["nome"]) for a in base]
        for nome_m, metrica in METRICAS.items():
            for sem in semanticas:
                N = matriz_nome(linhas, nomes, metrica, sem)
                cache_nome[(forma, nome_m, sem)] = N
                s = separacao(N, idx_verdade)
                idx, s1, s2 = top2(N)
                acertou_nome = np.array(
                    [matriculas[j] == v for j, v in zip(idx, verdades)])
                empates = int((s1 == s2).sum())
                marca = "  <- D01 hoje" if (forma == "completa"
                                            and nome_m == "jaro_winkler"
                                            and sem == "deleta") else ""
                print(f"   {forma:<17} {nome_m:<13} {sem:<8} "
                      f"{int((acertou_nome & na_base).sum()):>3}/{len(linhas):<3} "
                      f"{s['certo']:>7.3f} {s['impostor']:>7.3f} "
                      f"{s['sep']:>7.3f} {empates:>7}{marca}")
    print(f"   ({time.perf_counter() - t0:.1f} s, "
          f"{len(cache_nome)} matrizes de nome)")

    # --- bloco 2: ranking do score composto ----------------------------------
    print(f"\n2. RANKING DO SCORE COMPOSTO — top1 por alpha (o gate nao entra)\n")
    print(f"   {'forma da base':<17} {'metrica':<13} {'sem':<8} "
          + "".join(f"{a:>6.1f}" for a in ALPHAS))

    crus: list[dict] = []
    pontos: dict[tuple, tuple] = {}
    for (forma, nome_m, sem), N in cache_nome.items():
        celulas = []
        for alpha in ALPHAS:
            S = alpha * M + (1 - alpha) * N
            idx, s1, s2 = top2(S)
            acertou = np.array([matriculas[j] == v for j, v in zip(idx, verdades)])
            pontos[(forma, nome_m, sem, alpha)] = (s1, s2, acertou, na_base)
            celulas.append(int((acertou & na_base).sum()))
            for i, r in enumerate(linhas):
                crus.append({
                    "modo": modo, "forma": forma, "metrica": nome_m,
                    "semantica": sem, "alpha": alpha,
                    "folha": r["folha"], "linha": r["linha"],
                    "s1": float(s1[i]), "s2": float(s2[i]),
                    "margem": float(s1[i] - s2[i]),
                    "acertou": bool(acertou[i]), "na_base": bool(na_base[i]),
                })
        print(f"   {forma:<17} {nome_m:<13} {sem:<8} "
              + "".join(f"{c:>6}" for c in celulas))

    # --- bloco 3: o gate, item 0e --------------------------------------------
    print(f"\n3. O GATE — combinacoes com ZERO erro, ordenadas por cobertura "
          f"(top {top})\n")
    print(f"   {'forma da base':<17} {'metrica':<13} {'sem':<8} "
          f"{'alpha':>6} {'theta':>6} {'delta':>6} {'aceit':>6} {'precis':>7} {'cobert':>7}")
    limpos = []
    for chave, (s1, s2, acertou, nb) in pontos.items():
        for t in THETAS:
            for d in DELTAS:
                m = resumir(s1, s2, acertou, nb, t, d)
                if m["aceitos"] and m["errados"] == 0:
                    limpos.append((m["cobertura"], chave, t, d, m))
    limpos.sort(reverse=True, key=lambda x: (x[0], -x[1][3]))
    for cob, (forma, nome_m, sem, alpha), t, d, m in limpos[:top]:
        print(f"   {forma:<17} {nome_m:<13} {sem:<8} {alpha:>6.1f} {t:>6.2f} "
              f"{d:>6.2f} {m['aceitos']:>6} {m['precisao']:>7.3f} {cob:>7.3f}")
    if not limpos:
        print("   nenhuma combinacao zera os erros nesta amostra")

    # o ponto de referencia de hoje, para comparar contra o topo da lista
    print(f"\n   referencia (o que roda hoje e o que 0b escolheu):")
    for forma, nome_m, sem, alpha, t, d, rot in (
            ("completa", "jaro_winkler", semanticas[0], 0.7, 0.85, 0.05, "D01/D03 do texto"),
            ("completa", "jaro_winkler", semanticas[0], 0.4, 0.65, 0.05, "melhor de 0b")):
        s1, s2, ac, nb = pontos[(forma, nome_m, sem, alpha)]
        m = resumir(s1, s2, ac, nb, t, d)
        print(f"   {forma:<17} {nome_m:<13} {sem:<8} {alpha:>6.1f} {t:>6.2f} "
              f"{d:>6.2f} {m['aceitos']:>6} {m['precisao']:>7.3f} "
              f"{m['cobertura']:>7.3f}   {rot}")

    return crus


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Itens 0d e 0e: A23 x A24 cruzadas, e theta recalibrado.")
    p.add_argument("--modo", default="ambos", choices=("chuta", "abstem", "ambos"))
    p.add_argument("--registros", default=REGISTROS)
    p.add_argument("--saida", default=SAIDA)
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()

    base = carregar_base(BASE_XLSX)
    for a in base:
        a["nome"] = normalizar(a["nome"])       # uma vez, fora dos loops (D05)
    print(f"base vigente: {len(base)} pessoas")

    # o que cada forma da base faz com um nome de exemplo — sanidade visivel
    exemplo = next((a["nome"] for a in base if len(a["nome"].split()) >= 4),
                   base[0]["nome"])
    print(f"formas da base, sobre {exemplo!r}:")
    for forma, fn in FORMAS_BASE.items():
        print(f"  {forma:<17} {fn(exemplo)!r}")

    modos = ("chuta", "abstem") if args.modo == "ambos" else (args.modo,)
    t0 = time.perf_counter()
    crus = []
    for modo in modos:
        linhas = carregar_linhas(args.registros, modo)
        crus.extend(rodar_modo(modo, linhas, base, args.top))

    os.makedirs("dados", exist_ok=True)
    with open(args.saida, "w") as f:
        json.dump(crus, f, ensure_ascii=False, indent=1)
    print(f"\n{len(crus)} pontuacoes cruas em {args.saida} "
          f"({time.perf_counter() - t0:.1f} s no total)")
    print("ressalva: n=40, e escolher a configuracao olhando estas 40 linhas e "
          "calibrar\nno conjunto de desenvolvimento. A taxa de verdade vem da "
          "coleta dos Encontros.")
