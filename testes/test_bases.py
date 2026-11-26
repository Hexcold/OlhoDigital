"""As bases de pessoas: a geral e a de cada grupo (pedido do autor em 22/11/2026).

Decisões testadas: a identificação é contra a geral e a lista do grupo filtra quem sai aceito
(A66); sem lista, qualquer um da geral; trocar avisa e
oferece reler, e reler com decisão humana pede confirmação; apagar tira de uso mas guarda a
base enquanto alguma folha a usa; toda base nova tem prévia. Só pessoas inventadas.
"""
import io
import sys
import types
import unittest
from datetime import date
from unittest import mock

from openpyxl import Workbook, load_workbook

from test_grupos import ComBanco, PESSOAS                      # noqa: F401  isola o ambiente
from test_fila import RECURSOS

from api import bases, pipeline
from api.rotas import folhas
from api.tabelas import BaseDePessoas, Folha, PessoaDaBase

CSV_SIGAA = ("Matrícula,Nome,Curso,Status\n"
             "900001,Zuleica Amarante Frota,CURSO X,ATIVO\n"
             "900002,Quitéria Brandão Viegas,CURSO X,FORMANDO\n"
             "700001,Ondina Paixão Leal,CURSO Y,GRADUANDO\n"
             "700002,Teodoro Saraiva Cunha,CURSO Y,TRANCADO\n"
             "700001,Ondina Paixão Leal,CURSO Y,ATIVO\n").encode()


def xlsx(linhas, aba="21.08.2026"):
    wb = Workbook()
    ws = wb.active
    ws.title = aba
    for linha in linhas:
        ws.append(linha)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


class LerArquivo(unittest.TestCase):
    def test_csv_do_sigaa(self):
        lida = bases.ler_arquivo("students.csv", CSV_SIGAA)
        # FORMANDO e GRADUANDO contam (D25); TRANCADO fica de fora; a repetida sai uma vez
        self.assertEqual([p["matricula"] for p in lida.pessoas], ["900001", "900002", "700001"])
        self.assertEqual(lida.fora_por_status, {"TRANCADO": 1})
        self.assertEqual(lida.repetidas, 1)
        self.assertEqual(lida.pessoas[1]["nome"], "Quitéria Brandão Viegas")

    def test_csv_com_ponto_e_virgula_em_latin1(self):
        conteudo = "MATRICULA;NOME\n12345;Ãngelo Teste\n".encode("latin-1")
        lida = bases.ler_arquivo("lista.csv", conteudo)
        self.assertEqual(lida.pessoas, [{"matricula": "12345", "nome": "Ãngelo Teste",
                                         "curso": None}])   # 5 dígitos fica como está

    def test_xlsx_com_matricula_float_e_data_na_aba(self):
        lida = bases.ler_arquivo("base.xlsx", xlsx([("Matrícula", "Nome", "Curso"),
                                                     (559707.0, "Fulano de Tal", "CURSO Z")]))
        self.assertEqual(lida.pessoas[0]["matricula"], "559707")
        self.assertEqual(str(lida.data_aba), "2026-08-21")

    def test_recusa_sem_as_colunas_e_formato_estranho(self):
        with self.assertRaisesRegex(ValueError, "Matrícula e Nome"):
            bases.ler_arquivo("x.csv", b"codigo,pessoa\n1,a\n")
        with self.assertRaisesRegex(ValueError, ".xlsx ou um .csv"):
            bases.ler_arquivo("x.pdf", b"%PDF")
        with self.assertRaisesRegex(ValueError, "nenhuma pessoa"):
            bases.ler_arquivo("x.csv", b"Matricula,Nome\n,\n")


