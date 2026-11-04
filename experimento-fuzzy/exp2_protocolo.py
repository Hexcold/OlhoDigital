"""Experimento 02 — qual protocolo de preenchimento do nome, e quantas células?

A folha impressa hoje tem **18 células de nome** (medido na grade das folhas reais,
ver A07 no vault) e o cabeçalho pede "NOME E SOBRENOME". Este experimento pergunta
se essa é a melhor escolha.

## O papel do nome

Pela Eq. 4.1 do TCC o nome não é plano B: ele é co-signatário permanente,
    S = α·sim_matrícula + (1−α)·sim_nome,  α = 0,7
A matrícula pesa mais por ser identificador único e determinístico, mas o nome é
olhado **sempre** — e é ele que impede aceitar a pessoa errada.

O modo de falha que o nome existe para vetar é o **impostor**: o reconhecedor lê
`968334` como `968344`, e essa outra matrícula pertence a uma pessoa real. Aí
sim_matrícula do impostor vale 1,0 e só o nome pode derrubá-lo. É o "impostor
confiante" de A01 — θ o caça, e o nome é a segunda linha de defesa.

Por isso a grade de ruído tem dois braços distintos na matrícula:
  - **abstenção** (`?`): produz ambiguidade, que é problema do δ;
  - **substituição**: produz impostor, que é problema do **nome**.

O experimento instrumenta `impostor_criado` por registro — a leitura caiu sobre
outra matrícula que existe na base — para poder responder à pergunta que decide
o protocolo: *quando o OCR fabricou outra pessoa real, quantas vezes o nome vetou?*

## Ordem de aplicação

    formar/encurtar (humano) -> truncar em N células (papel) -> ruído do OCR (máquina)

O truncamento é determinístico: imposição física do
número de células, não sorteio.

## Duas tensões a resolver

1. **Caber x discriminar.** "primeiro + último" cabe em 18 células para 98,6% da
   base ativa, mas deixa 216 pessoas (16,7%) com nome idêntico a outra.
   "primeiro + iniciais + último" deixa só 8 ambíguas, mas só 69,2% cabe em 18.
   Como Jaro-Winkler tem viés de prefixo, truncar sai barato e homônimo sai caro
   — a hipótese é que o protocolo que NÃO cabe vença mesmo assim.

2. **Base crua x pré-reduzida.** Se o protocolo é orientado, a base pode ser
   levada ao MESMO espaço de representação (D05) — reduzida e truncada igual.
   'DAVID S OLIVEIRA' contra a base crua dá JW 0,918; contra a base reduzida,
   1,000. É de graça e nunca foi medido.

Base: **ativa** (apenas_ativos=True). É o contradomínio real de uma lista de
presença, tem zero homônimos por nome completo e é 3,3x menor (A04).

Uso:
    python exp2_protocolo.py                 # grade completa
    python exp2_protocolo.py --n_sims 50     # ensaio rápido
"""

import argparse
import json
import random
import time

from base_alunos import carregar_base
from normalizacao import normalizar
from simulador import ruido_ocr, encurtar_nome, ALFABETO_NOME, ALFABETO_MATRICULA
from fuzzy import identificar

ARQUIVO_SAIDA = "resultados_exp2.json"


# --- protocolos: a forma que a folha instrui a pessoa a escrever -------------

def forma_completo(palavras: list[str]) -> str:
    return " ".join(palavras)


def forma_iniciais(palavras: list[str]) -> str:
    """primeiro + iniciais do meio + último: 'DAVID S OLIVEIRA'."""
    if len(palavras) == 1:
        return palavras[0]
    return " ".join([palavras[0]] + [p[0] for p in palavras[1:-1]] + [palavras[-1]])


def forma_prim_ult(palavras: list[str]) -> str:
    """'nome e sobrenome' — o que a folha atual pede."""
    if len(palavras) == 1:
        return palavras[0]
    return f"{palavras[0]} {palavras[-1]}"


def forma_dois(palavras: list[str]) -> str:
    return " ".join(palavras[:2])


PROTOCOLOS = {
    "completo": forma_completo,
    "iniciais": forma_iniciais,
    "prim_ult": forma_prim_ult,
    "dois": forma_dois,
}

REGIME_LIVRE = "livre"  # a pessoa encurta como quiser (D04 original)


def truncar(texto: str, n_celulas: int) -> str:
    """O que não cabe nas células não é escrito. Corte à direita, determinístico.

    `.strip()` porque cortar em cima de um espaço deixaria um espaço solto no fim,
    e D05 exige espaços colapsados nos dois lados da comparação.
    """
    return texto[:n_celulas].strip()


