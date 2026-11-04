"""Leitura da base de referência — o contradomínio contra o qual se identifica.

Duas planilhas convivem, com formatos diferentes (ver D07 no vault):

  - `Lista de Alunos Campus Quixadá.xlsx` — **vigente**. Uma aba por data de
    extração; a aba `21.08.2026` tem 1.334 matriculados e as colunas
    Matrícula/Nome/Curso. É a lista de matriculados na data, então não tem
    coluna de status: quem não está matriculado simplesmente não está lá.
  - `filter_hc_student.xlsx`, aba `students` — histórica. 4.321 vínculos, com
    coluna Status.

Por isso as colunas são localizadas **pelo cabeçalho**, não por índice fixo: o
índice 3 era Status numa planilha e não existe na outra. E a aba é escolhida
pela data no nome, para que trocar de extração não exija tocar no código.
"""

from openpyxl import load_workbook
from collections import Counter
import datetime as dt
import re

from normalizacao import normalizar

# Aba de extração: o nome é a data INTEIRA, nada mais. A planilha vigente também
# tem abas descritivas ("Alunos com necessidade especial", "Atvs 09.05.23") que
# não são extrações — casar só o nome inteiro impede que uma delas seja eleita.
RE_DATA_ABA = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}|\d{4})$")


def _data_da_aba(nome: str) -> dt.date | None:
    """Interpreta 'DD.MM.AAAA' ou 'DD.MM.AA' como data. None se não for uma."""
    m = RE_DATA_ABA.match(nome.strip())
    if not m:
        return None
    dia, mes, ano = (int(g) for g in m.groups())
    if len(m.group(3)) == 2:
        ano += 2000
    try:
        return dt.date(ano, mes, dia)
    except ValueError:
        return None


def _mapa_colunas(ws) -> dict[str, int]:
    """Índice de cada campo pelo cabeçalho normalizado da primeira linha."""
    cabecalho = next(ws.iter_rows(max_row=1, values_only=True), ())
    mapa = {}
    for i, celula in enumerate(cabecalho):
        if celula is None:
            continue
        chave = normalizar(str(celula))
        if chave in ("MATRICULA", "NOME", "CURSO", "STATUS") and chave not in mapa:
            mapa[chave] = i
    return mapa


def escolher_aba(wb) -> str:
    """Aba de extração mais recente; se não houver nenhuma datada, a única que
    tem Matrícula E Nome no cabeçalho. Ambíguo é erro, não chute."""
    datadas = [(d, nome) for nome in wb.sheetnames if (d := _data_da_aba(nome))]
    if datadas:
        return max(datadas)[1]

    candidatas = [n for n in wb.sheetnames
                  if {"MATRICULA", "NOME"} <= set(_mapa_colunas(wb[n]))]
    if len(candidatas) == 1:
        return candidatas[0]
    raise ValueError(
        f"não sei qual aba usar em {wb.sheetnames}: nenhuma tem nome de data e "
        f"{len(candidatas)} têm Matrícula e Nome. Passe `aba=` explicitamente.")


def carregar_base(caminho: str, aba: str | None = None,
                  apenas_ativos: bool = False) -> list[dict]:
    """Lê a base e devolve [{'matricula', 'nome', 'curso'}, ...].

    Sem `aba`, escolhe a extração mais recente (ver `escolher_aba`). Descarta
    registros sem matrícula ou nome e remove matrículas duplicadas, mantendo a
    primeira ocorrência.

    `apenas_ativos` só existe para a base histórica e falha alto se a aba não
    tiver coluna Status — filtrar por um campo ausente devolveria a base inteira
    em silêncio, que é justamente o erro que o filtro deveria evitar.
    """
    wb = load_workbook(caminho, read_only=True, data_only=True)
    ws = wb[aba] if aba else wb[escolher_aba(wb)]

    col = _mapa_colunas(ws)
    faltando = {"MATRICULA", "NOME"} - set(col)
    if faltando:
        raise ValueError(f"aba {ws.title!r} de {caminho} não tem coluna {faltando}")
    if apenas_ativos and "STATUS" not in col:
        raise ValueError(
            f"aba {ws.title!r} não tem coluna Status — nesta base o status não "
            f"existe porque ela já é a lista de matriculados (ver D07)")

    alunos = []
    vistas = set()

    for linha in ws.iter_rows(min_row=2, values_only=True):
        matricula_bruta = linha[col["MATRICULA"]]
        nome = linha[col["NOME"]]

        if matricula_bruta is None or nome is None:
            continue

        if apenas_ativos and linha[col["STATUS"]] != "ATIVO":
            continue

        # o Excel entrega a matrícula como float; float -> int -> str. A base
        # histórica tem uma de 5 dígitos, que fica como está (não completar com
        # zeros: o dígito ausente não é zero, é desconhecido).
        matricula = str(int(matricula_bruta))

        if matricula in vistas:
            continue
        vistas.add(matricula)

        curso = linha[col["CURSO"]] if "CURSO" in col else None
        alunos.append({"matricula": matricula, "nome": nome, "curso": curso})

    return alunos


if __name__ == "__main__":
    VIGENTE = "dados/Lista de Alunos Campus Quixadá.xlsx"
    HISTORICA = "dados/filter_hc_student.xlsx"

    from openpyxl import load_workbook as _lw
    wb = _lw(VIGENTE, read_only=True, data_only=True)
    print("abas da base vigente:")
    for nome in wb.sheetnames:
        d = _data_da_aba(nome)
        print(f"  {nome!r:<40} {'-> ' + str(d) if d else '(não é extração)'}")
    print("escolhida:", escolher_aba(wb))

    alunos = carregar_base(VIGENTE)
    print(f"\nvigente: {len(alunos)} pessoas")
    print("  tamanhos de matrícula:", dict(Counter(len(a["matricula"]) for a in alunos)))
    print("  duplicadas após dedup (deve ser 0):",
          len(alunos) - len({a["matricula"] for a in alunos}))
    print("  cursos:", len({a["curso"] for a in alunos}))
    print("  exemplo:", alunos[0])

    # a aba sem nome de data ainda tem que ser alcançável por nome explícito
    antiga = carregar_base(VIGENTE, aba="04.12.2025")
    print(f"\naba 04.12.2025 (explícita): {len(antiga)} pessoas")

    hist = carregar_base(HISTORICA)
    print(f"\nhistórica (aba resolvida por cabeçalho): {len(hist)} vínculos")
    ativos = carregar_base(HISTORICA, apenas_ativos=True)
    print(f"  apenas ATIVO: {len(ativos)}")

    try:
        carregar_base(VIGENTE, apenas_ativos=True)
    except ValueError as e:
        print(f"\napenas_ativos na base vigente falha alto, como deve: {e}")