def linha_lida(numero, sugerida, decisao="aceito"):
    """Uma linha como o pipeline devolve, com dois candidatos."""
    return {"numero": numero, "leitura_matricula": sugerida, "leitura_nome": "X",
            "confiancas_matricula": [], "confiancas_nome": [], "motivos_matricula": ["lido"] * 6,
            "motivos_nome": ["lido"], "score": 0.9, "margem": 0.3, "decisao": decisao,
            "matricula_sugerida": sugerida, "duplicada_na_folha": False,
            "candidatos": [{"matricula": sugerida, "nome": "A", "score": 0.9},
                           {"matricula": "900001", "nome": "B", "score": 0.6}]}


class Bases(ComBanco):
    def setUp(self):
        super().setUp()
        self.ev_a = self.evento("GELEIA", "A")
        self.ev_b = self.evento("PRATO", "B")

    def enviar(self, conteudo=CSV_SIGAA, grupo=None, nome="students.csv", rota="/bases",
               data=None):
        dados = {k: v for k, v in (("grupo", grupo), ("data", data)) if v}
        return self.cliente.post(rota, files={"arquivo": (nome, conteudo)}, data=dados)

    def ler(self, folha, linhas=()):
        """Lê a folha com o pipeline trocado por um dublê que guarda a base que recebeu e
        devolve as `linhas` dadas, como o pipeline devolveria."""
        recebida = []

        def processar(caminho, modelo, base):
            recebida.append(sorted(p["matricula"] for p in base))
            return [dict(l) for l in linhas]

        with mock.patch.object(pipeline, "conferir_marcadores", return_value=4), \
                mock.patch.object(pipeline, "carregar_recursos", return_value=RECURSOS), \
                mock.patch.object(pipeline, "processar", processar), \
                mock.patch.object(pipeline, "recortar_para_revisao"), \
                mock.patch.object(folhas, "_vincular_evento"):
            folhas._ler_e_gravar(folha.id)
        self.db.expire_all()
        return recebida[0] if recebida else None

    # --- prévia ----------------------------------------------------------------------------
    def test_previa_compara_com_a_base_atual_e_nao_grava(self):
        r = self.enviar(rota="/bases/previa")
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()
        # a atual tem 900001, 900002, 912345; a nova, 900001, 900002, 700001
        self.assertEqual((p["pessoas"], p["entram"], p["saem"], p["mudam"]), (3, 1, 1, 2))
        self.assertEqual(p["fora_por_status"], {"TRANCADO": 1})
        self.assertTrue(any("saem 1 das 3" in a for a in p["alertas"]))
        self.assertEqual(self.db.query(BaseDePessoas).count(), 1)     # nada gravado

    def test_previa_avisa_lista_mais_velha_e_mesmo_arquivo(self):
        self.base_geral.data_referencia = __import__("datetime").date(2026, 8, 21)
        self.base_geral.sha256 = bases._sha(CSV_SIGAA)
        self.db.commit()
        alertas = self.enviar(rota="/bases/previa", data="2026-04-01").json()["alertas"]
        self.assertTrue(any("mais velha" in a for a in alertas), alertas)
        self.assertTrue(any("mesmo arquivo" in a for a in alertas), alertas)

    # --- aplicar e qual base vale ------------------------------------------------------------
    def test_com_lista_a_leitura_usa_a_geral_mais_quem_so_esta_na_lista(self):
        # A66: a lista não substitui a geral; a identificação tem os concorrentes do campus, e
        # o convidado que só está na lista (700001) também entra
        f_a = self.folha(self.ev_a, estado="recebida", nome="a.pdf")
        f_b = self.folha(self.ev_b, estado="recebida", nome="b.pdf")
        r = self.enviar(grupo="A")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["propria"]["pessoas"], 3)
        self.assertEqual(self.ler(f_a), sorted(set(PESSOAS) | {"700001"}))
        a, b_ = self.db.get(Folha, f_a.id), self.db.get(Folha, f_b.id)
        self.assertEqual(self.ler(f_b), sorted(PESSOAS))                  # B: só a geral
        self.assertEqual(a.base_id, self.base_geral.id)
        self.assertEqual(a.lista_id, bases.ativa(self.db, "A").id)
        self.assertEqual((b_.base_id, b_.lista_id), (self.base_geral.id, None))

    def test_aceite_fora_da_lista_vai_para_a_quarentena(self):
        self.enviar(grupo="A")                       # lista: 900001, 900002, 700001
        f = self.folha(self.ev_a, estado="recebida")
        self.ler(f, linhas=[linha_lida(1, "900001"), linha_lida(2, "912345")])
        linhas = {l["numero"]: l for l in self.cliente.get(f"/folhas/{f.id}/linhas").json()}
        self.assertEqual(linhas[1]["decisao"], "aceito")
        self.assertEqual((linhas[2]["decisao"], linhas[2]["fora_da_lista"]), ("quarentena", True))
        self.assertIn("não está na lista", linhas[2]["motivo_rejeicao"])
        # os candidatos dizem quem está na lista
        self.assertEqual([(c["matricula"], c["na_lista"]) for c in linhas[2]["candidatos"]],
                         [("912345", False), ("900001", True)])

    def test_sem_base_geral_a_lista_e_a_base(self):
        self.enviar(grupo="A")
        self.base_geral.ativa = False
        self.db.commit()
        f = self.folha(self.ev_a, estado="recebida")
        self.assertEqual(self.ler(f), ["700001", "900001", "900002"])
        f = self.db.get(Folha, f.id)
        self.assertEqual((f.base_id, f.lista_id), (bases.ativa(self.db, "A").id, None))

    def test_sem_base_nenhuma_a_folha_falha_com_aviso(self):
        self.base_geral.ativa = False
        self.db.commit()
        f = self.folha(self.ev_a, estado="recebida")
        self.assertIsNone(self.ler(f))
        f = self.db.get(Folha, f.id)
        self.assertEqual(f.estado, "erro")
        self.assertIn("nenhuma base de pessoas", f.erro)

    def test_substituir_apaga_a_que_ninguem_usou_e_guarda_a_usada(self):
        f = self.folha(self.ev_b, estado="recebida")
        self.ler(f)                                    # lida com a geral semeada
        geral_id = self.base_geral.id
        self.enviar()                                   # a geral muda
        self.db.expire_all()
        antiga = self.db.get(BaseDePessoas, geral_id)
        self.assertFalse(antiga.ativa)                  # guardada: uma folha a usou
        self.assertEqual(self.db.query(PessoaDaBase).filter_by(base_id=geral_id).count(), 3)
        nova_id = bases.ativa(self.db, None).id
        self.enviar(nome="outra.csv", conteudo=CSV_SIGAA + b"700003,Nova Pessoa,C,ATIVO\n")
        self.db.expire_all()
        self.assertIsNone(self.db.get(BaseDePessoas, nova_id))   # ninguém usou: saiu de vez
        self.assertEqual(self.db.query(PessoaDaBase).filter_by(base_id=nova_id).count(), 0)

    def test_base_guardada_sai_quando_a_ultima_folha_e_relida(self):
        f = self.folha(self.ev_a, estado="recebida")
        base_a = self.enviar(grupo="A").json()["propria"]["id"]
        self.ler(f)
        self.assertEqual(self.cliente.delete(f"/bases/{base_a}").json()["resultado"], "guardada")
        f = self.db.get(Folha, f.id)
        f.estado = "recebida"
        self.db.commit()
        self.ler(f)                                     # relida com a geral
        self.assertIsNone(self.db.get(BaseDePessoas, base_a))
        self.assertEqual(self.db.query(PessoaDaBase).filter_by(base_id=base_a).count(), 0)

    def test_apagar_a_do_grupo_volta_para_a_geral(self):
        base_id = self.enviar(grupo="A").json()["propria"]["id"]
        r = self.cliente.delete(f"/bases/{base_id}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resultado"], "apagada")
        self.assertIsNone(r.json()["propria"])
        self.assertEqual(bases.efetiva(self.db, "A").id, self.base_geral.id)

    def test_quem_enviou_fica_registrado(self):
        from test_login import SENHA, entrar, porta
        from api import sessao, usuarios
        from api.config import config
        config.arquivo_usuarios.unlink(missing_ok=True)
        usuarios.adicionar("chefe", SENHA, "admin")
        sessao.limitador._falhas.clear()
        c = porta()
        entrar(c, "chefe")
        r = c.post("/api/bases", files={"arquivo": ("students.csv", CSV_SIGAA)},
                   data={"grupo": "A"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["propria"]["enviada_por"], "chefe")

    # --- a revisão e a frequência usam a base da folha ---------------------------------------
    def test_revisao_pode_escolher_fora_da_lista_e_a_frequencia_diz(self):
        self.enviar(grupo="A")
        f = self.folha(self.ev_a, estado="recebida")
        self.ler(f)
        folha = self.db.get(Folha, f.id)
        fora = self.linha(folha, 1, "quarentena", "900001")
        convidado = self.linha(folha, 2, "quarentena", "900001")
        # 912345 está na geral e não na lista: a decisão humana manda (D03)
        r = self.cliente.post(f"/linhas/{fora.id}/revisao", json={"matricula_escolhida": "912345"})
        self.assertEqual(r.status_code, 201, r.text)
        # 700001 só está na lista (um convidado de fora do campus)
        r = self.cliente.post(f"/linhas/{convidado.id}/revisao",
                              json={"matricula_escolhida": "700001"})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.cliente.post(f"/linhas/{fora.id}/revisao", json={"matricula_escolhida": "999999"})
        self.assertEqual(r.status_code, 422, r.text)              # em base nenhuma
        freq = {l["linha"]: l for l in self.cliente.get("/grupos/A/frequencia.json")
                .json()["frequencia"]}
        self.assertEqual(freq[1]["situacao"], "presente (fora da lista)")
        self.assertEqual((freq[2]["situacao"], freq[2]["nome"]),
                         ("presente (revisado)", "Ondina Paixão Leal"))

    def test_motivo_do_nome_que_nao_confirma_chega_a_tela(self):
        # D37: a marca vem da leitura, é gravada na linha e vira o motivo da quarentena
        f = self.folha(self.ev_a, estado="recebida")
        self.ler(f, [{**linha_lida(1, "900001", "quarentena"), "nome_nao_confirma": True},
                     linha_lida(2, "900002")])
        um, dois = self.cliente.get(f"/folhas/{f.id}/linhas").json()
        self.assertTrue(um["nome_nao_confirma"])
        self.assertIn("o nome lido não confirma", um["motivo_rejeicao"])
        self.assertFalse(dois["nome_nao_confirma"])

    def test_baixar_e_reenviar_da_a_mesma_lista(self):
        self.enviar(grupo="A")
        lista = bases.ativa(self.db, "A")
        lista.data_referencia = date(2026, 8, 21)
        self.db.get(PessoaDaBase, (lista.id, "700001")).nome = "=1+1"   # nunca vira fórmula
        self.db.commit()
        r = self.cliente.get(f"/bases/{lista.id}/planilha.xlsx")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("lista_A_2026-08-21.xlsx", r.headers["content-disposition"])
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(load_workbook(io.BytesIO(r.content)).active["B2"].data_type, "s")
        volta = bases.ler_arquivo("baixada.xlsx", r.content)
        self.assertEqual(volta.data_aba, date(2026, 8, 21))
        no_banco = {(p.matricula, p.nome, p.curso) for p in self.db.query(PessoaDaBase)
                    .filter_by(base_id=lista.id)}
        self.assertEqual({(p["matricula"], p["nome"], p["curso"]) for p in volta.pessoas},
                         no_banco)
        self.assertEqual(len(volta.pessoas), 3)
        # sem data, a aba tem outro nome e ainda assim volta
        lista.data_referencia = None
        self.db.commit()
        r = self.cliente.get(f"/bases/{lista.id}/planilha.xlsx")
        self.assertIsNone(bases.ler_arquivo("baixada.xlsx", r.content).data_aba)
        # fora de uso ou inexistente não se baixa; a conta de impressão nem chega aqui
        self.assertEqual(self.cliente.get("/bases/99999/planilha.xlsx").status_code, 404)
        lista.ativa = False
        self.db.commit()
        self.assertEqual(self.cliente.get(f"/bases/{lista.id}/planilha.xlsx").status_code, 404)

    def test_conta_de_impressao_nao_baixa(self):
        from test_login import SENHA, entrar, porta
        from api import sessao, usuarios
        from api.config import config
        config.arquivo_usuarios.unlink(missing_ok=True)
        usuarios.adicionar("grafica", SENHA, "impressao")
        sessao.limitador._falhas.clear()
        c = porta()
        url = f"/api/bases/{self.base_geral.id}/planilha.xlsx"
        self.assertEqual(c.get(url).status_code, 401)
        entrar(c, "grafica")
        self.assertEqual(c.get(url).status_code, 403)

    def test_busca_pela_linha_e_por_matricula(self):
        self.enviar(grupo="A")
        f = self.folha(self.ev_a, estado="recebida")
        self.ler(f)
        linha = self.linha(self.db.get(Folha, f.id), 1, "quarentena", "900001")
        achados = self.cliente.get("/participantes", params={"q": "ondina",
                                                            "linha": linha.id}).json()
        self.assertEqual([(p["matricula"], p["na_lista"]) for p in achados], [("700001", True)])
        achados = self.cliente.get("/participantes", params={"q": "912",
                                                            "linha": linha.id}).json()
        self.assertEqual([(p["matricula"], p["na_lista"]) for p in achados], [("912345", False)])
        self.assertEqual(self.cliente.get("/participantes", params={
            "q": "ondina"}).json(), [])                              # a geral não tem
        # A63: buscar só dígitos casava com a base inteira
        achados = self.cliente.get("/participantes", params={"q": "9000"}).json()
        self.assertEqual(sorted(p["matricula"] for p in achados), ["900001", "900002"])

    def test_frequencia_guarda_o_nome_depois_de_trocar_a_base(self):
        f = self.folha(self.ev_b, estado="recebida")
        self.ler(f)
        self.linha(self.db.get(Folha, f.id), 1, "aceito", "912345")
        self.enviar()                       # a geral nova não tem 912345
        linhas = self.cliente.get("/grupos/B/frequencia.json").json()["frequencia"]
        self.assertEqual([(l["matricula"], l["nome"]) for l in linhas],
                         [("912345", PESSOAS["912345"])])

    # --- avisar e oferecer reler ---------------------------------------------------------------
    def test_trocar_a_base_avisa_e_reler_pede_confirmacao(self):
        f = self.folha(self.ev_a, estado="recebida")
        self.ler(f)
        linha = self.linha(self.db.get(Folha, f.id), 1, "quarentena", "900001")
        self.revisar(linha, "900001")
        situacao = self.enviar(grupo="A").json()
        self.assertEqual((situacao["desatualizadas"], situacao["decisoes_humanas"]), (1, 1))
        self.assertEqual(self.cliente.get("/bases", params={"grupo": "B"}).json()
                         ["desatualizadas"], 0)
        with mock.patch.object(folhas, "_ler_e_gravar"):
            r = self.cliente.post("/bases/reler", params={"grupo": "A"})
            self.assertEqual(r.status_code, 409, r.text)
            r = self.cliente.post("/bases/reler", params={"grupo": "A", "forcar": "true"})
        self.assertEqual(r.json()["relidas"], 1)
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, f.id).estado, "recebida")    # de volta à fila

    def test_copia_tambem_e_relida(self):
        # 22/11: relendo só a que valia, a cópia antiga voltava a valer (60 dos 62 papéis)
        vale = self.folha(self.ev_a, nome="p1.pdf")
        copia = self.folha(self.ev_a, nome="p2.pdf")
        copia.copia_de = vale.id
        vale.base_id = copia.base_id = self.base_geral.id
        self.db.commit()
        self.enviar(grupo="A")
        self.assertEqual({f.id for f in bases.desatualizadas(self.db, "A")}, {vale.id, copia.id})

    def test_na_copia_vale_a_lida_com_a_base_de_hoje(self):
        from api import reenvio
        antiga = self.folha(self.ev_a, nome="p1.pdf")
        nova = self.folha(self.ev_a, nome="p2.pdf")
        for n in (1, 2, 3):
            self.linha(antiga, n, "aceito", "900001")          # a antiga aceita mais
        for n in (1, 2, 3):
            self.linha(nova, n, "quarentena" if n > 1 else "aceito", "900001")
        self.enviar(grupo="A")
        antiga.base_id = nova.base_id = self.base_geral.id
        nova.lista_id = bases.ativa(self.db, "A").id          # só a nova leu com a lista de hoje
        self.db.commit()
        with mock.patch.object(reenvio, "semelhanca", return_value=1.0):
            reenvio.detectar(self.db, nova)
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.db.get(Folha, nova.id).copia_de)
        self.assertEqual(self.db.get(Folha, antiga.id).copia_de, nova.id)

    def test_grupo_apagado_leva_a_base_que_ninguem_usou(self):
        self.cliente.post("/grupos", json={"nome": "VAZIO"})
        self.enviar(grupo="VAZIO")
        self.assertEqual(self.cliente.delete("/grupos/VAZIO").status_code, 204)
        self.assertEqual(self.db.query(BaseDePessoas).filter_by(grupo="VAZIO").count(), 0)

    def test_grupo_cuja_lista_foi_usada_nao_se_apaga(self):
        # A folha guarda a lista em `lista_id` e a geral em `base_id` (A66): conferir só o
        # segundo deixava apagar o grupo com a lista em uso, e no Postgres a chave estrangeira
        # `base_pessoas.grupo` transformava isso em erro 500
        self.cliente.post("/grupos", json={"nome": "USADO"})
        self.enviar(grupo="USADO")
        f = self.folha(self.ev_b)
        f.base_id, f.lista_id = self.base_geral.id, bases.ativa(self.db, "USADO").id
        self.db.commit()
        r = self.cliente.delete("/grupos/USADO")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIsNotNone(bases.ativa(self.db, "USADO"))

    # --- a semente -------------------------------------------------------------------------
    def test_semente_cria_a_geral_e_aponta_as_folhas_antigas(self):
        f = self.folha(self.ev_a, estado="processada")
        self.db.query(PessoaDaBase).delete()
        self.db.query(BaseDePessoas).delete()
        self.db.commit()
        planilha = types.SimpleNamespace(
            carregar_base=mock.Mock(return_value=[{"matricula": "800001", "nome": "FULÂNIA",
                                                   "curso": None}]),
            escolher_aba=mock.Mock(return_value="21.08.2026"),
            _data_da_aba=mock.Mock(return_value=None))
        ident = types.SimpleNamespace(BASE_XLSX=__file__)      # só para haver bytes a ler
        with mock.patch.dict(sys.modules, {"base_alunos": planilha, "identificar_folha": ident}), \
                mock.patch("api.pipeline.preparar"), mock.patch.object(bases, "load_workbook"):
            base = bases.semear(self.db)
            self.assertIsNone(bases.semear(self.db))           # a segunda subida não mexe
        self.db.expire_all()
        self.assertEqual((base.origem, base.pessoas, base.grupo), ("semente", 1, None))
        self.assertEqual(self.db.get(Folha, f.id).base_id, base.id)


if __name__ == "__main__":
    unittest.main()
