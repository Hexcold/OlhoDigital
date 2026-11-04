"""Validação deixando uma folha de fora, sobre o ponto de operação (alpha, theta, delta).

Responde à objeção de que o ponto de operação é in-sample: ele saiu de uma
varredura sobre folhas de desenvolvimento, e a precisão 1,000 é relatada em 67
linhas que incluem 22 dessas. A pergunta que isto mede é mais dura:

    se o ponto for escolhido SEM VER a folha que vai ser medida, o que sobra?

## Desenho

Seis dobras, uma por folha. Em cada uma o ponto é escolhido nas outras cinco por
regra declarada e determinística — menos erros primeiro, mais aceitos depois — e
aplicado à folha retida. O critério empata muito (vários pontos com 0 erros e a
mesma cobertura), e aqui NÃO se inventa desempate: avalia-se o conjunto ótimo
inteiro na folha retida e reportam-se o pior e o melhor caso. O pior é o número
honesto, porque é o que uma escolha azarada entregaria.

Mede o PROCEDIMENTO de escolha, não o ponto: a escolha histórica também teve
julgamento (o planalto de A27), que nenhuma regra automática reproduz.

## Princípio de sempre

Pontue uma vez, limiarize muitas. O pipeline de imagem não roda aqui — as
leituras vêm de `dados/id_ampliado.json`. Só o matching é refeito, e só porque
alpha muda o score; theta e delta saem de graça depois.

O matcher é IMPORTADO de `identificar_folha.ranquear`, não reescrito: duplicar a
regra é o defeito que manteve theta=0,85 rodando três sessões.

Uso:
    python loso_ponto_operacao.py
"""

from __future__ import annotations
import json
import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base          # noqa: E402
from normalizacao import normalizar            # noqa: E402
from identificar_folha import ranquear         # noqa: E402

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "dados/id_ampliado.json"
SAIDA = "dados/loso_ponto_operacao.json"

MODO = "chuta"                      # a configuração que o resumo relata
ALPHAS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
THETAS = tuple(round(0.95 - 0.05 * i, 2) for i in range(14))    # 0,95 .. 0,30
DELTAS = (0.00, 0.02, 0.05, 0.10, 0.17)
VIGENTE = (0.5, 0.60, 0.05)

# A Tabela 1 do resumo, que a repontuação tem que reproduzir antes de qualquer
# conclusão: (alpha, delta) -> (aceitos, errados), com theta=0,60.
TABELA_RESUMO = {(1.0, 0.00): (66, 9), (1.0, 0.05): (56, 2),
                 (0.5, 0.00): (66, 0), (0.5, 0.05): (65, 0)}


def pontuar(linhas: list[dict], base: list[dict]) -> list[dict]:
    """Repontua as MESMAS leituras com cada alpha, guardando (s1, margem, acertou).

    `acertou` segue a regra de `identificar_folha.avaliar_linha`: o campeão do
    ranking é a pessoa certa, antes do gate.
    """
    saida = []
    for r in linhas:
        for alpha in ALPHAS:
            topo = ranquear(r["leitura"], r["leitura_nome"], base, alpha)
            s1, s2 = topo[0][0], topo[1][0]
            saida.append({"folha": r["folha"], "linha": r["linha"], "alpha": alpha,
                          "s1": s1, "margem": s1 - s2,
                          "acertou": topo[0][1]["matricula"] == r["verdade"]})
    return saida


def contar(pontos: list[dict], alpha: float, theta: float,
           delta: float) -> tuple[int, int]:
    """(aceitos, errados) de um ponto de operação sobre um conjunto de linhas."""
    aceitos = [p for p in pontos
               if p["alpha"] == alpha and p["s1"] >= theta and p["margem"] >= delta]
    return len(aceitos), sum(1 for p in aceitos if not p["acertou"])


def otimos(pontos: list[dict]) -> list[tuple[float, float, float]]:
    """Conjunto ótimo pela regra declarada: menos erros, depois mais aceitos.

    Devolve TODOS os empatados. Escolher um com a mão seria decidir justamente o
    que o experimento existe para medir.
    """
    placar = {(a, t, d): contar(pontos, a, t, d)
              for a in ALPHAS for t in THETAS for d in DELTAS}
    melhor = min((err, -ac) for ac, err in placar.values())
    return [p for p, (ac, err) in placar.items() if (err, -ac) == melhor]


