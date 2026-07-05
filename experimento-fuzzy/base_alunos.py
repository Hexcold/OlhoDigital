from openpyxl import load_workbook
from collections import Counter


def carregar_base(caminho: str, apenas_ativos: bool = False) -> list[dict]:
    """Lê a aba 'students' e retorna [{'matricula', 'nome', 'status'}, ...].
    Descarta registros sem matrícula ou nome; remove matrículas duplicadas
    mantendo a primeira ocorrência."""
    wb = load_workbook(caminho, read_only=True, data_only=True)
    ws = wb["students"]

    alunos = []
    vistas = set()

    for linha in ws.iter_rows(min_row=2, values_only=True):
        matricula_bruta = linha[0]
        nome = linha[1]
        status = linha[3]

        if matricula_bruta is None or nome is None:
            continue

        if apenas_ativos and status != "ATIVO":
            continue

        matricula = str(int(matricula_bruta))

        if matricula in vistas:
            continue
        vistas.add(matricula)

        alunos.append({"matricula": matricula, "nome": nome, "status": status})

    return alunos


if __name__ == "__main__":
    alunos = carregar_base("dados/filter_hc_student.xlsx")
    print("Total (base completa):", len(alunos))

    tamanhos = Counter(len(a["matricula"]) for a in alunos)
    print("Tamanhos de matrícula:", dict(tamanhos))

    matriculas = [a["matricula"] for a in alunos]
    print("Duplicadas após dedup (deve ser 0):", len(matriculas) - len(set(matriculas)))

    ativos = carregar_base("dados/filter_hc_student.xlsx", apenas_ativos=True)
    print("Total (apenas ATIVO):", len(ativos))