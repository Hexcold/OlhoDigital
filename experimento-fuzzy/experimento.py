import random
import time
from base_alunos import carregar_base
from normalizacao import normalizar
from simulador import simular_registro
from fuzzy import identificar


def rodar_config(base, n_sims, p_sub, p_del, p_abst, p_omit, p_abrev, semente):
    rng = random.Random(semente)
    registros = []
    for _ in range(n_sims):
        aluno = rng.choice(base)
        leitura = simular_registro(aluno, p_sub, p_del, p_abst,
                                   p_omit, p_abrev, rng)
        r = identificar(leitura["matricula_lida"], leitura["nome_lido"], base)
        registros.append({
            "acertou": r["aluno"]["matricula"] == aluno["matricula"],
            "decisao": r["decisao"],
            "score": r["score"],
            "margem": r["margem"],
        })
    return registros


def resumir(registros):
    n = len(registros)
    aceitos = [r for r in registros if r["decisao"] == "aceito"]
    acertos_aceitos = sum(1 for r in aceitos if r["acertou"])
    return {
        "top1": sum(r["acertou"] for r in registros) / n,
        "cobertura": len(aceitos) / n,
        "precisao": acertos_aceitos / len(aceitos) if aceitos else float("nan"),
        "n_aceitos": len(aceitos),
    }


if __name__ == "__main__":
    base = carregar_base("dados/filter_hc_student.xlsx")
    for a in base:
        a["nome"] = normalizar(a["nome"])

    N_SIMS = 300
    grade_cer = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    modos = ["chuta", "abstem"]

    todos = {}
    contador_semente = 0
    t0 = time.perf_counter()
    print(f"{'modo':<8} {'CER':>4} | {'top1':>6} {'cobert':>6} {'precis':>6}")
    for modo in modos:
        for cer in grade_cer:
            p_sub = cer if modo == "chuta" else 0.0
            p_abst = cer if modo == "abstem" else 0.0
            registros = rodar_config(base, N_SIMS, p_sub, 0.0, p_abst,
                                     0.0, 0.0, semente=1000 + contador_semente)
            contador_semente += 1
            todos[(modo, cer)] = registros
            m = resumir(registros)
            print(f"{modo:<8} {cer:>4.2f} | "
                  f"{m['top1']:>6.3f} {m['cobertura']:>6.3f} {m['precisao']:>6.3f}")
    print(f"\nTempo total: {time.perf_counter() - t0:.1f} s")
    
    import json
    salvar = [{"modo": modo, "cer": cer, "registros": regs}
              for (modo, cer), regs in todos.items()]
    with open("resultados_exp1.json", "w") as f:
        json.dump(salvar, f)
    print("Resultados salvos em resultados_exp1.json")