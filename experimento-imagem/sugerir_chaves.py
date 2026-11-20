"""Sugerir chaves livres e seguras para um grupo (D35, fase 4).

Em 04/11 o autor decidiu não fazer o gerador ("as chaves vêm prontas"). O D33 passou a liberar as
chaves de eventos encerrados, e o D35 revisou a decisão: o sistema sugere, quem cadastra escolhe.

As regras são as mesmas réguas que a impressão já usa (`lote_folhas`):
- **sem V** (A49): é a única letra lida ERRADA, sempre como W;
- **nunca usada**, nem por evento aberto nem por encerrado: chave de encerrado reaproveitada faz
  a folha pedir confirmação (D33), e a sugestão não deve criar esse trabalho;
- **a DISTANCIA_MINIMA (4) de toda chave aberta** (A45): até 3 '?' numa chave impressa, e cada
  '?' absorve uma edição; duas chaves mais próximas que isso podem empatar no casamento (D17).
  Vale contra as abertas de TODOS os grupos, porque o envio por "todas as folhas" casa com todas;
- **e a 4 das outras sugestões da mesma rodada**, para que usar várias juntas também seja seguro.

Entre as que passam, vêm primeiro as com mais **letras seguras** (A49: B D E F G H K L M P Q R S
T W Z nunca saem erradas) e, no empate, as mais distantes da chave aberta mais próxima.

Função pura sobre texto: não lê banco nem imagem. A lista de palavras está em `palavras_chave.txt`.
"""
from __future__ import annotations

from pathlib import Path

from rapidfuzz.distance import Levenshtein

from lote_folhas import DISTANCIA_MINIMA, LETRA_LIDA_ERRADA, LETRAS_SEGURAS

ARQUIVO_PALAVRAS = Path(__file__).resolve().with_name("palavras_chave.txt")


def carregar_palavras(caminho: Path = ARQUIVO_PALAVRAS) -> list[str]:
    return [linha.strip().upper() for linha in caminho.read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.startswith("#")]


def fracao_segura(chave: str) -> float:
    return sum(c in LETRAS_SEGURAS for c in chave) / len(chave)


def sugerir(usadas: set[str], abertas: set[str], n: int = 8,
            palavras: list[str] | None = None) -> list[dict]:
    """Até `n` chaves. Cada uma: chave, fração de letras seguras, a chave aberta mais próxima
    e a distância até ela (None sem chave aberta)."""
    palavras = carregar_palavras() if palavras is None else palavras
    usadas = {u.upper() for u in usadas}
    abertas = {a.upper() for a in abertas}
    candidatas = []
    for p in dict.fromkeys(palavras):                       # sem repetir, na ordem do arquivo
        if p in usadas or LETRA_LIDA_ERRADA in p or not p.isalpha() or len(p) > 16:
            continue
        perto = min(((Levenshtein.distance(p, a), a) for a in abertas), default=(None, None))
        if perto[0] is not None and perto[0] < DISTANCIA_MINIMA:
            continue
        candidatas.append((p, perto))
    candidatas.sort(key=lambda c: (-fracao_segura(c[0]),
                                   -(c[1][0] if c[1][0] is not None else 99), c[0]))
    escolhidas: list[dict] = []
    for p, (dist, proxima) in candidatas:
        if len(escolhidas) >= n:
            break
        if any(Levenshtein.distance(p, e["chave"]) < DISTANCIA_MINIMA for e in escolhidas):
            continue
        escolhidas.append({"chave": p, "letras_seguras": round(fracao_segura(p), 2),
                           "mais_proxima": proxima, "distancia": dist})
    return escolhidas


if __name__ == "__main__":
    palavras = carregar_palavras()
    print(f"{len(palavras)} palavras na lista")
    assert not any(LETRA_LIDA_ERRADA in p for p in palavras), "palavra com V na lista"
    s = sugerir(usadas={"BALEIA"}, abertas={"BALEIA", "PORTAL", "CIDADE"}, n=6)
    for x in s:
        print(" ", x)
    assert "BALEIA" not in {x["chave"] for x in s}
    assert all(x["distancia"] >= DISTANCIA_MINIMA for x in s)
    from itertools import combinations
    assert all(Levenshtein.distance(a["chave"], b["chave"]) >= DISTANCIA_MINIMA
               for a, b in combinations(s, 2))
    assert [x["letras_seguras"] for x in s] == sorted((x["letras_seguras"] for x in s),
                                                       reverse=True)
    print("ok")
