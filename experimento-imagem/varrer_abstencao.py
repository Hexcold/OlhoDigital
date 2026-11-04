"""O sinal de abstenção: a margem separa acerto de erro melhor que a confiança?

## A pergunta, e por que ela é velha

O `?` do D02 precisa de um critério, e o critério em uso é um corte absoluto na
softmax do vencedor (`--corte-letras`, padrão 0,95 na CNN). Isso é um sinal
UNÁRIO: responde "quão parecido com um E?".

A11 apontou que o que separa acerto de erro é RELACIONAL — "exclui as outras
classes?" —, e `Predicao.margem` (ocr.py) já calcula exatamente isso: a sobra do
vencedor sobre o segundo colocado, que é o teste de razão de Lowe no nível do
caractere, o mesmo raciocínio do δ do D03 um nível abaixo. Só que ninguém
consome esse campo: grep no pacote, e as demais ocorrências de "margem" são o δ
da identificação, que é outro nível.

Então a pergunta é medível hoje, sem imprimir nada e sem retreinar: nas mesmas
células reais, qual dos dois sinais deixa MENOS erro dentro da string quando se
abstém a mesma quantidade de vezes?

## Por que "à mesma taxa de abstenção", e não "ao mesmo corte"

Os dois sinais vivem em escalas diferentes — a softmax do vencedor se acumula
perto de 1,0, a margem se espalha. Comparar no mesmo valor de corte compararia
as escalas, não o poder de separação. Comparar à mesma taxa de abstenção fixa o
que o pipeline paga (quantos `?` entram na string) e mede o que ele recebe
(quantos erros saíram junto).

E é a comparação pareada certa: as MESMAS células, cada sinal escolhendo quais
reter. Discordância célula a célula dá McNemar exato, como em KNN x CNN.

## O que este módulo NÃO decide

Se a abstenção compensa. A13 mostrou a recomendação do TCC 1 se invertendo com
reconhecedor real, e quem decide isso é a identificação ponta a ponta, não o
caractere. Aqui se decide apenas QUAL sinal usar, caso se abstenha.

Uso:
    python varrer_abstencao.py
    python varrer_abstencao.py --coluna matricula
"""

from __future__ import annotations

import os
os.environ.setdefault("KERAS_BACKEND", "torch")

import argparse
import json

import numpy as np
from numpy.typing import NDArray

from afinar_cnn import mcnemar
from avaliar import carregar_celulas
from dados_treino import ALFABETO_MAIUSCULAS, ALFABETO_DIGITOS, normalizar_lote
from treinar_cnn import LADO

# Só a coluna de NOME tem CNN hoje (D08: um classificador por campo; os dígitos
# seguem no KNN). A pergunta deste módulo pressupõe softmax contínua nas duas
# pontas do ranking, e o KNN com k=3 só admite 4 níveis — que é a granularidade
# que A20 mostrou não ter resolução para abster. Medir a matrícula exige treinar
# a CNN dos dígitos antes; até lá, falhar alto é mais honesto que estimar.
MODELOS = {"nome": "modelo/cnn_emnist_maiusculas_28x28.keras"}
SAIDA = "dados/varredura_abstencao.json"

# Taxas de abstenção varridas. 0,0 é o "chuta" do A01/A13 e serve de linha de
# base; acima de ~0,4 a string fica mais curinga que letra e o matcher não tem
# o que fazer com ela.
TAXAS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)


def sinais(modelo, X: NDArray[np.uint8], y: NDArray[np.str_], alfabeto: str
           ) -> dict[str, NDArray]:
    """Prevê e devolve os dois sinais candidatos, pareados por célula.

    A célula passa pelo MESMO `normalizar_lote` do pipeline: é o casamento de
    domínio que A19 mostrou valer 22,9 pontos quando esquecido.
    """
    # O pré-processamento tem que ser IDÊNTICO ao do `avaliar.prever_cnn`:
    # `normalizar_lote` devolve uint8 em 0..255 e sem eixo de canal, e é o
    # `prever_cnn` que faz `(X/255)[..., None]`. Chamar `predict` direto na
    # saída do normalizador entrega a entrada 255x fora de escala — e a rede
    # não reclama, só devolve softmax achatada: 88/304 em vez de 258/304, com
    # a margem virando constante e a varredura inteira zerando.
    Z = (normalizar_lote(X, lado=LADO).astype(np.float32) / 255.0)[..., None]
    probs = modelo.predict(Z, verbose=0)
    ordenado = np.sort(probs, axis=1)
    letras = np.array(list(alfabeto))
    return {
        "predito": letras[probs.argmax(axis=1)],
        "certo": letras[probs.argmax(axis=1)] == y,
        # o sinal em uso hoje: probabilidade do vencedor
        "confianca": probs.max(axis=1),
        # o sinal que o ocr.py calcula e ninguém lê: sobra sobre o 2º colocado
        "margem": ordenado[:, -1] - ordenado[:, -2],
    }


