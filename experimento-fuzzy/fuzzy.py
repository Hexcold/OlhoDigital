"""Correspondência aproximada entre a leitura imperfeita e a base (D01, D02, D03).

## O ponto de operação, e de onde ele veio

    S = alpha * sim_matricula + (1 - alpha) * sim_nome
    aceito  <=>  S1 >= theta  E  (S1 - S2) >= delta

Os valores abaixo NÃO são os do texto do TCC (alpha=0,7, theta=0,85, Jaro-Winkler).
Aqueles vinham do simulador do TCC 1; estes foram medidos no pipeline real com CNN nas
letras, sobre 40 linhas das folhas de desenvolvimento:

    A24  partial_ratio supera o Jaro-Winkler no nome — 36 aceitos com 0 erros contra
         30, superconjunto estrito, McNemar exato p=0,031
    A22  alpha=0,7 não deixa o nome vetar nada; a 0,5 ele veta
    A27  theta é planalto abaixo de 0,60; quem faz o gate é delta

RESSALVA que precisa viajar com os números: são calibrados no conjunto de
desenvolvimento (n=40). A calibração de verdade vem da coleta dos Encontros.

## A semântica do '?' pertence à MÉTRICA, não ao canal

Nenhuma métrica de biblioteca conhece o curinga de D02. O texto do TCC enuncia a regra
por canal — "no nome, remover o '?' antes de calcular" —, e A24 mostrou que isso está
mal enunciado: para o Jaro-Winkler remover é melhor (21 x 19), para o partial_ratio é
muito pior (15 x 26). Removendo os curingas o nome fragmenta e o trecho que sobra casa
com qualquer um; mantendo, o '?' penaliza todos os candidatos por igual e a ordem
relativa sobrevive, que é o que o ranking precisa.

Por isso cada entrada de `SIM_NOME` aplica a PRÓPRIA semântica por dentro. O par
(métrica, semântica) é uma coisa só e não tem como ser descasado por quem chama.
"""

from rapidfuzz.distance import JaroWinkler
from rapidfuzz import fuzz

CURINGA = "?"

# ponto de operação vigente — ver o cabeçalho para a procedência de cada um
METRICA_NOME = "partial"
ALPHA = 0.5
THETA = 0.60
DELTA = 0.05


def dist_levenshtein(s1: str, s2: str) -> int:
    """Distância de edição (Wagner-Fischer) com wildcard:
    '?' casa com qualquer caractere a custo zero (abstenção do OCR)."""
    m, n = len(s1), len(s2)

    D = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        D[i][0] = i
    for j in range(n + 1):
        D[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1] or s1[i-1] == "?" or s2[j-1] == "?":
                custo = 0
            else:
                custo = 1
            D[i][j] = min(
                D[i-1][j] + 1,       # deleção
                D[i][j-1] + 1,       # inserção
                D[i-1][j-1] + custo  # pareamento: grátis, wildcard ou substituição
            )

    return D[m][n]

def sim_levenshtein(s1: str, s2: str) -> float:
    """Similaridade normalizada [0,1] da Eq. 2.1 do TCC,
    sobre a distância com wildcard."""
    denom = max(len(s1), len(s2))
    if denom == 0:
        return 1.0
    return 1.0 - dist_levenshtein(s1, s2) / denom


def sim_jaro_winkler(s1: str, s2: str) -> float:
    """Jaro-Winkler com o '?' REMOVIDO antes do cálculo.

    Semântica de deleção, que é a que A24 mediu como melhor para esta métrica
    (21 x 19). Remover com "" e nunca com " ": " " quebraria a palavra em duas.
    """
    limpo = " ".join(s1.replace(CURINGA, "").split())
    return JaroWinkler.similarity(limpo, s2)


def sim_partial(s1: str, s2: str) -> float:
    """`partial_ratio` com o '?' MANTIDO como caractere literal.

    Semântica oposta à do Jaro-Winkler, e é essa a diferença que A24 achou:
    manter vale 26 contra 15. O `partial_ratio` procura o melhor alinhamento de
    TRECHO, que é a operação que o problema pede — a pessoa escreve um nome
    truncado em 18 células e a base guarda o nome completo, então a relação
    entre os dois é substring, não prefixo.
    """
    return fuzz.partial_ratio(s1, s2) / 100


# o par (métrica, semântica) como uma coisa só — ver o cabeçalho do módulo
SIM_NOME = {
    "jaro_winkler": sim_jaro_winkler,   # D01 do texto; '?' deletado
    "partial": sim_partial,             # A24, vigente; '?' literal
}


def identificar(matricula_lida: str, nome_lido: str, base: list[dict],
                alpha: float = ALPHA, theta: float = THETA,
                delta: float = DELTA, sim_nome=None) -> dict:
    """Compara a leitura contra todos os candidatos da base.

    Score composto: alpha*sim_matricula + (1-alpha)*sim_nome.
    Aceito sse melhor score >= theta E margem sobre o vice >= delta.

    `sim_nome` recebe uma entrada de `SIM_NOME`, que já traz a semântica do '?'
    junto. Passar uma métrica crua do rapidfuzz aqui é o descasamento que A24
    mediu custando 11 linhas de 40.
    """
    if sim_nome is None:
        sim_nome = SIM_NOME[METRICA_NOME]

    melhor = None
    melhor_score = -1.0
    vice_score = -1.0

    for aluno in base:
        s_mat = sim_levenshtein(matricula_lida, aluno["matricula"])
        s_nome = sim_nome(nome_lido, aluno["nome"]) if alpha < 1.0 else 0.0
        score = alpha * s_mat + (1 - alpha) * s_nome

        if score > melhor_score:
            vice_score = melhor_score
            melhor_score = score
            melhor = aluno
        elif score > vice_score:
            vice_score = score

    margem = melhor_score - vice_score
    aceito = melhor_score >= theta and margem >= delta
    return {"aluno": melhor, "score": melhor_score,
            "margem": margem, "decisao": "aceito" if aceito else "quarentena"}




