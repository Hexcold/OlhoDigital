"""D33: encerrar um evento ou um grupo libera as chaves para outro usar.

A chave passa a ser única só entre os eventos abertos. A folha com chave reaproveitada (que já foi
de um evento encerrado) não liga sozinha: pode ser uma atrasada do evento antigo.
"""
import unittest
from unittest import mock

from sqlalchemy import select

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline
from api.rotas import folhas
from api.tabelas import Evento, Folha

CORPO = {"tipo": "evento", "nome": "x", "local": "sala 1", "grupo": "TESTE"}   # D35


class Encerrar(ComBanco):
    def criar(self, chave, **extra):
        return self.cliente.post("/eventos", json={**CORPO, "chave": chave, **extra})

    def situacao(self, evento_id, situacao):
        return self.cliente.post(f"/eventos/{evento_id}/situacao",
                                 params={"situacao": situacao})

    # --- chave única só entre os abertos --------------------------------------------------
    def test_chave_de_evento_aberto_continua_recusada(self):
        self.assertEqual(self.criar("TESTEUM").status_code, 201)
        r = self.criar("TESTEUM")
        self.assertEqual(r.status_code, 409)
        self.assertIn("encerre", r.json()["detail"])

    def test_encerrar_libera_a_chave(self):
        antigo = self.criar("TESTEUM", grupo="EU").json()
        self.assertEqual(self.situacao(antigo["id"], "encerrado").status_code, 200)
        novo = self.criar("TESTEUM", grupo="EU2027")
        self.assertEqual(novo.status_code, 201, novo.text)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Evento, antigo["id"]).encerrado_em)

    def test_reabrir_com_a_chave_tomada_e_recusado(self):
        antigo = self.criar("TESTEUM").json()
        self.situacao(antigo["id"], "encerrado")
        self.criar("TESTEUM", nome="o novo")
        r = self.situacao(antigo["id"], "em andamento")
        self.assertEqual(r.status_code, 409)
        self.assertIn("o novo", r.json()["detail"])

    def test_editar_para_uma_chave_aberta_e_409_e_nao_500(self):
        self.criar("TESTEUM")
        outro = self.criar("TESTEDOIS").json()
        r = self.cliente.patch(f"/eventos/{outro['id']}", json={**CORPO, "chave": "TESTEUM"})
        self.assertEqual(r.status_code, 409, r.text)

    def test_cadastro_em_lote_cria_sobre_chave_encerrada(self):
        antigo = self.criar("TESTEUM", grupo="EU").json()
        self.situacao(antigo["id"], "encerrado")
        itens = [{"linha": 2, "chave": "TESTEUM", "folhas": 1}]
        r = self.cliente.post("/impressao/cadastrar", json={"itens": itens, "grupo": "EU2027"})
        self.assertEqual(r.json(), {"criados": 1, "ja_existiam": 0})
        abertos = list(self.db.scalars(select(Evento).where(Evento.chave == "TESTEUM",
                                                            Evento.situacao == "em andamento")))
        self.assertEqual([e.grupo for e in abertos], ["EU2027"])

    # --- o grupo inteiro ------------------------------------------------------------------
    def test_encerrar_e_reabrir_o_grupo(self):
        for c in ("TESTEUM", "TESTEDOIS"):
            self.criar(c, grupo="EU")
        r = self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado", "confirmacao": "EU"})
        self.assertEqual(r.json(), {"grupo": "EU", "situacao": "encerrado", "eventos": 2,
                                    "folhas_religadas": 0})
        g = next(x for x in self.cliente.get("/grupos").json() if x["nome"] == "EU")
        self.assertEqual((g["eventos"], g["eventos_abertos"]), (2, 0))
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "em andamento"})
        g = next(x for x in self.cliente.get("/grupos").json() if x["nome"] == "EU")
        self.assertEqual(g["eventos_abertos"], 2)

    def test_reabrir_o_grupo_e_tudo_ou_nada(self):
        for c in ("TESTEUM", "TESTEDOIS"):
            self.criar(c, grupo="EU")
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado", "confirmacao": "EU"})
        self.criar("TESTEDOIS", grupo="OUTRO", nome="o dono novo")
        r = self.cliente.post("/grupos/EU/situacao", params={"situacao": "em andamento"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("TESTEDOIS", r.json()["detail"])
        self.db.expire_all()
        eu = list(self.db.scalars(select(Evento).where(Evento.grupo == "EU")))
        self.assertEqual({e.situacao for e in eu}, {"encerrado"})   # nenhum reabriu

    def test_frequencia_continua_depois_de_encerrar(self):
        ev = self.evento("TESTEUM", "EU")
        self.folha(ev)
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado", "confirmacao": "EU"})
        self.assertEqual(self.cliente.get("/grupos/EU/frequencia.json").status_code, 200)

    # --- D34: encerrar sem querer ---------------------------------------------------------
    def test_encerrar_o_grupo_pede_o_nome(self):
        self.criar("TESTEUM", grupo="EU")
        for errada in (None, "", "eu", "OUTRO"):
            with self.subTest(confirmacao=errada):
                params = {"situacao": "encerrado"}
                if errada is not None:
                    params["confirmacao"] = errada
                r = self.cliente.post("/grupos/EU/situacao", params=params)
                self.assertEqual(r.status_code, 422)
                self.assertIn("digite o nome", r.json()["detail"])
        g = next(x for x in self.cliente.get("/grupos").json() if x["nome"] == "EU")
        self.assertEqual(g["eventos_abertos"], 1)                 # nada foi encerrado

    def test_reabrir_nao_pede_o_nome(self):
        self.criar("TESTEUM", grupo="EU")
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado",
                                                         "confirmacao": "EU"})
        r = self.cliente.post("/grupos/EU/situacao", params={"situacao": "em andamento"})
        self.assertEqual(r.status_code, 200, r.text)

    def folha_na_bandeja(self, chave_lida, origem=None):
        f = self.folha(estado="processada", nome=f"{chave_lida}-{origem}.pdf")
        f.chave_lida, f.origem_vinculo = chave_lida, origem
        f.linhas_preenchidas = 0
        self.db.commit()
        return f

    def test_reabrir_religa_o_que_caiu_na_bandeja(self):
        ev = self.evento("TESTEUM", "EU")
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado",
                                                         "confirmacao": "EU"})
        caiu = self.folha_na_bandeja("TESTEUM")                 # chegou durante o encerramento
        tirada = self.folha_na_bandeja("TESTEUM", "removido")   # alguém tirou de propósito
        outra = self.folha_na_bandeja("TESTEDOIS")              # chave de outro evento...
        self.evento("TESTEDOIS", "OUTRO")                       # ...que abriu depois
        r = self.cliente.post("/grupos/EU/situacao", params={"situacao": "em andamento"})
        self.assertEqual(r.json()["folhas_religadas"], 1)
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, caiu.id).evento_id, ev.id)
        self.assertEqual(self.db.get(Folha, caiu.id).origem_vinculo, "automatico")
        self.assertIsNone(self.db.get(Folha, tirada.id).evento_id)
        # reabrir o EU não mexe na bandeja de outro grupo
        self.assertIsNone(self.db.get(Folha, outra.id).evento_id)

    def test_reabrir_um_evento_tambem_religa(self):
        ev = self.evento("TESTEUM")
        self.situacao(ev.id, "encerrado")
        caiu = self.folha_na_bandeja("TESTEUM")
        self.situacao(ev.id, "em andamento")
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, caiu.id).evento_id, ev.id)

    def test_tirar_do_evento_a_mao_fica_marcado(self):
        ev = self.evento("TESTEUM")
        f = self.folha(ev)
        r = self.cliente.post(f"/folhas/{f.id}/evento", json={"evento_id": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, f.id).origem_vinculo, "removido")
        # e reler não religa por cima da decisão
        f = self.db.get(Folha, f.id)
        f.chave_lida = "TESTEUM"
        with mock.patch.object(pipeline, "ler_chave", return_value="TESTEUM"):
            folhas._vincular_evento(self.db, f)
        self.assertIsNone(f.evento_id)

    # --- folha com chave reaproveitada ----------------------------------------------------
    def vincular(self, chave_lida):
        f = self.folha(estado="processada", nome=f"{chave_lida}.pdf")
        with mock.patch.object(pipeline, "ler_chave", return_value=chave_lida):
            folhas._vincular_evento(self.db, f)
        self.db.commit()
        return f

    def test_chave_nova_liga_sozinha(self):
        ev = self.evento("TESTEUM")
        f = self.vincular("TESTEUM")
        self.assertEqual((f.evento_id, f.origem_vinculo), (ev.id, "automatico"))

    def test_chave_reaproveitada_pede_confirmacao(self):
        antigo = self.evento("TESTEUM", "EU")
        self.situacao(antigo.id, "encerrado")
        novo = self.criar("TESTEUM", nome="o novo").json()
        f = self.vincular("TESTEUM")
        self.assertIsNone(f.evento_id)                       # na bandeja
        s = self.cliente.get(f"/folhas/{f.id}/eventos-sugeridos").json()
        self.assertEqual(s["candidatos"][0]["id"], novo["id"])
        self.assertIn("já foi do evento", s["aviso"])
        self.assertIn("EU", s["aviso"])
        r = self.cliente.post(f"/folhas/{f.id}/evento", json={"evento_id": novo["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, f.id).origem_vinculo, "confirmado")


if __name__ == "__main__":
    unittest.main()
