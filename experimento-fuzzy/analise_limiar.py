import json


def reaplicar_gate(registros, theta, delta):
    """Reaplica a política de aceitação sobre scores já computados.
    Nenhuma identificação nova acontece aqui: só comparações."""
    aceitos = [r for r in registros if r["score"] >= theta and r["margem"] >= delta]
    certos = sum(1 for r in aceitos if r["acertou"])
    return {
        "n_aceitos": len(aceitos),
        "errados": len(aceitos) - certos,
        "precisao": certos / len(aceitos) if aceitos else float("nan"),
        "cobertura": len(aceitos) / len(registros),
    }


with open("resultados_exp1.json") as f:
    blocos = json.load(f)

pool = {"chuta": [], "abstem": []}
for b in blocos:
    if b["cer"] > 0:
        pool[b["modo"]].extend(b["registros"])

print(f"Pool chuta: {len(pool['chuta'])} | pool abstem: {len(pool['abstem'])}")

print("\n--- Varredura de theta (delta fixo = 0.05) ---")
print(f"{'modo':<8} {'theta':>5} | {'aceitos':>7} {'errados':>7} {'precis':>7} {'cobert':>7}")
for modo in ["chuta", "abstem"]:
    for theta in [0.85, 0.88, 0.90, 0.92, 0.94, 0.96]:
        m = reaplicar_gate(pool[modo], theta, 0.05)
        print(f"{modo:<8} {theta:>5.2f} | {m['n_aceitos']:>7} {m['errados']:>7} "
              f"{m['precisao']:>7.3f} {m['cobertura']:>7.3f}")

print("\n--- Varredura de delta (theta fixo = 0.85) ---")
print(f"{'modo':<8} {'delta':>5} | {'aceitos':>7} {'errados':>7} {'precis':>7} {'cobert':>7}")
for modo in ["chuta", "abstem"]:
    for delta in [0.00, 0.02, 0.05, 0.10, 0.15, 0.20]:
        m = reaplicar_gate(pool[modo], 0.85, delta)
        print(f"{modo:<8} {delta:>5.2f} | {m['n_aceitos']:>7} {m['errados']:>7} "
              f"{m['precisao']:>7.3f} {m['cobertura']:>7.3f}")