if __name__ == "__main__":
    casos = [
        ("SILVA", "SLVA"),
        ("HENRIQUE", "HE?RIQUE"),
        ("912314", "912314"),
        ("", "ABC"),
        ("9?2314", "912314"),
        ("??????", "912314"),
        ("??????", "999999"),
        ("5?", "5"),
    ]
    for a, b in casos:
        print(f"dist({a!r}, {b!r}) = {dist_levenshtein(a, b)}")

    import random
    rng = random.Random(1)
    simetrica = True
    for _ in range(1000):
        a = "".join(rng.choice("AB?") for _ in range(rng.randint(0, 8)))
        b = "".join(rng.choice("AB?") for _ in range(rng.randint(0, 8)))
        if dist_levenshtein(a, b) != dist_levenshtein(b, a):
            simetrica = False
            print("ASSIMETRIA:", repr(a), repr(b))
    print("Simétrica em 1000 pares aleatórios?", simetrica)
    
    print()
    print("--- 4b: similaridades, e as duas semânticas do '?' ---")
    verdade = "DAVID SENA OLIVEIRA"

    print("Assinatura preguicosa 'DAVID O':")
    print("  Levenshtein :", round(sim_levenshtein("DAVID O", verdade), 3))
    print("  Jaro-Winkler:", round(sim_jaro_winkler("DAVID O", verdade), 3))
    print("  partial     :", round(sim_partial("DAVID O", verdade), 3))
    print("Abreviacao 'DAVID S OLIVEIRA':")
    print("  Jaro-Winkler:", round(sim_jaro_winkler("DAVID S OLIVEIRA", verdade), 3))
    print("  partial     :", round(sim_partial("DAVID S OLIVEIRA", verdade), 3))

    # A24: a semântica do '?' é do par (métrica, semântica), não do canal. As
    # duas funções abaixo tratam o MESMO curinga de formas opostas, de propósito.
    print("O mesmo '?' nas duas métricas ('DAVID ? OLIVEIRA'):")
    print("  Jaro-Winkler (deleta) :", round(sim_jaro_winkler("DAVID ? OLIVEIRA", verdade), 3))
    print("  partial      (literal):", round(sim_partial("DAVID ? OLIVEIRA", verdade), 3))
    assert "?" not in " ".join("DAVID ? OLIVEIRA".replace(CURINGA, "").split()), \
        "sim_jaro_winkler tem que remover o curinga"

    print("Mesmo estado de conhecimento, dois canais:")
    print("  matricula '??????' vs '912314':", round(sim_levenshtein("??????", "912314"), 3))
    print("  nome '????? ????' vs 'MARIA SILVA':", round(sim_jaro_winkler("????? ????", "MARIA SILVA"), 3))

    print("Sanidade:")
    print("  '912314' vs si mesma:", round(sim_levenshtein("912314", "912314"), 3))
    print("  '9?2314' vs '912314':", round(sim_levenshtein("9?2314", "912314"), 3))

    print()
    print(f"--- 5: matcher (ponto vigente: {METRICA_NOME}, "
          f"alpha={ALPHA} theta={THETA} delta={DELTA}) ---")
    import time
    from base_alunos import carregar_base
    from normalizacao import normalizar

    # base VIGENTE (D07). A filter_hc_student foi aposentada como contradomínio.
    base = carregar_base("dados/Lista de Alunos Campus Quixadá.xlsx")
    for a in base:
        a["nome"] = normalizar(a["nome"])
    print(f"base: {len(base)} pessoas")

    alvo = base[0]
    # um sósia a um dígito, se existir: é o caso de A15, e o que delta caça
    mats = {a["matricula"] for a in base}
    sosia = next((m for m in mats
                  if m != alvo["matricula"] and len(m) == len(alvo["matricula"])
                  and sum(x != y for x, y in zip(m, alvo["matricula"])) == 1), None)

    t0 = time.perf_counter()
    r = identificar(alvo["matricula"], alvo["nome"], base)
    dt = time.perf_counter() - t0
    print("Leitura perfeita :", r["aluno"]["nome"], "|",
          round(r["score"], 3), round(r["margem"], 3), r["decisao"])
    print(f"Tempo por identificacao: {dt * 1000:.1f} ms")
    assert r["aluno"]["matricula"] == alvo["matricula"], "leitura perfeita tem que casar"
    assert r["decisao"] == "aceito", "leitura perfeita tem que ser aceita"

    # um curinga na matrícula, nome legível: o nome tem que sustentar sozinho
    truncada = alvo["matricula"][:-1] + "?"
    r = identificar(truncada, alvo["nome"], base)
    print(f"{truncada} + nome ok :", r["aluno"]["nome"], "|",
          round(r["score"], 3), round(r["margem"], 3), r["decisao"])

    # o mesmo curinga SEM o nome: tem que cair na quarentena, não escolher no par-ou-ímpar
    r = identificar(truncada, "?" * len(alvo["nome"]), base)
    print(f"{truncada} + nome ? :", round(r["score"], 3),
          round(r["margem"], 3), r["decisao"])
    if sosia:
        print(f"  (existe sósia a 1 dígito na base: {sosia})")

    r = identificar("?" * 6, "?" * 12, base)
    print("Tudo ilegivel    :", round(r["score"], 3),
          round(r["margem"], 3), r["decisao"])
    assert r["decisao"] == "quarentena", "leitura ilegível NUNCA pode ser aceita"