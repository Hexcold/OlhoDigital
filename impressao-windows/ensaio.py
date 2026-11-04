"""Ensaio de caixa-preta do `.exe`: abre como a pessoa abriria e usa a tela pela API.

    python impressao-windows/ensaio.py dist/OlhoDigital-Impressao.exe saida-ensaio

Só biblioteca padrão, para rodar fora do ambiente do programa. A pasta pessoal simulada
tem acento e espaço de propósito: `C:\\Users\\João da Silva` é comum no Brasil, e é o que
mais quebra programa de linha de comando no Windows. Os PDFs ficam em `saida`, para serem
comparados pixel a pixel com os da máquina onde a folha foi medida.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ESPERA_S = 240
CHAVES = ("ABCDEFGHIJKLMNOP", "QRSTUVWXYZ")      # as 26 letras, para comparar a fonte
FOLHAS_GRANDE = 600   # com o caminho inteiro, a junção estouraria no Windows perto de 500
COMPLEMENTO = "CAMPUS QUIXADÁ"

# Roda em processo à parte porque quem se liga ao console recebe o aviso de fechar junto.
FECHAR_JANELA = """
import ctypes, sys
kernel32 = ctypes.WinDLL("kernel32")
user32 = ctypes.WinDLL("user32")
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
kernel32.FreeConsole()
if not kernel32.AttachConsole(int(sys.argv[1])):
    sys.exit(2)
janela = kernel32.GetConsoleWindow()
if not janela:
    sys.exit(3)
