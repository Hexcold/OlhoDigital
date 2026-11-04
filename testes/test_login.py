"""O sistema guarda nomes e matrículas: a porta é o que separa isso do link."""
import json
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from apoio import RAIZ, lf                                    # noqa: F401
from api import sessao, usuarios
from api.config import config
from api.estaticos import Aplicacao
from api.main import app
from api.sessao import COOKIE, ExigeLogin, criar_token, ler_token

SENHA = "senha-de-teste-123"

# Escritas aqui, e não importadas de `api.sessao`: um teste que lê a lista que vigia não
# percebe quando alguém a aumenta. Foi o que aconteceu: abrir /api/participantes passou
# por esta bateria até estas listas existirem.
ABERTAS = {("POST", "/api/login"), ("GET", "/api/saude")}
IMPRESSAO = {
    ("GET", "/api/eu"), ("POST", "/api/sair"),
    ("GET", "/api/impressao/cabecalho"), ("PUT", "/api/impressao/cabecalho"),
    ("POST", "/api/impressao/cabecalho/previa"), ("POST", "/api/impressao/folhas"),
    ("GET", "/api/impressao/planilha/modelo"), ("POST", "/api/impressao/planilha"),
    ("POST", "/api/impressao/pdf"),
}


def porta():
    pasta = Path(tempfile.mkdtemp())
    (pasta / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    return TestClient(ExigeLogin(Aplicacao(app, pasta, diretas=sessao.DOCUMENTACAO)),
                      raise_server_exceptions=False)


def entrar(c, nome):
    r = c.post("/api/login", json={"usuario": nome, "senha": SENHA})
    assert r.status_code == 200, r.text
    return r


class Senhas(unittest.TestCase):
    def setUp(self):
        config.arquivo_usuarios.unlink(missing_ok=True)

    def test_hash_confere_e_nao_repete(self):
        a, b = usuarios.gerar_hash(SENHA), usuarios.gerar_hash(SENHA)
        self.assertNotEqual(a, b)
        self.assertTrue(usuarios.conferir_hash(SENHA, a))
        self.assertFalse(usuarios.conferir_hash("outra", a))

    def test_hash_estragado_nao_quebra(self):
        for ruim in ("", "x", "scrypt$1$2", "scrypt$a$b$c$d$e", "scrypt$16384$8$1$@@$@@", 5, None):
            with self.subTest(ruim=ruim):
                self.assertFalse(usuarios.conferir_hash(SENHA, ruim))

    def test_senha_nunca_fica_em_texto_no_arquivo(self):
        usuarios.adicionar("chefe", SENHA, "admin")
        self.assertNotIn(SENHA, config.arquivo_usuarios.read_text(encoding="utf-8"))

    def test_so_o_dono_le_o_arquivo(self):
        usuarios.adicionar("chefe", SENHA, "admin")
        self.assertEqual(stat.S_IMODE(config.arquivo_usuarios.stat().st_mode), 0o600)

    def test_recusas_ao_criar(self):
        for nome, senha, papel in (("A B", SENHA, "admin"), ("chefe", "curta", "admin"), ("chefe", SENHA, "dono"),
                                   ("", SENHA, "admin"), ("../x", SENHA, "admin"), ("MAIUSCULA", SENHA, "admin")):
            with self.subTest(nome=nome, papel=papel):
                with self.assertRaises(ValueError):
                    usuarios.adicionar(nome, senha, papel)

    def test_arquivo_quebrado_ninguem_entra(self):
        for conteudo in ("{quebrado", "[]", '{"chefe": 1}', '{"chefe": {"papel": "dono"}}', '{"chefe": {"senha": 5, "papel": "admin"}}'):
            with self.subTest(conteudo=conteudo):
                config.arquivo_usuarios.parent.mkdir(parents=True, exist_ok=True)
                config.arquivo_usuarios.write_text(conteudo, encoding="utf-8")
                self.assertIsNone(usuarios.conferir("chefe", SENHA))


class Token(unittest.TestCase):
    def test_ida_e_volta(self):
        self.assertEqual(ler_token(criar_token("chefe")), "chefe")

    def test_sobrevive_ao_cookie_sem_aspas(self):
        """Um `=` no valor faz o cookie voltar entre aspas e a sessão nunca abrir."""
        self.assertRegex(criar_token("a.b-c_d"), r"^[0-9a-f]+\.[0-9a-f]+$")

    def test_vence(self):
        self.assertIsNone(ler_token(criar_token("chefe", agora=time.time() - sessao.DURACAO_S - 5)))

    def test_adulterado(self):
        t = criar_token("grafica")
        carga, _, assinatura = t.partition(".")
        falso = f"chefe|{int(time.time()) + 999}".encode().hex()
        for ruim in (f"{falso}.{assinatura}", f"{carga}.{'0' * 64}", f"{carga}.", "", ".", "x.y", t[:-1], "ç.ç"):
            with self.subTest(ruim=ruim[:20]):
                self.assertIsNone(ler_token(ruim))

    def test_outro_segredo_nao_vale(self):
        t = criar_token("chefe")
        original = config.segredo_sessao
        config.segredo_sessao = "outro-segredo"
        try:
            self.assertIsNone(ler_token(t))
        finally:
            config.segredo_sessao = original


def rotas_da_api():
    """Todas as rotas que existem, tiradas da própria API: rota nova entra sozinha."""
    saida = []
    for caminho, metodos in app.openapi()["paths"].items():
        for metodo in metodos:
            exemplo = caminho.replace("{folha_id}", "1").replace("{linha_id}", "1").replace("{evento_id}", "1")
            saida.append((metodo.upper(), "/api" + exemplo))
    return saida


class Porta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.arquivo_usuarios.unlink(missing_ok=True)
        usuarios.adicionar("chefe", SENHA, "admin")
        usuarios.adicionar("grafica", SENHA, "impressao")

    def setUp(self):
        sessao.limitador._falhas.clear()
        self.c = porta()

    def test_inventario_cobre_a_api(self):
        rotas = rotas_da_api()
        self.assertGreater(len(rotas), 30)
        for esperada in ("/api/participantes", "/api/folhas/1/imagem", "/api/impressao/pdf"):
            self.assertIn(esperada, [c for _, c in rotas])

    def test_as_listas_do_codigo_sao_as_combinadas(self):
        self.assertEqual(sessao.ROTAS_ABERTAS, ABERTAS)
        self.assertEqual(sessao.ROTAS_IMPRESSAO, IMPRESSAO)

    def test_sem_login_nao_sai_dado_de_aluno(self):
        for caminho in ("/api/participantes", "/api/participantes?q=a", "/api/folhas", "/api/folhas/1/linhas",
                        "/api/folhas/1/imagem", "/api/linhas/1/recorte", "/api/eventos"):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.c.get(caminho).status_code, 401)

    def test_sem_login_toda_rota_da_api_e_recusada(self):
        for metodo, caminho in rotas_da_api():
            if (metodo, caminho) in ABERTAS:
                continue
            with self.subTest(rota=f"{metodo} {caminho}"):
                self.assertEqual(self.c.request(metodo, caminho).status_code, 401)

    def test_conta_de_impressao_so_alcanca_a_lista(self):
        entrar(self.c, "grafica")
        liberadas = IMPRESSAO | ABERTAS
        recusadas = 0
        for metodo, caminho in rotas_da_api():
            if (metodo, caminho) in liberadas:
                continue
            with self.subTest(rota=f"{metodo} {caminho}"):
                self.assertEqual(self.c.request(metodo, caminho).status_code, 403)
                recusadas += 1
        self.assertGreater(recusadas, 20)

    def test_lista_de_impressao_nao_tem_rota_de_dados(self):
        for _, caminho in sessao.ROTAS_IMPRESSAO:
            if caminho in ("/api/eu", "/api/sair"):
                continue
            self.assertTrue(caminho.startswith("/api/impressao/"), caminho)
            self.assertNotIn("cadastrar", caminho)

    def test_conta_de_impressao_gera_folhas(self):
        entrar(self.c, "grafica")
        r = self.c.post("/api/impressao/folhas", json={"chave": "GOIABA", "folhas": 1})
        self.assertEqual((r.status_code, r.content[:4]), (200, b"%PDF"))
        r = self.c.get("/api/impressao/planilha/modelo")
        r = self.c.post("/api/impressao/planilha", files={"arquivo": ("m.xlsx", r.content)})
        self.assertEqual(r.json()["total_chaves"], 3)

    def test_conta_de_impressao_nao_cadastra(self):
        entrar(self.c, "grafica")
        self.assertEqual(self.c.post("/api/impressao/cadastrar", json={"itens": []}).status_code, 403)

    def test_admin_alcanca_tudo(self):
        entrar(self.c, "chefe")
        for metodo, caminho in (("GET", "/api/impressao/cabecalho"), ("GET", "/api/modelos-folha"),
                                ("POST", "/api/impressao/cadastrar")):
            with self.subTest(caminho=caminho):
                self.assertNotIn(self.c.request(metodo, caminho, json={"itens": []}).status_code, (401, 403))

    def test_documentacao_so_para_admin(self):
        for caminho in sessao.DOCUMENTACAO:
            with self.subTest(caminho=caminho):
                self.assertEqual(self.c.get(caminho).status_code, 401)
        entrar(self.c, "grafica")
        self.assertEqual(self.c.get("/openapi.json").status_code, 403)
        entrar(self.c, "chefe")
        self.assertEqual(self.c.get("/openapi.json").status_code, 200)

    def test_truques_de_caminho(self):
        entrar(self.c, "grafica")
        for caminho in ("/api//participantes", "/api/impressao/../participantes", "/api/impressao/%2e%2e/participantes",
                        "/api/impressao/..%2fparticipantes", "/api/./participantes", "/api/api/participantes",
                        "/api/participantes/", "/API/participantes", "/api/impressao/cabecalho/../../participantes"):
            with self.subTest(caminho=caminho):
                r = self.c.get(caminho, follow_redirects=False)
                self.assertNotIn("matricula", r.text)
                self.assertFalse(r.status_code == 200 and "json" in r.headers.get("content-type", ""), r.text[:80])

    def test_cliente_e_aberto_e_nao_tem_dado(self):
        for caminho in ("/", "/login", "/impressao", "/folhas/3"):
            with self.subTest(caminho=caminho):
                r = self.c.get(caminho)
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/html", r.headers["content-type"])

    def test_saude_nao_revela_dado(self):
        r = self.c.get("/api/saude")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"api", "banco", "pipeline"})

    def test_cookie_e_protegido(self):
        cookie = entrar(self.c, "grafica").headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("secure", cookie, "em http puro o cookie não voltaria")
        r = self.c.post("/api/login", json={"usuario": "grafica", "senha": SENHA}, headers={"X-Forwarded-Proto": "https"})
        self.assertIn("secure", r.headers["set-cookie"].lower())

    def test_mensagem_nao_diz_se_a_conta_existe(self):
        a = self.c.post("/api/login", json={"usuario": "chefe", "senha": "errada-mesmo"})
        b = self.c.post("/api/login", json={"usuario": "ninguem", "senha": "errada-mesmo"})
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        self.assertEqual(a.status_code, 401)

    def test_nome_em_maiuscula_e_com_espaco_entra(self):
        self.assertEqual(self.c.post("/api/login", json={"usuario": " Chefe ", "senha": SENHA}).status_code, 200)

    def test_entrada_ruim_no_login(self):
        for corpo in ({}, {"usuario": "chefe"}, {"senha": SENHA}, {"usuario": 1, "senha": 2}, {"usuario": "chefe", "senha": SENHA, "papel": "admin"}, []):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.c.post("/api/login", json=corpo).status_code, 422)

    def test_senha_errada_demais_bloqueia_mesmo_com_a_certa(self):
        for _ in range(sessao.Limitador.MAXIMO):
            self.c.post("/api/login", json={"usuario": "grafica", "senha": "errada-mesmo"})
        self.assertEqual(self.c.post("/api/login", json={"usuario": "grafica", "senha": SENHA}).status_code, 429)
        self.assertEqual(self.c.post("/api/login", json={"usuario": "chefe", "senha": SENHA}).status_code, 200, "só aquela conta")

    def test_acerto_zera_as_falhas(self):
        for _ in range(sessao.Limitador.MAXIMO - 1):
            self.c.post("/api/login", json={"usuario": "chefe", "senha": "errada-mesmo"})
        entrar(self.c, "chefe")
        self.assertFalse(sessao.limitador.bloqueado("chefe"))

    def test_sair(self):
        entrar(self.c, "grafica")
        self.assertEqual(self.c.get("/api/eu").json()["usuario"], "grafica")
        self.assertEqual(self.c.post("/api/sair").status_code, 204)
        self.c.cookies.clear()
        self.assertEqual(self.c.get("/api/eu").status_code, 401)

    def test_remover_a_conta_derruba_a_sessao(self):
        usuarios.adicionar("temporaria", SENHA, "admin")
        entrar(self.c, "temporaria")
        self.assertEqual(self.c.get("/api/eu").status_code, 200)
        usuarios.remover("temporaria")
        self.assertEqual(self.c.get("/api/eu").status_code, 401)

    def test_trocar_o_papel_vale_na_hora(self):
        usuarios.adicionar("promovida", SENHA, "admin")
        entrar(self.c, "promovida")
        self.assertEqual(self.c.get("/api/modelos-folha").status_code, 200)
        usuarios.adicionar("promovida", SENHA, "impressao")
        self.assertEqual(self.c.get("/api/modelos-folha").status_code, 403)
        usuarios.remover("promovida")

    def test_cookie_falso_nao_entra(self):
        self.c.cookies.set(COOKIE, "abc.def")
        self.assertEqual(self.c.get("/api/eu").status_code, 401)

    def test_eu_nao_expoe_outras_contas(self):
        entrar(self.c, "chefe")
        self.assertNotIn("grafica", json.dumps(self.c.get("/api/eu").json()))