def escrever_na_folha(nome_base: str, protocolo: str, n_celulas: int,
                      rng: random.Random, p_omit: float, p_abrev: float) -> str:
    """Humano + papel: o que efetivamente fica escrito na linha da folha."""
    if protocolo == REGIME_LIVRE:
        escrito = encurtar_nome(nome_base, p_omit, p_abrev, rng)
    else:
        escrito = PROTOCOLOS[protocolo](nome_base.split())
    return truncar(escrito, n_celulas)


def reduzir_base(base: list[dict], protocolo: str, n_celulas: int) -> list[dict]:
    """Leva a base ao mesmo espaço de representação da folha (D05).

    Só faz sentido quando o protocolo é orientado: se a pessoa escreve
    'DAVID S OLIVEIRA', comparar contra 'DAVID SENA OLIVEIRA' desconta 8 pontos de
    Jaro-Winkler por uma diferença que o protocolo já explicou.
    Feito UMA vez por condição, fora do laço de identificação.
    """
    if protocolo == REGIME_LIVRE:
        return base
    forma = PROTOCOLOS[protocolo]
    return [{**a, "nome": truncar(forma(a["nome"].split()), n_celulas)} for a in base]


# --- runner -----------------------------------------------------------------

def vizinhas_reais(matriculas: set[str]) -> dict[str, list[str]]:
    """Para cada matrícula, as outras matrículas REAIS a uma substituição de distância.

    É o mapa do risco de impostor: 64,8% da base ativa tem pelo menos uma vizinha,
    ou seja, um único dígito errado converte a pessoa em outra pessoa que existe.
    Calculado uma vez, fora dos laços.
    """
    mapa: dict[str, list[str]] = {}
    for m in matriculas:
        viz = [m[:i] + d + m[i + 1:]
               for i in range(len(m)) for d in "0123456789"
               if d != m[i] and m[:i] + d + m[i + 1:] in matriculas]
        if viz:
            mapa[m] = viz
    return mapa


def rodar_config(base: list[dict], base_busca: list[dict], matriculas: set[str],
                 vizinhas: dict[str, list[str]], n_sims: int,
                 protocolo: str, n_celulas: int,
                 p_abst_nome: float, p_abst_mat: float, p_sub_mat: float,
                 forcar_impostor: bool, semente: int,
                 p_omit: float = 0.2, p_abrev: float = 0.2) -> list[dict]:
    """Uma condição. `base` é a verdade (nome completo); `base_busca` é contra o
    que o matcher compara — as duas diferem quando a base é pré-reduzida.

    `forcar_impostor` troca a matrícula lida por uma vizinha REAL, sorteada entre
    as que existem na base. Não é ruído: é o experimento controlado do modo de
    falha que o nome existe para vetar. Amostra só de quem tem vizinha (64,8% da
    base ativa), então a taxa aqui é **condicional** — dado que o impostor
    aconteceu, o nome derruba?
    """
    rng = random.Random(semente)
    candidatos = ([a for a in base if a["matricula"] in vizinhas]
                  if forcar_impostor else base)
    registros = []
    for _ in range(n_sims):
        aluno = candidatos[rng.randrange(len(candidatos))]

        escrito = escrever_na_folha(aluno["nome"], protocolo, n_celulas,
                                    rng, p_omit, p_abrev)
        nome_lido = ruido_ocr(escrito, 0.0, 0.0, p_abst_nome, ALFABETO_NOME, rng)
        nome_lido = " ".join(nome_lido.split())

        if forcar_impostor:
            opcoes = vizinhas[aluno["matricula"]]
            matricula_lida = opcoes[rng.randrange(len(opcoes))]
        else:
            matricula_lida = ruido_ocr(aluno["matricula"], p_sub_mat, 0.0,
                                       p_abst_mat, ALFABETO_MATRICULA, rng)

        # o impostor: a leitura caiu inteira sobre a matrícula de OUTRA pessoa real.
        # É o caso em que sim_matrícula vale 1,0 para quem não é — e só o nome veta.
        impostor = (matricula_lida != aluno["matricula"]
                    and matricula_lida in matriculas)

        r = identificar(matricula_lida, nome_lido, base_busca)
        registros.append({
            "acertou": r["aluno"]["matricula"] == aluno["matricula"],
            "decisao": r["decisao"],
            "score": r["score"],
            "margem": r["margem"],
            "impostor": impostor,
            "n_escrito": len(escrito),
        })
    return registros


