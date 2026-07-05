import unicodedata


def normalizar(texto: str) -> str:
    """Traduz um texto para o alfabeto da folha: A-Z e espaço.
    Decompõe acentos via Unicode NFD, remove as marcas, sobe para
    maiúsculas e descarta qualquer caractere fora do alfabeto."""
    decomposto = unicodedata.normalize("NFD", texto)
    sem_acentos = "".join(ch for ch in decomposto if unicodedata.category(ch) != "Mn")
    maiusculo = sem_acentos.upper()
    permitidos = "".join(ch for ch in maiusculo if "A" <= ch <= "Z" or ch == " ")
    return " ".join(permitidos.split())


if __name__ == "__main__":
    from base_alunos import carregar_base

    exemplos = ["CÍCERA DE TAL FICTÍCIA", "José da Conceição", "ANA  MARIA D'ÁVILA"]
    for e in exemplos:
        print(f"{e!r} -> {normalizar(e)!r}")

    alunos = carregar_base("dados/filter_hc_student.xlsx")

    mudaram = 0
    violacoes = 0
    for a in alunos:
        norm = normalizar(a["nome"])
        if norm != a["nome"]:
            mudaram += 1
        for ch in norm:
            if not ("A" <= ch <= "Z" or ch == " "):
                print("CONTRATO VIOLADO:", a["nome"], "->", norm)
                violacoes += 1

    print("Nomes alterados pela normalização:", mudaram)
    print("Violações do contrato (deve ser 0):", violacoes)