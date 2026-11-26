"""D35, fases 4 e 5: sugerir chaves livres e seguras; o envio pelo grupo casa só com ele.

Dados inventados. A leitura da chave é trocada por um dublê: o objeto é com quem ela casa.
"""
import unittest
from itertools import combinations
from unittest import mock

from rapidfuzz.distance import Levenshtein

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline
from api.rotas import folhas
from api.tabelas import Folha


class Sugerir(ComBanco):
    def test_sugestoes_seguras_livres_e_distantes(self):
        self.evento("BALEIA", "EU")
        # aberta a 1 de TEMPERO, que sem a regra sairia entre as primeiras (86% letras seguras)
        self.evento("TEMPEROS", "OUTRO")
        fechado = self.evento("MESTRE", "EU")                 # usada por evento encerrado
        self.cliente.post(f"/eventos/{fechado.id}/situacao", params={"situacao": "encerrado"})
        r = self.cliente.get("/grupos/EU/chaves-sugeridas", params={"n": 12})
        self.assertEqual(r.status_code, 200, r.text)
        chaves = [s["chave"] for s in r.json()]
        self.assertEqual(len(chaves), 12)
        self.assertNotIn("BALEIA", chaves)
        self.assertNotIn("MESTRE", chaves)                    # nunca usada, nem por encerrado
        self.assertFalse(any("V" in c for c in chaves))
        self.assertNotIn("TEMPERO", chaves)
        for aberta in ("BALEIA", "TEMPEROS"):                # a 4 de toda aberta, de todo grupo
            self.assertTrue(all(Levenshtein.distance(c, aberta) >= 4 for c in chaves), aberta)
        self.assertTrue(all(Levenshtein.distance(a, b) >= 4 for a, b in combinations(chaves, 2)))
        seguras = [s["letras_seguras"] for s in r.json()]
        self.assertEqual(seguras, sorted(seguras, reverse=True))

    def test_grupo_inexistente(self):
        self.assertEqual(self.cliente.get("/grupos/NADA/chaves-sugeridas").status_code, 404)


class EnvioPeloGrupo(ComBanco):
    def setUp(self):
        super().setUp()
        self.prato = self.evento("PRATO", "A")
        self.geleia = self.evento("GELEIA", "B")

    def folha_lida(self, chave, grupo_envio=None, nome="x.pdf"):
        f = self.folha(estado="processada", nome=nome)
        f.grupo_envio = grupo_envio
        f.evento_id = None
        self.db.commit()
        with mock.patch.object(pipeline, "ler_chave", return_value=chave):
            folhas._vincular_evento(self.db, f)
        self.db.commit()
        return f

    def test_pelo_grupo_casa_so_com_ele(self):
        f = self.folha_lida("GELEIA", "A", "a1.pdf")          # chave de OUTRO grupo
        self.assertIsNone(f.evento_id)
        s = self.cliente.get(f"/folhas/{f.id}/eventos-sugeridos").json()
        self.assertEqual([c["chave"] for c in s["candidatos"]], ["PRATO"])
        g = self.folha_lida("PRATO", "A", "a2.pdf")
        self.assertEqual(g.evento_id, self.prato.id)

    def test_envio_geral_casa_com_todos(self):
        f = self.folha_lida("GELEIA", None)
        self.assertEqual(f.evento_id, self.geleia.id)

    def test_a_aba_envio_mostra_o_que_ainda_nao_achou_evento(self):
        sem = self.folha_lida("GELEIA", "A", "a1.pdf")
        com = self.folha_lida("PRATO", "A", "a2.pdf")
        de_b = self.folha_lida("GELEIA", None, "b.pdf")
        ids = {f["id"] for f in self.cliente.get("/folhas", params={"grupo": "A"}).json()}
        self.assertEqual(ids, {sem.id, com.id})
        self.assertNotIn(de_b.id, ids)

    def test_reenviar_o_mesmo_arquivo_responde_200_e_nao_duplica(self):
        # 22/11: o reenvio voltava 201, igual a um envio novo, e a tela não dizia nada
        arquivo = {"arquivo": ("f.png", b"\x89PNG mesma folha", "image/png")}
        with mock.patch.object(folhas, "_ler_e_gravar"):
            primeiro = self.cliente.post("/folhas", files=arquivo)
            segundo = self.cliente.post("/folhas", files=arquivo)
        self.assertEqual((primeiro.status_code, segundo.status_code), (201, 200))
        self.assertEqual([f["id"] for f in segundo.json()], [f["id"] for f in primeiro.json()])

    def test_enviar_guarda_o_grupo(self):
        with mock.patch.object(folhas, "_ler_e_gravar"):
            r = self.cliente.post("/folhas", data={"grupo": "A"},
                                  files={"arquivo": ("f.png", b"\x89PNG folha", "image/png")})
            self.assertIn(r.status_code, (200, 201), r.text)
            self.assertEqual(r.json()[0]["grupo_envio"], "A")
            r = self.cliente.post("/folhas", data={"grupo": "NADA"},
                                  files={"arquivo": ("g.png", b"\x89PNG outra", "image/png")})
            self.assertEqual(r.status_code, 404)

    def test_renomear_o_grupo_leva_o_grupo_de_envio(self):
        f = self.folha_lida("GELEIA", "A")
        self.cliente.patch("/grupos/A", json={"nome": "A 2027"})
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, f.id).grupo_envio, "A 2027")


if __name__ == "__main__":
    unittest.main()
