"""Liga a chave lida na folha a um evento cadastrado (D15, D17).

Errar aqui é o pior erro desta parte do sistema: as pessoas continuam certas,
só que na pasta do evento errado, e nem precisão nem cobertura enxergam (D14).
Mandar para a bandeja custa um clique. Por isso o gate é duro, e aceita só
quando as três condições valem:

  1. distância ZERO, com o `?` valendo como curinga. Em 4.848 células só o V foi
     lido errado (A49); o resto abstém. Sobrar qualquer diferença é sinal de
     verdade, e no caso do V a folha cai na bandeja em vez de arriscar;
  2. UMA candidata só. Empate é a leitura dizendo que não sabe;
  3. ao menos MINIMO_LETRAS legíveis. Com poucas letras, a unicidade vem de
     quais chaves estão cadastradas naquele dia, não do que foi lido.

Não reusa θ e δ do participante: lá o espaço é a matrícula, densa e dada; aqui
as chaves são nossas e dá para escolhê-las esparsas.

Função pura sobre texto: roda sem imagem, sem modelo e sem banco.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "experimento-fuzzy"))
from fuzzy import dist_levenshtein  # noqa: E402

CURINGA = "?"

# Dedução, não calibração: nas 36 chaves medidas nenhum corte de 1 a 4 produziu
# vínculo errado (D17). O custo de 3 é uma folha na bandeja em 36, `?A?A?`.
MINIMO_LETRAS = 3

MOTIVOS = {
    "unica": "a leitura combina com uma chave só",
    "sem_banda": "esta geometria de folha não tem chave",
    "em_branco": "ninguém preencheu a chave",
    "sem_eventos": "não há evento aberto para comparar",
    "poucas_letras": "poucas letras legíveis na chave",
    "empate": "a leitura combina com mais de um evento",
    "sem_candidata": "a leitura não combina com nenhum evento aberto",
}


@dataclass(frozen=True)
class Casamento:
    chave: str | None                  # None manda a folha para a bandeja
    motivo: str                        # uma das entradas de MOTIVOS
    candidatas: list[tuple[str, int]]  # (chave, distância), a mais próxima antes

    @property
    def explicacao(self) -> str:
        return MOTIVOS[self.motivo]


def casar_chave(lida: str | None, chaves: list[str]) -> Casamento:
    """Decide o vínculo. `lida` é `Chave.texto`; None é folha sem banda."""
    candidatas = sorted(((c, dist_levenshtein(lida or "", c)) for c in chaves),
                        key=lambda par: (par[1], par[0]))
    if lida is None:
        return Casamento(None, "sem_banda", candidatas)
    if not lida:
        return Casamento(None, "em_branco", candidatas)
    if not chaves:
        return Casamento(None, "sem_eventos", candidatas)
    if len(lida) - lida.count(CURINGA) < MINIMO_LETRAS:
        return Casamento(None, "poucas_letras", candidatas)
    if candidatas[0][1] != 0:
        return Casamento(None, "sem_candidata", candidatas)
    if len(candidatas) > 1 and candidatas[1][1] == 0:
        return Casamento(None, "empate", candidatas)
    return Casamento(candidatas[0][0], "unica", candidatas)


if __name__ == "__main__":
    chaves = ["BANANA", "GOIABA", "CACAU", "PITANGA", "PITAYA", "TAMARINDO"]
    casos = [
        ("BANANA", "BANANA", "unica"),         # leitura limpa
        ("BA?ANA", "BANANA", "unica"),         # manuscrita, m7_01
        ("TAMARIND?", "TAMARINDO", "unica"),
        ("PITA?A", "PITAYA", "unica"),         # a menor margem medida em A48
        ("?A?A?", None, "poucas_letras"),      # CACAU, só 2 letras legíveis
        ("JABUTICABA", None, "sem_candidata"), # chave fora do cadastro
        ("GOIABB", None, "sem_candidata"),     # uma letra errada não passa
        ("", None, "em_branco"),
        (None, None, "sem_banda"),
    ]
    for lida, esperada, motivo in casos:
        r = casar_chave(lida, chaves)
        assert (r.chave, r.motivo) == (esperada, motivo), (lida, r)
        print(f"  {str(lida):12s} -> {str(r.chave):10s} {r.explicacao}")

    r = casar_chave("BANANA", [])
    assert (r.chave, r.motivo) == (None, "sem_eventos")

    # O curinga cai bem na letra que separa as duas: é o que o alerta de
    # distância da planilha existe para evitar.
    r = casar_chave("BA?ANA", ["BANANA", "BACANA"])
    assert (r.chave, r.motivo) == (None, "empate"), r

    # A bandeja precisa da sugestão certa em primeiro, mesmo quando recusa.
    r = casar_chave("?A?A?", chaves)
    assert r.chave is None and r.candidatas[0] == ("CACAU", 0), r.candidatas
    r = casar_chave("GOIABB", chaves)
    assert r.candidatas[0] == ("GOIABA", 1), r.candidatas
    print("  sugestões da bandeja: ok")
    print("self-check concluído")
