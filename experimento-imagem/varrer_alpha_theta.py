"""Varredura conjunta de alpha, theta e delta sobre as leituras já feitas.

Item 0b do backlog. Responde a pergunta que a primeira revisão humana levantou:
o canal do nome está sendo aproveitado?

## Por que os três juntos, e não um de cada vez

alpha e theta são acoplados por construção, e calibrar um sem o outro engana.

O score de um acerto típico é `alpha·1,0 + (1-alpha)·JW`, onde JW é a
similaridade entre o nome LIDO e o nome verdadeiro — que não é 1,0, porque o
OCR de letras erra: mediana medida de 0,703. Então baixar alpha **baixa o teto
do score dos acertos**, e um theta fixo passa a barrar identificações corretas.

Medido: de alpha 0,6 para 0,4 o top1 sobe (30 -> 32) e a cobertura cai
(0,425 -> 0,375). O ranking melhora enquanto o gate piora. Quem lê só uma das
duas colunas conclui coisas opostas.

## O princípio de sempre

Pontue uma vez, limiarize muitas. O pipeline de imagem já rodou e as leituras
estão em `dados/identificacao_folhas.json`; aqui só o MATCHING é refeito, o que
custa ~20 ms por linha por alpha. theta e delta saem de graça depois, porque
não mudam o score — só o corte.

Uso:
    python varrer_alpha_theta.py
    python varrer_alpha_theta.py --modo abstem --top 15
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base                     # noqa: E402
from normalizacao import normalizar                       # noqa: E402
from fuzzy import sim_levenshtein, sim_jaro_winkler       # noqa: E402

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "dados/identificacao_folhas.json"
SAIDA = "dados/varredura_alpha_theta.json"

ALPHAS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
THETAS = (0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65)
DELTAS = (0.00, 0.05, 0.10, 0.17)


def pontuar(linhas: list[dict], base: list[dict], alpha: float) -> list[dict]:
    """Repontua as MESMAS leituras com um alpha, guardando o par (s1, s2).

    Guarda só o que os limiares precisam: o score do campeão, o do segundo e se
    o campeão é a pessoa certa. theta e delta se aplicam depois, sem repagar.
    """
    saida = []
    for r in linhas:
        melhor = segundo = -1.0
        campeao = None
        for al in base:
            s_mat = sim_levenshtein(r["leitura"], al["matricula"])
            s_nome = (sim_jaro_winkler(r["leitura_nome"], al["nome"])
                      if alpha < 1.0 else 0.0)
            s = alpha * s_mat + (1 - alpha) * s_nome
            if s > melhor:
                melhor, segundo, campeao = s, melhor, al
            elif s > segundo:
                segundo = s
        saida.append({
            "folha": r["folha"], "linha": r["linha"], "alpha": alpha,
            "s1": melhor, "s2": segundo, "margem": melhor - segundo,
            "acertou": campeao["matricula"] == r["verdade"],
            "na_base": r["verdade_na_base"],
        })
    return saida


def resumir(pontos: list[dict], theta: float, delta: float) -> dict:
    """Aplica o gate. Aceitar quem está fora da base é erro, não caso à parte."""
    aceitos = [p for p in pontos if p["s1"] >= theta and p["margem"] >= delta]
    certos = sum(1 for p in aceitos if p["acertou"] and p["na_base"])
    return {
        "n": len(pontos),
        "top1": sum(p["acertou"] for p in pontos),
        "aceitos": len(aceitos),
        "certos": certos,
        "errados": len(aceitos) - certos,
        "precisao": certos / len(aceitos) if aceitos else float("nan"),
        "cobertura": len(aceitos) / len(pontos) if pontos else float("nan"),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Varre alpha x theta x delta.")
    p.add_argument("--modo", default="chuta", choices=("chuta", "abstem"))
    p.add_argument("--registros", default=REGISTROS)
    p.add_argument("--saida", default=SAIDA)
    p.add_argument("--top", type=int, default=12,
                   help="quantas combinações listar no ranking final")
    args = p.parse_args()

    with open(args.registros) as f:
        regs = json.load(f)
    # uma leitura por (folha, linha): o alpha gravado não importa aqui, só a
    # string lida, que não depende dele. Deduplicar em vez de fixar alpha=0,7 —
    # senão isto quebra em silêncio toda vez que o alpha vigente muda.
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
    print(f"base vigente: {len(base)} pessoas")

    t0 = time.perf_counter()
    todos = []
    for alpha in ALPHAS:
        todos.extend(pontuar(linhas, base, alpha))
    print(f"pontuação: {time.perf_counter() - t0:.1f} s "
          f"({len(ALPHAS)} alphas x {len(linhas)} linhas)\n")

    por_alpha = {a: [p for p in todos if p["alpha"] == a] for a in ALPHAS}

    # 1. o efeito de alpha no RANKING, que independe de theta e delta
    print("ranking (independe do gate):")
    print(f"{'alpha':>6} {'top1':>5} {'de':>4}")
    for a in ALPHAS:
        m = resumir(por_alpha[a], 0.0, 0.0)
        print(f"{a:>6.1f} {m['top1']:>5} {m['n']:>4}")

    # 2. a superfície: precisão e cobertura por (alpha, theta), delta fixo
    for delta in (0.05,):
        print(f"\nprecisão / cobertura por alpha x theta (delta={delta}):")
        print("       " + "".join(f"{t:>14.2f}" for t in THETAS))
        for a in ALPHAS:
            celulas = []
            for t in THETAS:
                m = resumir(por_alpha[a], t, delta)
                celulas.append(f"{m['precisao']:>6.3f}/{m['cobertura']:<7.3f}"
                               if m["aceitos"] else f"{'—':>13}")
            print(f"{a:>6.1f} " + "".join(celulas))

    # 3. ranking: melhor cobertura entre as combinações SEM erro
    print(f"\ncombinações com ZERO erro, por cobertura (top {args.top}):")
    print(f"{'alpha':>6} {'theta':>6} {'delta':>6} {'aceitos':>8} {'precis':>7} {'cobert':>7}")
    limpos = []
    for a in ALPHAS:
        for t in THETAS:
            for d in DELTAS:
                m = resumir(por_alpha[a], t, d)
                if m["aceitos"] and m["errados"] == 0:
                    limpos.append((m["cobertura"], a, t, d, m))
    limpos.sort(reverse=True, key=lambda x: x[0])
    for cob, a, t, d, m in limpos[:args.top]:
        print(f"{a:>6.1f} {t:>6.2f} {d:>6.2f} {m['aceitos']:>8} "
              f"{m['precisao']:>7.3f} {cob:>7.3f}")
    if not limpos:
        print("  nenhuma combinação zera os erros nesta amostra")

    os.makedirs("dados", exist_ok=True)
    with open(args.saida, "w") as f:
        json.dump(todos, f, ensure_ascii=False, indent=1)
    print(f"\n{len(todos)} pontuações cruas em {args.saida}")
    print(f"ressalva: n={len(linhas)}. As taxas não estabelecem nada — a "
          f"calibração de verdade vem da coleta dos Encontros.")
