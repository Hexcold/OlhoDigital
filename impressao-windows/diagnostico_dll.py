"""Temporário: em que condição o pdftoppm do pacote quebra no Windows."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AJUDANTE = r"""
import ctypes, subprocess, sys
pasta, cwd, *comando = sys.argv[1:]
if pasta != "-":
    ctypes.windll.kernel32.SetDllDirectoryW(pasta)
print(subprocess.run(comando, cwd=cwd, capture_output=True).returncode)
"""


def rodar(exe, cwd, pasta_dll="-"):
    r = subprocess.run([sys.executable, "-c", AJUDANTE, str(pasta_dll), str(cwd), str(exe),
                        "-r", "150", "-png", "-singlefile", "f.pdf", "saida"],
                       capture_output=True, text=True)
    return r.stdout.strip() or f"ajudante falhou: {r.stderr[-200:]}"


def diagnosticar(meipass: Path) -> None:
    programas = Path(__file__).parent / "programas"
    pdftoppm = programas / "pdftoppm.exe"
    print(f"\ndiagnóstico: {meipass}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "f.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40mm" '
                                    'height="20mm"><text x="5" y="30" font-size="20">A</text></svg>')
        subprocess.run([str(programas / "rsvg-convert.exe"), "--format=pdf", "--output", "f.pdf",
                        "f.svg"], cwd=base, check=True)
        acento = base / "João da Silva" / "programas"
        shutil.copytree(programas, acento)

        sem_crt, so_crt = base / "sem_crt", base / "so_crt"
        sem_crt.mkdir()
        so_crt.mkdir()
        for arquivo in meipass.iterdir():
            if arquivo.is_file():
                crt = arquivo.name.lower().startswith(("ucrtbase", "api-ms-win"))
                shutil.copy(arquivo, so_crt if crt else sem_crt)

        with open(base / "log.txt", "ab") as log:
            arquivo_saida = subprocess.run(
                [str(pdftoppm), "-r", "150", "-png", "-singlefile", "f.pdf", "saida"], cwd=base,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode

        casos = {
            "caminho ASCII, sem busca herdada": rodar(pdftoppm, base),
            "saída em arquivo": arquivo_saida,
            "caminho com acento": rodar(acento / "pdftoppm.exe", base),
            "dentro do _MEI, sem busca herdada": rodar(meipass / "programas" / "pdftoppm.exe", base),
            "dentro do _MEI, busca herdada (como no exe)":
                rodar(meipass / "programas" / "pdftoppm.exe", base, meipass),
            "caminho ASCII, busca herdada": rodar(pdftoppm, base, meipass),
            "busca herdada, só as DLLs de CRT": rodar(pdftoppm, base, so_crt),
            "busca herdada, sem as DLLs de CRT": rodar(pdftoppm, base, sem_crt),
        }
    for caso, codigo in casos.items():
        print(f"  {caso:46} código {codigo}", flush=True)
    print(flush=True)
