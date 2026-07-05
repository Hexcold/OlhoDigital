from openpyxl import load_workbook
from collections import Counter

CAMINHO = "dados/filter_hc_student.xlsx"

wb = load_workbook(CAMINHO, read_only=True, data_only=True)
print("Abas encontradas:", wb.sheetnames)

ws = wb["students"]

alunos = []

for linha in ws.iter_rows(min_row=2, values_only=True):
    matricula_bruta = linha[0]
    nome = linha[1]
    status = linha[3]
        
    if matricula_bruta is None or nome is None:
        print("Linha descartada pela guarda:", linha)
        continue
        
    matricula = str(int(matricula_bruta))
        
    alunos.append({"matricula": matricula, "nome": nome, "status": status})
        
print("Total de alunos carregados:", len(alunos))
print("Primeiros 3:")
for aluno in alunos[:3]:
    print(" ", aluno)

for a in alunos:
    if len(a["matricula"]) != 6:
        print("Matrícula fora do padrão:", a)
    
print("\n--- PROFILING ---")

tamanhos = Counter(len(a["matricula"]) for a in alunos)
print("Tamanho de matriculas:", dict(tamanhos))

matriculas = [a["matricula"] for a in alunos]
print("Matriculas duplicadas:", len(matriculas) - len(set(matriculas)))

estranhos = set()
for a in alunos:
    for ch in a["nome"]:
        if not ("A" <= ch <= "Z" or ch == " "):
            estranhos.add(ch)
            
print("Caracteres fora A-Z/espaço:", estranhos)
    
print("Status:", dict(Counter(a["status"] for a in alunos)))

contagem = Counter(matriculas)
for mat, vezes in contagem.items():
    if vezes > 1:
        print("Matricula repetidas:", mat, f"({vezes}x)")
        for a in alunos:
            if a["matricula"] == mat:
                print(" ", a["nome"], "|", a["status"])
            
            