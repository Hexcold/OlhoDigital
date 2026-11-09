"""A API recebe de tudo: entrada ruim tem que virar 4xx claro, nunca erro 500."""
import json
import unittest

from fastapi.testclient import TestClient

from apoio import RAIZ, lf
from api.main import app

CLIENTE = TestClient(app, raise_server_exceptions=False)
BASE = {"titulo": "ENCONTROS UNIVERSITÁRIOS 2026", "subtitulo": "FOLHA DE PRESENÇA", "complemento": "",
        "instrucao": "USE LETRA DE FORMA MAIÚSCULA, UMA LETRA POR QUADRADO."}
ITEM = {"linha": 2, "chave": "GOIABA", "folhas": 1}


class Comum(unittest.TestCase):
    def setUp(self):
        lf.ARQUIVO_CABECALHO.unlink(missing_ok=True)


class Cabecalho(Comum):
    def test_leitura(self):
        r = CLIENTE.get("/impressao/cabecalho")
        self.assertEqual(r.status_code, 200)
        corpo = r.json()
        self.assertEqual(corpo["cabecalho"], BASE)
        self.assertIn("carimbo de versão", corpo["travado"])
        self.assertIn("grade de células", corpo["travado"])

    def test_salvar_e_reler(self):
        r = CLIENTE.put("/impressao/cabecalho", json={**BASE, "complemento": "Sala 1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CLIENTE.get("/impressao/cabecalho").json()["cabecalho"]["complemento"], "Sala 1")

    def test_campo_fora_do_contrato_e_recusado(self):
        for extra in ({"chave": "X"}, {"linhas": 40}, {"rotulo_nome": "X"}, {"carimbo": "v9"}, {"ARUCO_LADO_MM": 5}, {"lado_celula": 9}):
            with self.subTest(extra=extra):
                self.assertEqual(CLIENTE.put("/impressao/cabecalho", json={**BASE, **extra}).status_code, 422)
        self.assertFalse(lf.ARQUIVO_CABECALHO.exists())

    def test_tipo_errado(self):
        for corpo in ({}, {"titulo": 1}, {"titulo": None}, {"titulo": ["a"]}, [], "x"):
            with self.subTest(corpo=corpo):
                self.assertEqual(CLIENTE.put("/impressao/cabecalho", json=corpo).status_code, 422)

    def test_texto_que_nao_cabe(self):
        r = CLIENTE.put("/impressao/cabecalho", json={**BASE, "titulo": "W" * 60})
        self.assertEqual(r.status_code, 422)
        self.assertIn("não cabe", r.json()["detail"])

    def test_previa(self):
        r = CLIENTE.post("/impressao/cabecalho/previa", json={"cabecalho": BASE, "chave": "goiaba"})
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
        for chave, codigo in (("SALA2", 422), ("A" * 17, 422), ("", 200)):
            with self.subTest(chave=chave):
                self.assertEqual(CLIENTE.post("/impressao/cabecalho/previa", json={"cabecalho": BASE, "chave": chave}).status_code, codigo)

    def test_arquivo_salvo_quebrado_nao_vira_erro_500(self):
        lf.ARQUIVO_CABECALHO.write_text("{quebrado", encoding="utf-8")
        for r in (CLIENTE.get("/impressao/cabecalho"), CLIENTE.post("/impressao/folhas", json={"chave": "GOIABA"}), CLIENTE.post("/impressao/pdf", json={"itens": [ITEM]})):
            with self.subTest(url=str(r.request.url)):
                self.assertEqual(r.status_code, 422, r.text[:120])
                self.assertIn("cabeçalho", r.json()["detail"])


class Folhas(Comum):
    def test_gera(self):
        r = CLIENTE.post("/impressao/folhas", json={"chave": "goiaba", "folhas": 2})
        self.assertEqual((r.status_code, r.content[:4]), (200, b"%PDF"))
        self.assertIn('filename="folhas_GOIABA_2paginas.pdf"', r.headers["content-disposition"])

    def test_em_branco(self):
        r = CLIENTE.post("/impressao/folhas", json={})
        self.assertEqual(r.status_code, 200)
        self.assertIn("sem_chave", r.headers["content-disposition"])

    def test_entrada_ruim(self):
        for corpo in ({"chave": "SALA2"}, {"chave": 5}, {"chave": None}, {"folhas": 0}, {"folhas": -1}, {"folhas": 1.5}, {"folhas": "abc"}, {"folhas": None}, {"folhas": 10 ** 9}, {"folhas": 10 ** 30}, {"chave": "A" * 500}, {"x": 1}):
            with self.subTest(corpo=corpo):
                self.assertEqual(CLIENTE.post("/impressao/folhas", json=corpo).status_code, 422)


class Planilha(Comum):
    def enviar(self, nome, conteudo):
        return CLIENTE.post("/impressao/planilha", files={"arquivo": (nome, conteudo)})

    def test_modelo(self):
        r = CLIENTE.get("/impressao/planilha/modelo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.enviar("modelo.xlsx", r.content).json()["total_folhas"], 25)

    def test_erro_de_linha_nao_e_erro_de_http(self):
        r = self.enviar("a.csv", b"chave,folhas\nSALA2,1\nGOIABA,2\n")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["erros"]), 1)
        self.assertEqual(r.json()["total_chaves"], 1)

    def test_arquivos_ruins(self):
        for nome, conteudo in (("a.xlsx", b"lixo"), ("a.xlsx", b""), ("a.csv", b""), ("a.pdf", b"%PDF"), ("../../etc/passwd", b"x"), ("a.csv", b"\xff\xfe\x00\x00")):
            with self.subTest(nome=nome):
                self.assertEqual(self.enviar(nome, conteudo).status_code, 422)

    def test_sem_arquivo(self):
        self.assertEqual(CLIENTE.post("/impressao/planilha").status_code, 422)

    def test_ate_a_linha(self):
        conteudo = b"chave,folhas\nBANANA,3\nGOIABA,2\nMANGA,1\n"
        r = CLIENTE.post("/impressao/planilha", files={"arquivo": ("a.csv", conteudo)}, data={"ate_linha": "3"})
        self.assertEqual((r.status_code, [i["chave"] for i in r.json()["itens"]]), (200, ["BANANA", "GOIABA"]))
        for ruim in ("0", "-1", "abc", "1.5"):
            with self.subTest(ate_linha=ruim):
                r = CLIENTE.post("/impressao/planilha", files={"arquivo": ("a.csv", conteudo)}, data={"ate_linha": ruim})
                self.assertEqual(r.status_code, 422)

    def test_dia_vai_e_volta(self):
        r = self.enviar("a.csv", "chave;dia\nBANANA;11/11/2026\nGOIABA;\n".encode())
        self.assertEqual([(i["chave"], i["folhas"], i["dia"]) for i in r.json()["itens"]],
                         [("BANANA", 1, "2026-11-11"), ("GOIABA", 1, None)])
        self.assertEqual(r.json()["alertas"][0], lf.SEM_FOLHAS)

    def test_acima_do_limite(self):
        self.assertEqual(self.enviar("a.csv", b"x" * (5 * 1024 * 1024 + 1)).status_code, 413)


class Pdf(Comum):
    def test_gera(self):
        r = CLIENTE.post("/impressao/pdf", json={"itens": [{**ITEM, "folhas": 2}, {**ITEM, "linha": 3, "chave": "JABUTICABA"}]})
        self.assertEqual((r.status_code, r.content[:4]), (200, b"%PDF"))
        self.assertIn("2chaves_3paginas", r.headers["content-disposition"])

    def test_gera_com_dia(self):
        r = CLIENTE.post("/impressao/pdf", json={"itens": [{**ITEM, "dia": "2026-11-11"}]})
        self.assertEqual((r.status_code, r.content[:4]), (200, b"%PDF"))
        for ruim in ("amanhã", "2026-13-01", 5):
            with self.subTest(dia=ruim):
                self.assertEqual(CLIENTE.post("/impressao/pdf", json={"itens": [{**ITEM, "dia": ruim}]}).status_code, 422)

    def test_alerta(self):
        itens = [{**ITEM, "chave": "BANANA"}, {**ITEM, "linha": 3, "chave": "BANANO"}]
        self.assertEqual(CLIENTE.post("/impressao/pdf", json={"itens": itens}).status_code, 409)
        self.assertEqual(CLIENTE.post("/impressao/pdf", json={"itens": itens, "aceitar_alertas": True}).status_code, 200)

    def test_entrada_ruim(self):
        for corpo in ({"itens": []}, {}, {"itens": None}, {"itens": [{}]}, {"itens": [{**ITEM, "chave": "SALA2"}]}, {"itens": [{**ITEM, "folhas": 0}]}, {"itens": [{**ITEM, "folhas": 10 ** 12}]}, {"itens": [ITEM, ITEM]}, {"itens": [{**ITEM, "x": 1}]}):
            with self.subTest(corpo=json.dumps(corpo)[:50]):
                self.assertEqual(CLIENTE.post("/impressao/pdf", json=corpo).status_code, 422)

    def test_cadastrar_sem_banco_explica(self):
        r = CLIENTE.post("/impressao/cadastrar", json={"itens": [ITEM]})
        self.assertEqual(r.status_code, 503)
        self.assertIn("Postgres", r.json()["detail"])

    def test_cadastrar_valida_antes_do_banco(self):
        self.assertEqual(CLIENTE.post("/impressao/cadastrar", json={"itens": [{**ITEM, "chave": "SALA2"}]}).status_code, 422)


@unittest.skipUnless((RAIZ / "web/dist/index.html").exists(), "cliente não compilado")
class UmaPortaSo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from api.servir import aplicacao
        cls.c = TestClient(aplicacao, raise_server_exceptions=False)

    def test_telas(self):
        for caminho in ("/", "/impressao", "/eventos", "/folhas/3"):
            with self.subTest(caminho=caminho):
                r = self.c.get(caminho)
                self.assertEqual(r.status_code, 200)
                self.assertIn('id="root"', r.text)

    def test_api_no_prefixo_e_com_login(self):
        self.assertEqual(self.c.get("/api/impressao/cabecalho").status_code, 401)
        self.assertEqual(self.c.get("/api/saude").status_code, 200)

    def test_nao_serve_arquivo_fora_do_cliente(self):
        for caminho in ("/../api/main.py", "/..%2f..%2fapi%2fmain.py", "/assets/../../../api/main.py", "/.env", "/vault/OlhoDigital.md"):
            with self.subTest(caminho=caminho):
                r = self.c.get(caminho)
                self.assertNotIn("FastAPI", r.text)
                self.assertNotIn("Vault", r.text)


if __name__ == "__main__":
    unittest.main()