def resumir(registros: list[dict]) -> dict:
    n = len(registros)
    aceitos = [r for r in registros if r["decisao"] == "aceito"]
    certos = sum(1 for r in aceitos if r["acertou"])
    imp = [r for r in registros if r["impostor"]]
    imp_aceitos_errados = sum(1 for r in imp
                              if r["decisao"] == "aceito" and not r["acertou"])
    return {
        "n": n,
        "top1": sum(r["acertou"] for r in registros) / n,
        "cobertura": len(aceitos) / n,
        "precisao": certos / len(aceitos) if aceitos else float("nan"),
        "n_aceitos": len(aceitos),
        "n_errados": len(aceitos) - certos,
        "n_impostores": len(imp),
        "impostores_passaram": imp_aceitos_errados,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Compara protocolos de preenchimento do nome.")
    p.add_argument("--n_sims", type=int, default=200, help="repetições por condição")
    p.add_argument("--base_completa", action="store_true",
                   help="usar os 4.321 vínculos em vez da base ativa (A04)")
    p.add_argument("--saida", default=ARQUIVO_SAIDA)
    args = p.parse_args()

    base = carregar_base("dados/filter_hc_student.xlsx",
                         apenas_ativos=not args.base_completa)
    for a in base:
        a["nome"] = normalizar(a["nome"])
    matriculas = {a["matricula"] for a in base}
    vizinhas = vizinhas_reais(matriculas)
    print(f"base: {len(base)} registros "
          f"({'completa' if args.base_completa else 'apenas ATIVO'})")
    print(f"risco de impostor: {len(vizinhas)}/{len(matriculas)} matrículas "
          f"({100 * len(vizinhas) / len(matriculas):.1f}%) têm vizinha real "
          f"a um dígito de distância")

    grade_protocolo = [REGIME_LIVRE, "completo", "iniciais", "prim_ult", "dois"]
    grade_celulas = [18, 28]
    # (rótulo, abst. no nome, abst. na matrícula, substituição na matrícula)
    # Os dois últimos braços são os que fabricam impostor — o alvo do nome.
    # (rótulo, abst. nome, abst. matrícula, subst. matrícula, forçar impostor)
    grade_ruido = [
        ("limpo",       0.00, 0.00, 0.00, False),
        ("abst.leve",   0.15, 0.15, 0.00, False),
        ("abst.pesada", 0.30, 0.30, 0.00, False),
        ("impostor",    0.15, 0.00, 0.00, True),
    ]

    todos = []
    semente = 2000
    t0 = time.perf_counter()
    print(f"\n{'protocolo':<10} {'N':>3} {'base':<9} {'ruido':<12} | "
          f"{'top1':>6} {'cobert':>6} {'precis':>7} {'aceitos':>8} {'errados':>8} "
          f"{'impost':>7} {'passaram':>9}")

    for protocolo in grade_protocolo:
        # base pré-reduzida não existe no regime livre: não há forma a impor
        variantes = [False] if protocolo == REGIME_LIVRE else [False, True]
        for n_celulas in grade_celulas:
            for reduzida in variantes:
                base_busca = (reduzir_base(base, protocolo, n_celulas)
                              if reduzida else base)
                for rotulo, ab_nome, ab_mat, sub_mat, forcar in grade_ruido:
                    regs = rodar_config(base, base_busca, matriculas, vizinhas,
                                        args.n_sims, protocolo, n_celulas,
                                        ab_nome, ab_mat, sub_mat, forcar, semente)
                    semente += 1
                    m = resumir(regs)
                    todos.append({
                        "protocolo": protocolo, "n_celulas": n_celulas,
                        "base_reduzida": reduzida, "ruido": rotulo,
                        "p_abst_nome": ab_nome, "p_abst_mat": ab_mat,
                        "p_sub_mat": sub_mat, "forcar_impostor": forcar,
                        "registros": regs,
                    })
                    nome_base = "reduzida" if reduzida else "crua"
                    print(f"{protocolo:<10} {n_celulas:>3} {nome_base:<9} {rotulo:<12} | "
                          f"{m['top1']:>6.3f} {m['cobertura']:>6.3f} "
                          f"{m['precisao']:>7.3f} {m['n_aceitos']:>8} "
                          f"{m['n_errados']:>8} {m['n_impostores']:>7} "
                          f"{m['impostores_passaram']:>9}")

    print(f"\ntempo total: {time.perf_counter() - t0:.1f} s")
    with open(args.saida, "w") as f:
        json.dump(todos, f)
    print(f"registros crus em {args.saida} — pontue uma vez, limiarize muitas")
