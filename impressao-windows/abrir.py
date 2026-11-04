"""Abre a tela de impressão no navegador. É este programa que vira o `.exe`.

    python impressao-windows/abrir.py

Roda aqui mesmo no Linux, usando os programas do sistema, e é assim que ele é ensaiado
antes de ir para o Windows. Só escuta em 127.0.0.1: ver `api/impressao_local.py`.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ENDERECO = "127.0.0.1"
ESPERA_S = 120

AVISO = """
OlhoDigital: impressão das folhas

Aberto no navegador em {url}
Deixe esta janela aberta enquanto usa. Para encerrar, feche esta janela.
"""


def pasta_do_pacote() -> Path:
    """Onde estão as fontes e os programas: `_MEIPASS` é onde o `.exe` se descompacta."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def pasta_de_dados() -> Path:
    """Onde o cabeçalho salvo mora.

    Não fica ao lado do `.exe`, que pode estar num pendrive só de leitura, nem em
    `_MEIPASS`, que some quando o programa fecha.
    """
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    pasta = Path(base) / "OlhoDigital"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def preparar_ambiente(pacote: Path, dados: Path) -> None:
    """Tem que rodar antes de importar a API: `lote_folhas` lê o caminho no import."""
    programas = pacote / "programas"
    if programas.is_dir():
        os.environ["PATH"] = f"{programas}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["FONTCONFIG_FILE"] = str(pacote / "fontes" / "fonts.conf")
    os.environ["FONTCONFIG_PATH"] = str(pacote / "fontes")
    # Sem isto o Pango do Windows pergunta ao sistema, e a fonte instalada ganha.
    os.environ["PANGOCAIRO_BACKEND"] = "fc"
    os.environ["XDG_CACHE_HOME"] = str(pacote / "cache")
    os.environ["OLHODIGITAL_CABECALHO"] = str(dados / "cabecalho.json")


def liberar_busca_de_dll() -> None:
    """Devolve a busca de DLLs ao normal, para os programas que o `.exe` chama.

    Ao abrir, o `.exe` aponta essa busca para dentro do pacote, e quem ele chama herda
    isso. Zerar é o que a documentação do PyInstaller recomenda, e faz os programas rodarem
    como nos testes. Só depois das importações, que ainda dependem dela.
    """
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind((ENDERECO, 0))
        return s.getsockname()[1]


def abrir_quando_pronto(url: str) -> None:
    # Sem proxy: o do sistema, quando existe, não enxerga 127.0.0.1.
    cliente = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    limite = time.monotonic() + ESPERA_S
    while time.monotonic() < limite:
        try:
            cliente.open(url + "api/saude", timeout=2).close()
        except OSError:
            time.sleep(0.2)
        else:
            webbrowser.open(url)
            return
    print(f"O navegador não foi aberto. Abra {url} à mão.", flush=True)


def ler_argumentos() -> argparse.Namespace:
    leitor = argparse.ArgumentParser(description="Impressão das folhas do OlhoDigital")
    leitor.add_argument("--porta", type=int, default=0, help="padrão: uma porta livre")
    leitor.add_argument("--sem-navegador", action="store_true",
                        help="não abre o navegador (usado nos ensaios)")
    return leitor.parse_args()


def main() -> None:
    argumentos = ler_argumentos()
    preparar_ambiente(pasta_do_pacote(), pasta_de_dados())

    import uvicorn

    from api.impressao_local import criar
    from api.rotas.impressao import _lote

    aplicacao = criar()
    _lote()      # carrega cv2 e numpy aqui, antes de liberar a busca
    liberar_busca_de_dll()

    porta = argumentos.porta or porta_livre()
    url = f"http://{ENDERECO}:{porta}/"
    print(AVISO.format(url=url), flush=True)
    if not argumentos.sem_navegador:
        threading.Thread(target=abrir_quando_pronto, args=(url,), daemon=True).start()
    uvicorn.run(aplicacao, host=ENDERECO, port=porta, http="h11", ws="none",
                loop="asyncio", lifespan="off", log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception:                                       # noqa: BLE001
        # Sem isto a janela fecha antes de dar tempo de ler o erro.
        traceback.print_exc()
        try:
            input("\nNão foi possível abrir. Aperte Enter para fechar.")
        except EOFError:
            pass
        sys.exit(1)
