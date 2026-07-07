import json
import matplotlib.pyplot as plt
from experimento import resumir

with open("resultados_exp1.json") as f:
    blocos = json.load(f)

curvas = {}
for b in sorted(blocos, key=lambda b: (b["modo"], b["cer"])):
    curvas.setdefault(b["modo"], []).append((b["cer"], resumir(b["registros"])))

fig, (ax_p, ax_c) = plt.subplots(1, 2, figsize=(6.3, 2.8))

estilo = {"chuta":  dict(marker="o", color="tab:red",  label="OCR que chuta"),
          "abstem": dict(marker="s", color="tab:blue", label="OCR que se abstém")}

for modo, pontos in curvas.items():
    cers = [c for c, _ in pontos]
    ax_p.plot(cers, [m["precisao"] for _, m in pontos], **estilo[modo])
    ax_c.plot(cers, [m["cobertura"] for _, m in pontos], **estilo[modo])

ax_p.axhline(0.99, ls="--", lw=0.8, color="gray")
ax_p.set_xlabel("Taxa de erro do OCR (CER)")
ax_p.set_ylabel("Precisão nos auto-aceitos")
ax_c.set_xlabel("Taxa de erro do OCR (CER)")
ax_c.set_ylabel("Cobertura (auto-aceitos)")
for ax in (ax_p, ax_c):
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3)
ax_p.legend(fontsize=8)
fig.tight_layout()
fig.savefig("exp1_chutar_vs_abster.pdf")
fig.savefig("exp1_chutar_vs_abster.png", dpi=200)
print("Figura salva (.pdf e .png)")

for modo, pontos in curvas.items():
    for cer, m in pontos:
        print(f"{modo:<8} CER {cer:.2f} | aceitos {m['n_aceitos']:>3} "
              f"| precisao {m['precisao']:.3f} | cobertura {m['cobertura']:.3f}")