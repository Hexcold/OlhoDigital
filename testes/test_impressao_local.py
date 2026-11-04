"""O `.exe` da impressão: só a impressão, só neste computador, e o que muda no Windows."""
import hashlib
import importlib.util
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from apoio import ESTADO, EU, PIPELINE, RAIZ, gf, info_pdf, lf
from api import impressao_local

PACOTE = RAIZ / "impressao-windows"
FONTES = PACOTE / "fontes"

# Listas fixadas aqui, e não lidas do código: rota nova na porta aberta tem que ser
# decisão de alguém, e o teste é onde essa decisão aparece.
ROTAS = {("GET", "/eu"), ("GET", "/saude"),
         ("GET", "/impressao/cabecalho"), ("PUT", "/impressao/cabecalho"),
         ("POST", "/impressao/cabecalho/previa"), ("POST", "/impressao/folhas"),
         ("GET", "/impressao/planilha/modelo"), ("POST", "/impressao/planilha"),
         ("POST", "/impressao/pdf")}
FORA_DO_PACOTE = ("sqlalchemy", "psycopg", "torch", "keras", "sklearn", "api.pipeline",
                  "api.db", "api.tabelas", "api.usuarios", "api.sessao",
                  "identificar_folha", "ocr", "base_alunos", "quarentena")

# As fontes em que a chave impressa foi medida (A49), noto-fonts 2026.09.01.
SHA256_FONTES = {
    "NotoSans-Regular.ttf":
        "478c558ea716033cd60c03438f628dfa75694dcf6b5f6d505a2f05fd2b4f3823",
    "NotoSans-Bold.ttf":
        "1df075a380fc7cb898acf64c1f7b3b4dd780de3caa860178bf929de35817a913",
}

LIMITE_WINDOWS = 32_767    # caracteres da linha de comando no CreateProcess