class SoAVersaoComLoginSai(unittest.TestCase):
    def test_versao_compilada_tem_a_porta(self):
        self.assertIn("ExigeLogin(Aplicacao(", (RAIZ / "api/servir.py").read_text(encoding="utf-8"))

    def test_script_usa_a_versao_com_login(self):
        script = (RAIZ / "compartilhar.sh").read_text(encoding="utf-8")
        self.assertIn("api.servir:aplicacao", script)
        self.assertNotIn("api.main:app", script)
        self.assertEqual(subprocess.run(["bash", "-n", str(RAIZ / "compartilhar.sh")]).returncode, 0)

    def test_script_nao_abre_o_link_sem_ver_este_sistema_na_porta(self):
        """Porta ocupada por outro programa publicaria o que estivesse nela."""
        script = (RAIZ / "compartilhar.sh").read_text(encoding="utf-8")
        self.assertLess(script.index("/api/saude"), script.index("cloudflared tunnel"))
        self.assertLess(script.index('[ -z "$PRONTO" ]'), script.index("cloudflared tunnel"))

    def test_docker_usa_a_versao_com_login(self):
        self.assertIn('"api.servir:aplicacao"', (RAIZ / "Dockerfile").read_text(encoding="utf-8"))

    def test_contas_ficam_fora_do_git(self):
        r = subprocess.run(["git", "check-ignore", "-q", "experimento-imagem/dados/usuarios.json"], cwd=RAIZ)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
