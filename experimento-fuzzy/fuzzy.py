from rapidfuzz.distance import JaroWinkler


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
    """Similaridade [0,1] via Jaro-Winkler (rapidfuzz).
    Abstenções são removidas antes: no canal sem wildcard,
    abster tem semântica de deleção."""
    limpo = " ".join(s1.replace("?", "").split())
    return JaroWinkler.similarity(limpo, s2)

def identificar(matricula_lida: str, nome_lido: str, base: list[dict],
                alpha: float = 0.7, theta: float = 0.85,
                delta: float = 0.05) -> dict:
    """Compara a leitura contra todos os candidatos da base.
    Score composto: alpha*sim_matricula + (1-alpha)*sim_nome.
    Aceito sse melhor score >= theta E margem sobre o vice >= delta."""
    melhor = None
    melhor_score = -1.0
    vice_score = -1.0

    for aluno in base:
        s_mat = sim_levenshtein(matricula_lida, aluno["matricula"])
        s_nome = sim_jaro_winkler(nome_lido, aluno["nome"])
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
    print("--- 4b: similaridades ---")
    verdade = "DAVID SENA OLIVEIRA"

    print("Assinatura preguicosa 'DAVID O':")
    print("  Levenshtein :", round(sim_levenshtein("DAVID O", verdade), 3))
    print("  Jaro-Winkler:", round(sim_jaro_winkler("DAVID O", verdade), 3))
    print("Abreviacao 'DAVID S OLIVEIRA':")
    print("  Jaro-Winkler:", round(sim_jaro_winkler("DAVID S OLIVEIRA", verdade), 3))

    print("Mesmo estado de conhecimento, dois canais:")
    print("  matricula '??????' vs '912314':", round(sim_levenshtein("??????", "912314"), 3))
    print("  nome '????? ????' vs 'MARIA SILVA':", round(sim_jaro_winkler("????? ????", "MARIA SILVA"), 3))

    print("Sanidade:")
    print("  '912314' vs si mesma:", round(sim_levenshtein("912314", "912314"), 3))
    print("  '9?2314' vs '912314':", round(sim_levenshtein("9?2314", "912314"), 3))
    
    print()
    print("--- 5: matcher ---")
    import time
    from base_alunos import carregar_base
    from normalizacao import normalizar

    base = carregar_base("dados/filter_hc_student.xlsx")
    for a in base:
        a["nome"] = normalizar(a["nome"])

    carlos = next(a for a in base if a["matricula"] == "994671")

    t0 = time.perf_counter()
    r = identificar(carlos["matricula"], carlos["nome"], base)
    dt = time.perf_counter() - t0
    print("Leitura perfeita :", r["aluno"]["nome"], "|",
          round(r["score"], 3), round(r["margem"], 3), r["decisao"])
    print(f"Tempo por identificacao: {dt * 1000:.1f} ms")

    r = identificar("99467?", carlos["nome"], base)
    print("99467? + nome ok :", r["aluno"]["nome"], "|",
          round(r["score"], 3), round(r["margem"], 3), r["decisao"])

    r = identificar("99467?", "????? ?????", base)
    print("99467? + nome ?  :", round(r["score"], 3),
          round(r["margem"], 3), r["decisao"])

    r = identificar("??????", "????? ?????", base)
    print("Tudo ilegivel    :", round(r["score"], 3),
          round(r["margem"], 3), r["decisao"])