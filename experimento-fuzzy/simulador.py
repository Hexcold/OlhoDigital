import random

ALFABETO_NOME = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALFABETO_MATRICULA = "0123456789"


def ruido_ocr(texto: str, p_sub: float, p_del: float, p_abst: float,
              alfabeto: str, rng: random.Random) -> str:
    """Simula erros de leitura do OCR, caractere a caractere.
    p_sub: prob. de trocar por outro caractere do alfabeto
    p_del: prob. de o caractere sumir da leitura
    p_abst: prob. de o OCR marcar como ilegível ('?')
    Espaços nunca são corrompidos (estrutura de palavras é o passo 3b)."""
    assert p_sub + p_del + p_abst <= 1.0, "probabilidades somam mais que 1"

    saida = []
    for ch in texto:
        if ch == " ":
            saida.append(ch)
            continue

        r = rng.random()
        if r < p_sub:
            saida.append(rng.choice(alfabeto.replace(ch, "")))
        elif r < p_sub + p_del:
            pass
        elif r < p_sub + p_del + p_abst:
            saida.append("?")
        else:
            saida.append(ch)

    return "".join(saida)

def encurtar_nome(nome: str, p_omit: float, p_abrev: float,
                  rng: random.Random) -> str:
    """Simula o encurtamento humano do nome ao escrever na folha.
    O primeiro nome é sempre preservado. Cada nome seguinte pode,
    de forma exclusiva: ser omitido (p_omit), ser abreviado para
    a inicial (p_abrev), ou ser mantido."""
    assert p_omit + p_abrev <= 1.0, "probabilidades somam mais que 1"

    palavras = nome.split()
    saida = [palavras[0]]

    for palavra in palavras[1:]:
        r = rng.random()
        if r < p_omit:
            continue
        elif r < p_omit + p_abrev:
            saida.append(palavra[0])
        else:
            saida.append(palavra)

    return " ".join(saida)

def simular_registro(aluno: dict, p_sub: float, p_del: float, p_abst: float,
                     p_omit: float, p_abrev: float, rng: random.Random) -> dict:
    """Simula o registro completo de um aluno: a pessoa encurta o nome
    ao escrever (erros humanos), depois o OCR lê ambos os campos com
    ruído (erros de máquina). A matrícula não sofre encurtamento."""
    nome_escrito = encurtar_nome(aluno["nome"], p_omit, p_abrev, rng)
    nome_lido = ruido_ocr(nome_escrito, p_sub, p_del, p_abst, ALFABETO_NOME, rng)
    matricula_lida = ruido_ocr(aluno["matricula"], p_sub, p_del, p_abst,
                               ALFABETO_MATRICULA, rng)

    nome_lido = " ".join(nome_lido.split())

    return {"matricula_lida": matricula_lida, "nome_lido": nome_lido}

if __name__ == "__main__":
    rng = random.Random(42)

    nome = "CICERA DE TAL FICTICIA"
    print("Original :", nome)
    for _ in range(5):
        print("Ruidoso  :", ruido_ocr(nome, 0.10, 0.05, 0.05, ALFABETO_NOME, rng))

    print()
    matricula = "912314"
    print("Original :", matricula)
    for _ in range(5):
        print("Ruidoso  :", ruido_ocr(matricula, 0.10, 0.05, 0.05, ALFABETO_MATRICULA, rng))

    print()
    r1 = random.Random(123)
    r2 = random.Random(123)
    a = ruido_ocr(nome, 0.2, 0.1, 0.1, ALFABETO_NOME, r1)
    b = ruido_ocr(nome, 0.2, 0.1, 0.1, ALFABETO_NOME, r2)
    print("Mesma semente -> mesma saída?", a == b)
    print("  a:", a)
    print("  b:", b)

    print()
    rng_stat = random.Random(7)
    n = 100_000
    texto_teste = "A" * n
    saida = ruido_ocr(texto_teste, 0.10, 0.0, 0.0, ALFABETO_NOME, rng_stat)
    trocados = sum(1 for orig, lido in zip(texto_teste, saida) if orig != lido)
    print(f"Taxa pedida: 0.1000 | taxa empírica: {trocados / n:.4f}")
    
    print()
    rng3 = random.Random(11)
    nome2 = "DAVID SENA OLIVEIRA"
    print("Original :", nome2)
    for _ in range(6):
        print("Escrito  :", encurtar_nome(nome2, 0.20, 0.20, rng3))

    print()
    rng_stat2 = random.Random(99)
    nome_teste = "PRIMEIRO " + " ".join(["NOME"] * 10)
    n_trials = 10_000
    total = 10 * n_trials
    omitidos = abreviados = 0
    for _ in range(n_trials):
        resto = encurtar_nome(nome_teste, 0.20, 0.30, rng_stat2).split()[1:]
        abreviados += sum(1 for p in resto if p == "N")
        omitidos += 10 - len(resto)
    print(f"Omissão  pedida: 0.2000 | empírica: {omitidos / total:.4f}")
    print(f"Abrevia. pedida: 0.3000 | empírica: {abreviados / total:.4f}")
    
    print()
    print("--- 3c: composicao ---")
    rng4 = random.Random(5)
    aluno_teste = {"matricula": "912314", "nome": "DAVID SENA OLIVEIRA"}

    limpo = simular_registro(aluno_teste, 0, 0, 0, 0, 0, rng4)
    print("Identidade ok?",
          limpo["nome_lido"] == aluno_teste["nome"]
          and limpo["matricula_lida"] == aluno_teste["matricula"])

    so_primeiro = simular_registro(aluno_teste, 0, 0, 0, 1.0, 0, rng4)
    print("p_omit=1 ->", so_primeiro["nome_lido"])

    tudo_abst = simular_registro(aluno_teste, 0, 0, 1.0, 0, 0, rng4)
    print("p_abst=1 ->", tudo_abst["nome_lido"], "|", tudo_abst["matricula_lida"])

    from base_alunos import carregar_base
    from normalizacao import normalizar

    alunos = carregar_base("dados/filter_hc_student.xlsx")
    cicero = next(a for a in alunos if "CICERO" in normalizar(a["nome"]))
    nome_norm = normalizar(cicero["nome"])
    print("Base       :", cicero["nome"], "|", cicero["matricula"])
    print("Normalizado:", nome_norm)
    aluno_norm = {"matricula": cicero["matricula"], "nome": nome_norm}
    for _ in range(3):
        reg = simular_registro(aluno_norm, 0.05, 0.03, 0.05, 0.15, 0.15, rng4)
        print("Lido       :", reg["nome_lido"], "|", reg["matricula_lida"])