def auc(sinal: NDArray[np.float64], certo: NDArray[np.bool_]) -> float:
    """P(sinal de um acerto > sinal de um erro), empates contando meio.

    É a estatística de Mann-Whitney normalizada, e é a pergunta do A11 escrita
    como número: o sinal ordena acerto acima de erro? 0,5 é moeda.
    """
    bons, ruins = sinal[certo], sinal[~certo]
    if len(bons) == 0 or len(ruins) == 0:
        return float("nan")
    comp = bons[:, None] - ruins[None, :]
    return float((np.sum(comp > 0) + 0.5 * np.sum(comp == 0)) / comp.size)


def retidos_na_taxa(sinal: NDArray[np.float64], taxa: float) -> NDArray[np.bool_]:
    """Máscara das células que SOBREVIVEM abstendo `taxa` das mais fracas.

    O corte sai do quantil do próprio sinal, não de um valor absoluto: é o que
    torna os dois sinais comparáveis apesar de viverem em escalas diferentes.
    """
    if taxa <= 0:
        return np.ones(len(sinal), dtype=bool)
    return sinal > np.quantile(sinal, taxa)


def main() -> None:
    p = argparse.ArgumentParser(description="Qual sinal governa a abstenção do '?'.")
    p.add_argument("--coluna", default="nome", choices=("nome", "matricula"))
    p.add_argument("--json", default=SAIDA)
    args = p.parse_args()

    if args.coluna not in MODELOS:
        raise SystemExit(
            f"não há CNN para a coluna {args.coluna!r} — só {sorted(MODELOS)}. "
            "Os dígitos seguem no KNN (D08), cuja confiança com k=3 tem 4 "
            "níveis e não sustenta esta comparação (A20).")

    alfabeto = (ALFABETO_MAIUSCULAS if args.coluna == "nome"
                else ALFABETO_DIGITOS)
    X, y = carregar_celulas(args.coluna)

    from keras import saving
    modelo = saving.load_model(MODELOS[args.coluna])
    s = sinais(modelo, X, y, alfabeto)
    certo = s["certo"]
    n, acertos = len(certo), int(certo.sum())
    print(f"coluna={args.coluna} | {n} células | CNN base acerta "
          f"{acertos}/{n} = {acertos / n:.4f}\n")

    print("=== o sinal ordena acerto acima de erro? (AUC, 0,5 = moeda) ===")
    for nome_s in ("confianca", "margem"):
        print(f"  {nome_s:<10} AUC = {auc(s[nome_s], certo):.4f}")

    print(f"\n=== à MESMA taxa de abstenção, quantos erros sobram na string? ===")
    print(f"{'abstém':>7} {'':3} {'confiança':>22} {'':3} {'margem':>22}")
    print(f"{'':7} {'':3} {'retidas  erros  acur':>22} {'':3} "
          f"{'retidas  erros  acur':>22}")
    registros = []
    mascaras: dict[float, dict[str, NDArray[np.bool_]]] = {}
    for taxa in TAXAS:
        linha = {"taxa_abstencao": taxa}
        mascaras[taxa] = {}
        for nome_s in ("confianca", "margem"):
            ret = retidos_na_taxa(s[nome_s], taxa)
            mascaras[taxa][nome_s] = ret
            erros = int((~certo & ret).sum())
            linha[nome_s] = {"retidas": int(ret.sum()), "erros": erros,
                             "acuracia": float(certo[ret].mean()) if ret.any() else 0.0}
        registros.append(linha)
        c, m = linha["confianca"], linha["margem"]
        print(f"{taxa:>7.2f} {'':3} {c['retidas']:>7} {c['erros']:>6} "
              f"{c['acuracia']:>6.4f} {'':3} {m['retidas']:>7} {m['erros']:>6} "
              f"{m['acuracia']:>6.4f}")

    # O teste pareado: a unidade é a CÉLULA, e o evento ruim é "foi retida E
    # está errada" — o erro que sobrevive à abstenção e entra na string. Cada
    # sinal decide quais reter, sobre as mesmas células, então a discordância é
    # exatamente o que McNemar consome.
    print(f"\n=== erro que SOBREVIVE à abstenção, pareado por célula ===")
    print(f"{'abstém':>7} {'só conf':>9} {'só marg':>9} {'p':>9}")
    for taxa in TAXAS:
        if taxa == 0:
            continue
        ruim_c = ~certo & mascaras[taxa]["confianca"]
        ruim_m = ~certo & mascaras[taxa]["margem"]
        # mcnemar espera vetores de ACERTO; aqui "acertar" é não deixar passar
        so_c, so_m, pv = mcnemar(~ruim_c, ~ruim_m)
        for r in registros:
            if r["taxa_abstencao"] == taxa:
                r["mcnemar"] = {"so_confianca": so_c, "so_margem": so_m, "p": pv}
        print(f"{taxa:>7.2f} {so_c:>9} {so_m:>9} {pv:>9.4f}")

    saida = {"coluna": args.coluna, "n": n, "acertos_sem_abster": acertos,
             "auc": {k: auc(s[k], certo) for k in ("confianca", "margem")},
             "pontos": registros}
    with open(args.json, "w") as f:
        json.dump(saida, f, indent=2)
    print(f"\nregistros crus em {args.json}")


if __name__ == "__main__":
    main()