def carregar_abrir():
    spec = importlib.util.spec_from_file_location("abrir", PACOTE / "abrir.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class SoImpressao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pasta = tempfile.TemporaryDirectory()
        Path(cls.pasta.name, "index.html").write_text("<html>tela</html>")
        cls.cliente = TestClient(impressao_local.criar(Path(cls.pasta.name)),
                                 base_url="http://127.0.0.1")

    @classmethod
    def tearDownClass(cls):
        cls.pasta.cleanup()

    def test_as_rotas_sao_exatamente_as_da_impressao(self):
        # `app.routes` esconde as rotas incluídas num objeto só. O OpenAPI enxerga todas.
        rotas = {(metodo.upper(), caminho)
                 for caminho, operacoes in impressao_local.app.openapi()["paths"].items()
                 for metodo in operacoes}
        self.assertEqual(rotas, ROTAS)

    def test_entra_direto_e_so_ve_a_impressao(self):
        r = self.cliente.get("/api/eu")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"usuario": "local", "papel": "impressao",
                                    "login": False})

    def test_o_que_nao_e_impressao_nao_existe(self):
        for caminho in ("/api/eventos", "/api/folhas", "/api/participantes",
                        "/api/login", "/api/docs", "/api/openapi.json"):
            self.assertEqual(self.cliente.get(caminho).status_code, 404, caminho)
        self.assertEqual(self.cliente.post("/api/impressao/cadastrar", json={}).status_code,
                         404)

    def test_gera_pdf(self):
        r = self.cliente.post("/api/impressao/folhas",
                              json={"chave": "GOIABA", "folhas": 2})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:5], b"%PDF-")

    def test_recusa_outro_dominio(self):
        for host, esperado in (("127.0.0.1:53124", 200), ("localhost:53124", 200),
                               ("exemplo.invalid", 400), ("127.0.0.1.exemplo.invalid", 400)):
            r = self.cliente.get("/api/eu", headers={"host": host})
            self.assertEqual(r.status_code, esperado, host)

    def test_o_registro_do_windows_nao_deixa_a_tela_em_branco(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for nome in ("index.html", "a.js", "a.css", "a.svg"):
                (pasta / nome).write_text("x")
            # o que o registro de algumas máquinas diz, e o `mimetypes` obedece
            for extensao in (".js", ".css", ".svg"):
                mimetypes.add_type("text/plain", extensao)
            cliente = TestClient(impressao_local.criar(pasta), base_url="http://127.0.0.1")
            for nome, tipo in (("a.js", "text/javascript"), ("a.css", "text/css"),
                               ("a.svg", "image/svg+xml")):
                self.assertTrue(cliente.get("/" + nome).headers["content-type"].startswith(tipo),
                                nome)

    def test_nao_carrega_banco_nem_leitura(self):
        codigo = ("import sys\n"
                  f"sys.path[:0] = [{str(RAIZ)!r}, {str(PIPELINE)!r}]\n"
                  "import api.impressao_local\n"
                  "from api.rotas import impressao\n"
                  "impressao._lote()\n"
                  f"print(sorted(set({FORA_DO_PACOTE!r}) & set(sys.modules)))\n")
        saida = subprocess.run([sys.executable, "-W", "ignore", "-c", codigo], cwd=RAIZ,
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(saida.strip(), "[]")


class Abrir(unittest.TestCase):
    def setUp(self):
        self.abrir = carregar_abrir()

    def test_so_escuta_neste_computador(self):
        self.assertEqual(self.abrir.ENDERECO, "127.0.0.1")
        with socket_livre() as porta:
            self.assertGreater(porta, 0)

    def test_o_texto_salvo_fica_na_pasta_do_usuario(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            pasta = self.abrir.pasta_de_dados()
            self.assertEqual(pasta, Path(tmp) / "OlhoDigital")
            self.assertTrue(pasta.is_dir())

    def test_o_ambiente_aponta_para_o_pacote(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            pacote = Path(tmp)
            (pacote / "programas").mkdir()
            self.abrir.preparar_ambiente(pacote, pacote / "dados")
            self.assertEqual(os.environ["PATH"].split(os.pathsep)[0],
                             str(pacote / "programas"))
            self.assertEqual(os.environ["FONTCONFIG_FILE"],
                             str(pacote / "fontes" / "fonts.conf"))
            self.assertEqual(os.environ["PANGOCAIRO_BACKEND"], "fc")
            self.assertEqual(os.environ["XDG_CACHE_HOME"], str(pacote / "cache"))
            self.assertEqual(os.environ["OLHODIGITAL_CABECALHO"],
                             str(pacote / "dados" / "cabecalho.json"))

    def test_os_programas_chamados_nao_herdam_a_busca_de_dll(self):
        kernel32 = mock.Mock()
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("ctypes.windll", mock.Mock(kernel32=kernel32), create=True):
            self.abrir.liberar_busca_de_dll()
        kernel32.SetDllDirectoryW.assert_called_once_with(None)

    def test_a_busca_so_e_liberada_depois_das_importacoes(self):
        fonte = (PACOTE / "abrir.py").read_text(encoding="utf-8")
        corpo = fonte[fonte.index("def main()"):]
        self.assertLess(corpo.index("_lote()"), corpo.index("liberar_busca_de_dll()"))
        self.assertLess(corpo.index("liberar_busca_de_dll()"), corpo.index("uvicorn.run"))

    def test_fora_do_exe_a_busca_nao_e_tocada(self):
        kernel32 = mock.Mock()
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("ctypes.windll", mock.Mock(kernel32=kernel32), create=True):
            self.abrir.liberar_busca_de_dll()
        kernel32.SetDllDirectoryW.assert_not_called()

    def test_sem_a_pasta_de_programas_usa_os_do_sistema(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            antes = os.environ["PATH"]
            self.abrir.preparar_ambiente(Path(tmp), Path(tmp))
            self.assertEqual(os.environ["PATH"], antes)


def socket_livre():
    import contextlib
    import socket

    @contextlib.contextmanager
    def contexto():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            yield s.getsockname()[1]
    return contexto()


class Fonte(unittest.TestCase):
    """Se a fonte mudar, a tabela de letras seguras de A49 deixa de valer sem erro."""

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        self.ambiente = dict(os.environ, FONTCONFIG_FILE=str(FONTES / "fonts.conf"),
                             FONTCONFIG_PATH=str(FONTES), PANGOCAIRO_BACKEND="fc",
                             XDG_CACHE_HOME=self.cache.name)

    def test_o_cache_de_fontes_nao_suja_a_pasta_de_trabalho(self):
        # A pasta de trabalho pode ser a do pendrive, só de leitura.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["rsvg-convert", "--format=png"], cwd=tmp, env=self.ambiente,
                           input=gf.desenhar_svg().encode(), capture_output=True, check=True)
            self.assertEqual(os.listdir(tmp), [])
        self.assertTrue(any(Path(self.cache.name).rglob("*")),
                        "o cache devia ter ido para a pasta indicada")

    def test_as_fontes_sao_as_medidas(self):
        for nome, esperado in SHA256_FONTES.items():
            self.assertEqual(hashlib.sha256((FONTES / nome).read_bytes()).hexdigest(),
                             esperado, nome)

    def test_a_licenca_acompanha_a_fonte(self):
        texto = (FONTES / "OFL.txt").read_text(encoding="utf-8")
        self.assertIn("The Noto Project Authors", texto)
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", texto)

    def test_a_troca_de_familia_e_a_mesma_do_docker(self):
        def troca(caminho):
            raiz = ET.parse(caminho).getroot()
            return (raiz.find("match/test/string").text,
                    raiz.find("match/edit/string").text)
        self.assertEqual(troca(FONTES / "fonts.conf"), troca(RAIZ / "docker" / "fontes.conf"))
        self.assertEqual(troca(FONTES / "fonts.conf"), ("DejaVu Sans", "Noto Sans"))

    @unittest.skipUnless(shutil.which("fc-list"), "sem fc-list")
    def test_so_a_fonte_do_pacote_e_visivel(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp) / "fontes"
            shutil.copytree(FONTES, pasta)
            self.ambiente.update(FONTCONFIG_FILE=str(pasta / "fonts.conf"),
                                 FONTCONFIG_PATH=str(pasta))
            saida = subprocess.run(["fc-list", ":", "file"], env=self.ambiente,
                                   capture_output=True, text=True, check=True).stdout
        # a linha é `caminho: `, e no Windows o caminho também tem dois-pontos
        nomes = sorted(Path(l.strip().rstrip(":")).name for l in saida.splitlines()
                       if l.strip())
        self.assertEqual(nomes, sorted(SHA256_FONTES))

    def test_a_folha_sai_com_as_fontes_do_pacote(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "f.svg").write_text(gf.desenhar_svg(chave="GOIABA", cabecalho=EU),
                                         encoding="utf-8")
            subprocess.run(["rsvg-convert", "--format=pdf", "--output", "f.pdf", "f.svg"],
                           cwd=pasta, env=self.ambiente, check=True)
            saida = subprocess.run(["pdffonts", "f.pdf"], cwd=pasta, capture_output=True,
                                   text=True, check=True).stdout
        fontes = {l.split()[0].split("+")[-1] for l in saida.splitlines()[2:]}
        self.assertEqual(fontes, {"NotoSans-Regular", "NotoSans-Bold"})


class NoWindows(unittest.TestCase):
    """Limites do Windows que o Linux não tem. Daqui só dá para garantir que o código
    não depende deles; quem prova é o ensaio na máquina Windows."""

    def _espiar(self):
        """Roda tudo de verdade, menos o pdfunite, e guarda a linha de comando."""
        chamadas = []
        real = subprocess.run

        def espia(args, **kw):
            chamadas.append((list(args), kw.get("cwd")))
            if args[0] == "pdfunite":
                Path(kw["cwd"], "lote.pdf").write_bytes(b"%PDF-")
                return subprocess.CompletedProcess(args, 0, b"", b"")
            return real(args, **kw)
        return chamadas, mock.patch("subprocess.run", espia)

    def test_o_lote_maximo_cabe_na_linha_de_comando(self):
        chamadas, espia = self._espiar()
        with tempfile.TemporaryDirectory() as tmp, espia:
            lf.gerar_avulsas("GOIABA", lf.LIMITE_PAGINAS, EU, str(Path(tmp) / "saida.pdf"))
        args = next(a for a, _ in chamadas if a[0] == "pdfunite")
        self.assertEqual(len(args), lf.LIMITE_PAGINAS + 2)
        self.assertLess(len(subprocess.list2cmdline(args)), LIMITE_WINDOWS)

    def test_programa_nenhum_recebe_o_caminho_da_pasta(self):
        pasta = ESTADO / "João da Silva"
        pasta.mkdir(exist_ok=True)
        chamadas, espia = self._espiar()
        with mock.patch.object(tempfile, "tempdir", str(pasta)), espia:
            lf.gerar_avulsas("GOIABA", 2, EU, str(pasta / "saida.pdf"))
        externas = [a for a, _ in chamadas if a[0] != "rsvg-convert" or "--output" in a]
        self.assertTrue(externas)
        for args in externas:
            self.assertNotIn("João", " ".join(args), args[0])

    def test_pasta_com_acento_e_espaco(self):
        pasta = ESTADO / "João da Conceição"
        pasta.mkdir(exist_ok=True)
        saida = pasta / "saída.pdf"
        with mock.patch.object(tempfile, "tempdir", str(pasta)):
            resumo = lf.gerar_avulsas("GOIABA", 3, EU, str(saida))
        self.assertEqual(resumo["paginas"], 3)
        # A cópia é do teste: `pdfinfo` não faz parte do que roda no `.exe`.
        copia = shutil.copy(saida, ESTADO / "copia.pdf")
        self.assertEqual(info_pdf(str(copia))["paginas"], 3)

    def test_a_deteccao_nao_usa_imread(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = gf.gerar(str(Path(tmp) / "f.pdf"), "GOIABA", EU)
            with mock.patch("cv2.imread", side_effect=AssertionError("imread")):
                achados = gf.conferir_deteccao(pdf, (200,))
        self.assertEqual(achados[200], [0, 1, 2, 3])

    def test_a_deteccao_nao_deixa_lixo_na_pasta(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = gf.gerar(str(Path(tmp) / "f.pdf"), "GOIABA", EU)
            antes = sorted(os.listdir(tmp))
            gf.conferir_deteccao(pdf, (200,))
            self.assertEqual(sorted(os.listdir(tmp)), antes)


class Pacote(unittest.TestCase):
    def test_a_receita_so_leva_o_que_foi_listado(self):
        receita = (PACOTE / "impressao.spec").read_text(encoding="utf-8")
        inicio, fim = receita.index("dados = ["), receita.index("analise = Analysis")
        trecho = receita[inicio:fim]
        for proibido in ("xlsx", '"dados"', "modelo", "usuarios", "vault"):
            self.assertNotIn(proibido, trecho)

    def test_o_exe_se_descompacta_em_pasta_sem_acento(self):
        # A51: o poppler quebra dentro de `C:\\Users\\João\\...`. Quem prova é o ensaio no
        # Windows, com a pasta pessoal simulada; aqui fica só a trava contra tirar a opção.
        receita = (PACOTE / "impressao.spec").read_text(encoding="utf-8")
        self.assertIn(r'EXTRACAO = r"%ProgramData%\OlhoDigital"', receita)
        self.assertIn("runtime_tmpdir=EXTRACAO", receita)

    def test_os_programas_entram_depois_da_analise(self):
        receita = (PACOTE / "impressao.spec").read_text(encoding="utf-8")
        self.assertNotIn('"programas"), "programas"', receita)
        self.assertLess(receita.index("analise = Analysis"), receita.index("Tree("))

    def test_as_dependencias_nao_trazem_a_leitura(self):
        linhas = (PACOTE / "requirements.txt").read_text(encoding="utf-8").splitlines()
        pacotes = {l.split("==")[0].lower() for l in linhas if "==" in l}
        self.assertFalse(pacotes & {"torch", "keras", "scikit-learn", "sqlalchemy",
                                    "psycopg", "scipy", "matplotlib"})

    def test_as_versoes_sao_as_do_resto_do_projeto(self):
        def fixadas(caminho):
            return dict(l.split("[")[0].split("==") if "[" not in l else
                        (l.split("[")[0], l.split("==")[1])
                        for l in caminho.read_text(encoding="utf-8").splitlines()
                        if "==" in l and not l.startswith("#"))
        projeto = fixadas(RAIZ / "requirements.txt") | fixadas(RAIZ / "api" / "requirements.txt")
        for pacote, versao in fixadas(PACOTE / "requirements.txt").items():
            if pacote != "pyinstaller":
                self.assertEqual(versao, projeto[pacote], pacote)

    def test_o_que_o_build_gera_fica_fora_do_git(self):
        for caminho in ("dist/OlhoDigital-Impressao.exe", "build/x",
                        "impressao-windows/programas/x.dll", "impressao-windows/cache/x"):
            r = subprocess.run(["git", "check-ignore", "-q", caminho], cwd=RAIZ)
            self.assertEqual(r.returncode, 0, caminho)