user32.PostMessageW(janela, 0x0010, 0, 0)   # WM_CLOSE, o mesmo que clicar no X
"""

cliente = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Programa:
    """O `.exe` rodando, com a pasta pessoal simulada."""

    def __init__(self, exe: Path, ambiente: dict, log: Path) -> None:
        self.exe, self.ambiente, self.log = exe, ambiente, log
        self.processo = None
        self.base = ""

    def abrir(self, console_proprio: bool = False) -> float:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            porta = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{porta}"
        inicio = time.monotonic()
        with open(self.log, "ab") as log:
            self.processo = subprocess.Popen(
                [str(self.exe), "--porta", str(porta), "--sem-navegador"],
                env=self.ambiente, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_CONSOLE if console_proprio else 0)
        while time.monotonic() - inicio < ESPERA_S:
            if self.processo.poll() is not None:
                falhar(f"o programa fechou sozinho, código {self.processo.returncode}")
            try:
                cliente.open(self.base + "/api/saude", timeout=2).close()
                return time.monotonic() - inicio
            except OSError:
                time.sleep(0.25)
        falhar(f"o programa não respondeu em {ESPERA_S} s")

    def fechar(self) -> None:
        if self.processo is None or self.processo.poll() is not None:
            return
        if os.name == "nt":
            # o `.exe` de arquivo único são dois processos, pai e filho
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.processo.pid)],
                           capture_output=True)
        else:
            self.processo.terminate()
        self.processo.wait(timeout=30)


def falhar(motivo: str) -> None:
    print(f"\nFALHOU: {motivo}", flush=True)
    raise SystemExit(1)


def confere(condicao: bool, o_que: str) -> None:
    if not condicao:
        falhar(o_que)
    print(f"  ok  {o_que}", flush=True)


def pedir(base, metodo, caminho, corpo=None, tipo="application/json", host=None):
    pedido = urllib.request.Request(base + caminho, data=corpo, method=metodo)
    if corpo is not None:
        pedido.add_header("Content-Type", tipo)
    if host:
        pedido.add_header("Host", host)
    try:
        with cliente.open(pedido, timeout=900) as resposta:
            return resposta.status, resposta.read()
    except urllib.error.HTTPError as erro:
        return erro.code, erro.read()


def enviar_json(base, metodo, caminho, dados):
    return pedir(base, metodo, caminho, json.dumps(dados).encode("utf-8"))


def enviar_arquivo(base, caminho, nome, conteudo):
    fronteira = "ensaio-olhodigital"
    corpo = (f'--{fronteira}\r\nContent-Disposition: form-data; name="arquivo"; '
             f'filename="{nome}"\r\nContent-Type: application/octet-stream\r\n\r\n'
             ).encode() + conteudo + f"\r\n--{fronteira}--\r\n".encode()
    return pedir(base, "POST", caminho, corpo, f"multipart/form-data; boundary={fronteira}")


def tipo_do_arquivo(base, caminho):
    with cliente.open(base + caminho, timeout=30) as resposta:
        return resposta.headers.get("content-type", "")


def cronometrado(chamada, *args):
    inicio = time.monotonic()
    resultado = chamada(*args)
    return resultado, round(time.monotonic() - inicio, 1)


def ensaiar(programa: Programa, saida: Path, dados: Path) -> dict:
    tempos = {"abrir_s": round(programa.abrir(), 1)}
    base = programa.base
    print(f"abriu em {tempos['abrir_s']} s", flush=True)

    print("\nsó a impressão, só neste computador")
    estado, html = pedir(base, "GET", "/")
    confere(estado == 200 and b'id="root"' in html, "a tela abre")
    arquivos = [a.decode() for a in re.findall(rb'"(/assets/[^"]+)"', html)]
    confere(len(arquivos) >= 2, f"a tela pede {len(arquivos)} arquivos")
    for arquivo in arquivos:
        tipo = tipo_do_arquivo(base, arquivo)
        esperado = "text/css" if arquivo.endswith(".css") else "text/javascript"
        confere(tipo.startswith(esperado), f"{arquivo} vem como {tipo}")
    estado, corpo = pedir(base, "GET", "/api/eu")
    eu = json.loads(corpo)
    confere(estado == 200 and eu["papel"] == "impressao" and eu["login"] is False,
            "entra direto, com o papel de impressão")
    for caminho in ("/api/eventos", "/api/folhas", "/api/participantes", "/api/docs"):
        confere(pedir(base, "GET", caminho)[0] == 404, f"{caminho} não existe")
    confere(pedir(base, "POST", "/api/impressao/cadastrar", b"{}")[0] == 404,
            "cadastrar eventos não existe")
    confere(pedir(base, "GET", "/api/eu", host="exemplo.invalid")[0] == 400,
            "pedido com outro domínio é recusado")

    print("\nfolhas")
    estado, corpo = pedir(base, "GET", "/api/impressao/cabecalho")
    cabecalho = json.loads(corpo)["cabecalho"]
    confere(estado == 200 and cabecalho["titulo"].startswith("ENCONTROS"),
            "o cabeçalho padrão veio do pacote")

    itens = [{"linha": i + 2, "chave": c, "folhas": 1} for i, c in enumerate(CHAVES)]
    (estado, pdf), tempos["primeiro_pdf_s"] = cronometrado(
        enviar_json, base, "POST", "/api/impressao/pdf",
        {"itens": itens, "aceitar_alertas": True})
    confere(estado == 200 and pdf[:5] == b"%PDF-", f"PDF com as 26 letras, {pdf[:5]!r}")
    (saida / "chaves.pdf").write_bytes(pdf)

    estado, modelo = pedir(base, "GET", "/api/impressao/planilha/modelo")
    confere(estado == 200 and modelo[:2] == b"PK", "baixa o modelo de planilha")
    estado, corpo = enviar_arquivo(base, "/api/impressao/planilha", "chaves.xlsx", modelo)
    lote = json.loads(corpo)
    confere(estado == 200 and lote["total_chaves"] == 3 and lote["total_folhas"] == 25,
            "lê a planilha: 3 chaves, 25 folhas")
    (estado, pdf), tempos["pdf_25_paginas_s"] = cronometrado(
        enviar_json, base, "POST", "/api/impressao/pdf",
        {"itens": lote["itens"], "aceitar_alertas": False})
    confere(estado == 200 and pdf[:5] == b"%PDF-", "PDF do lote da planilha")
    (saida / "lote.pdf").write_bytes(pdf)

    (estado, pdf), tempos[f"pdf_{FOLHAS_GRANDE}_paginas_s"] = cronometrado(
        enviar_json, base, "POST", "/api/impressao/folhas",
        {"chave": "GOIABA", "folhas": FOLHAS_GRANDE})
    confere(estado == 200 and pdf[:5] == b"%PDF-", f"PDF de {FOLHAS_GRANDE} páginas")
    (saida / "grande.pdf").write_bytes(pdf)

    print("\ntextos")
    novo = dict(cabecalho, complemento=COMPLEMENTO)
    (estado, png), tempos["previa_s"] = cronometrado(
        enviar_json, base, "POST", "/api/impressao/cabecalho/previa",
        {"cabecalho": novo, "chave": "GOIABA"})
    confere(estado == 200 and png[:4] == b"\x89PNG", "prévia do cabeçalho")
    (saida / "previa.png").write_bytes(png)
    confere(enviar_json(base, "PUT", "/api/impressao/cabecalho", novo)[0] == 200,
            "salva o texto editado")
    confere((dados / "OlhoDigital" / "cabecalho.json").exists(),
            "o texto fica na pasta do usuário")

    programa.fechar()
    tempos["reabrir_s"] = round(programa.abrir(), 1)
    corpo = pedir(programa.base, "GET", "/api/impressao/cabecalho")[1]
    confere(json.loads(corpo)["cabecalho"]["complemento"] == COMPLEMENTO,
            "o texto editado continua depois de fechar e abrir")
    return tempos


def medir_fechar_no_x(programa: Programa, temporaria: Path) -> dict:
    """Fecha a janela como a pessoa fecharia e conta o que sobra na pasta temporária.

    A documentação do PyInstaller garante a limpeza ao terminar normalmente, e diz que a
    pasta fica se o programa for morto. Sobre o X ela não diz nada, então isto é medido.
    """
    programa.fechar()
    antes = set(temporaria.glob("_MEI*"))
    programa.abrir(console_proprio=True)
    aberta = set(temporaria.glob("_MEI*")) - antes
    r = subprocess.run([sys.executable, "-c", FECHAR_JANELA, str(programa.processo.pid)],
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode in (2, 3):
        return {"medido": False, "motivo": f"sem console, código {r.returncode}"}
    try:
        programa.processo.wait(timeout=30)
    except subprocess.TimeoutExpired:
        return {"medido": False, "motivo": "o programa não fechou em 30 s"}
    time.sleep(5)
    sobrou = set(temporaria.glob("_MEI*")) - antes
    tamanho = sum(a.stat().st_size for p in sobrou for a in p.rglob("*") if a.is_file())
    return {"medido": True, "pastas_ao_abrir": len(aberta), "pastas_que_sobraram": len(sobrou),
            "mb_que_sobraram": round(tamanho / 1e6, 1)}


def main() -> None:
    exe, saida = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    saida.mkdir(parents=True, exist_ok=True)
    pessoal = Path(tempfile.mkdtemp(prefix="ensaio_")) / "João da Silva"
    dados = pessoal / "AppData" / "Local"
    temporaria = dados / "Temp"
    temporaria.mkdir(parents=True)
    ambiente = dict(os.environ, LOCALAPPDATA=str(dados), TEMP=str(temporaria),
                    TMP=str(temporaria), TMPDIR=str(temporaria))
    print(f"pasta pessoal simulada: {pessoal}", flush=True)

    programa = Programa(exe, ambiente, saida / "exe.log")
    try:
        tempos = ensaiar(programa, saida, dados)
        if os.name == "nt":
            tempos["fechar_no_x"] = medir_fechar_no_x(programa, temporaria)
    finally:
        programa.fechar()
        if (saida / "exe.log").exists():
            print("\n--- saída do programa ---")
            print((saida / "exe.log").read_text(encoding="utf-8", errors="replace")[-3000:])
    (saida / "relatorio.json").write_text(json.dumps(tempos, indent=2), encoding="utf-8")
    print("\ntempos:", tempos, f"\ntamanho: {exe.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