def main() -> None:
    t0 = time.time()
    base = carregar_base(BASE_XLSX)
    for a in base:
        a["nome"] = normalizar(a["nome"])      # uma vez, fora dos loops (D05)

    with open(REGISTROS) as f:
        cru = json.load(f)
    linhas = [r for r in cru
              if r["modo"] == MODO and r["alpha"] == 1.0 and r["verdade"]]
    folhas = sorted({r["folha"] for r in linhas})
    por_folha = {f: sum(1 for r in linhas if r["folha"] == f) for f in folhas}
    print(f"{len(linhas)} linhas com gabarito em {len(folhas)} folhas: {por_folha}")

    pontos = pontuar(linhas, base)
    with open(SAIDA, "w") as f:
        json.dump(pontos, f, ensure_ascii=False)
    print(f"repontuado em {time.time() - t0:.1f}s -> {len(pontos)} registros\n")

    print("== self-check contra a Tabela 1 do resumo ==")
    ok = True
    for (alpha, delta), esperado in TABELA_RESUMO.items():
        obtido = contar(pontos, alpha, 0.60, delta)
        ok &= obtido == esperado
        print(f"  {'OK ' if obtido == esperado else 'ERRO'} alpha={alpha} "
              f"delta={delta:.2f}: obtido {obtido}, esperado {esperado}")
    if not ok:
        sys.exit("self-check falhou — a repontuação não reproduz o publicado")

    print("\n== o ponto vigente, folha a folha ==")
    for f in folhas:
        ac, err = contar([p for p in pontos if p["folha"] == f], *VIGENTE)
        print(f"  {f:8} {por_folha[f]:>3} linhas -> {ac} aceitos, {err} errados")

    print("\n== LOSO por folha ==")
    print(f"  {'retida':8} {'|ótimos|':>8} {'vigente é ótimo?':>17} "
          f"{'pior':>9} {'melhor':>9}")
    conjuntos, dobras = {}, []
    total_pior, total_melhor = [0, 0], [0, 0]
    for f in folhas:
        treino = [p for p in pontos if p["folha"] != f]
        retida = [p for p in pontos if p["folha"] == f]
        conj = otimos(treino)
        conjuntos[f] = set(conj)
        avaliados = [contar(retida, *p) for p in conj]
        pior = max(avaliados, key=lambda t: (t[1], -t[0]))     # mais erros
        melhor = min(avaliados, key=lambda t: (t[1], -t[0]))
        print(f"  {f:8} {len(conj):>8} {str(VIGENTE in conj):>17} "
              f"{str(pior):>9} {str(melhor):>9}")
        total_pior = [total_pior[0] + pior[0], total_pior[1] + pior[1]]
        total_melhor = [total_melhor[0] + melhor[0], total_melhor[1] + melhor[1]]
        dobras.append({"folha": f, "n": por_folha[f], "otimos": sorted(conj),
                       "vigente_e_otimo": VIGENTE in conj,
                       "retida_pior": pior, "retida_melhor": melhor})

    comum = set.intersection(*conjuntos.values())
    print(f"\nintersecção das 6 dobras: {len(comum)} pontos · "
          f"alphas {sorted({a for a, _, _ in comum})} · "
          f"thetas {min(t for _, t, _ in comum)}-{max(t for _, t, _ in comum)} · "
          f"deltas {sorted({d for _, _, d in comum})}")

    n = len(linhas)
    for rotulo, (ac, err) in (("pior", total_pior), ("melhor", total_melhor)):
        print(f"agregado {rotulo:7}: {ac} aceitos, {err} errados, "
              f"precisão {(ac - err) / ac:.3f}, cobertura {ac / n:.3f}")
    ac, err = contar(pontos, *VIGENTE)
    print(f"ponto vigente (in-sample): {ac} aceitos, {err} errados, "
          f"precisão {(ac - err) / ac:.3f}, cobertura {ac / n:.3f}")

    with open(SAIDA.replace(".json", "_dobras.json"), "w") as f:
        json.dump(dobras, f, ensure_ascii=False, indent=1)
    print(f"\ntotal {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
