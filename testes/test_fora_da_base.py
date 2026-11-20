"""D32: "não está na base" — a pessoa estava lá, é legível, mas não está no contradomínio.

Dados inventados: 900001 e 900002 estão na base do teste; 951234 não está.
"""
import unittest

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api.tabelas import Revisao


class ForaDaBase(ComBanco):
    def setUp(self):
        super().setUp()
        self.ev = self.evento("TESTEUM", "EU")
        self.f = self.folha(self.ev)
        self.f.linhas_preenchidas = 1
        self.db.commit()
        self.ln = self.linha(self.f, 1, "quarentena", "900001")

    def revisar(self, **corpo):
        return self.cliente.post(f"/linhas/{self.ln.id}/revisao",
                                 json={"matricula_escolhida": None, "revisor": "gente", **corpo})

    def ultima(self):
        self.db.expire_all()
        return max(self.db.query(Revisao).filter_by(linha_id=self.ln.id), key=lambda r: r.id)

    def test_grava_e_entra_na_frequencia(self):
        r = self.revisar(matricula_informada=" 951234 ", nome_informado="  FULANO   DE TAL ")
        self.assertEqual(r.status_code, 201, r.text)
        rev = self.ultima()
        self.assertEqual((rev.matricula_escolhida, rev.matricula_informada, rev.nome_informado),
                         (None, "951234", "FULANO DE TAL"))
        freq = self.cliente.get("/grupos/EU/frequencia.json").json()["frequencia"]
        self.assertEqual([(x["situacao"], x["matricula"], x["nome"]) for x in freq],
                         [("presente (fora da base)", "951234", "FULANO DE TAL")])

    def test_sem_matricula_vira_nao_identificado(self):
        r = self.revisar(matricula_informada="  ", nome_informado="FULANO")
        self.assertEqual(r.status_code, 201, r.text)
        rev = self.ultima()
        self.assertEqual((rev.matricula_informada, rev.nome_informado), (None, None))
        freq = self.cliente.get("/grupos/EU/frequencia.json").json()["frequencia"]
        self.assertEqual(freq[0]["situacao"], "não identificado")

    def test_matricula_da_base_pede_para_escolher_a_pessoa(self):
        r = self.revisar(matricula_informada="900002", nome_informado="X")
        self.assertEqual(r.status_code, 422)
        self.assertIn("está na base", r.json()["detail"])
        self.assertIn("QUITERIA", r.json()["detail"])

    def test_matricula_ilegivel_ou_de_tamanho_errado(self):
        for ruim in ("95?234", "95123", "9512345", "abc123"):
            with self.subTest(ruim=ruim):
                r = self.revisar(matricula_informada=ruim)
                if ruim == "95123":                            # 5 dígitos é válido (A04)
                    self.assertEqual(r.status_code, 201, r.text)
                else:
                    self.assertEqual(r.status_code, 422, ruim)

    def test_nao_escolhe_e_informa_ao_mesmo_tempo(self):
        r = self.revisar(matricula_escolhida="900001", matricula_informada="951234")
        self.assertEqual(r.status_code, 422)

    def test_conta_como_decidida_no_resumo(self):
        self.revisar(matricula_informada="951234", nome_informado="FULANO")
        resumo = self.cliente.get(f"/folhas/{self.f.id}/resumo").json()
        self.assertEqual((resumo["fora_da_base"], resumo["nao_identificados"],
                          resumo["pendentes"]), (1, 0, 0))
        self.assertEqual(self.cliente.post(f"/folhas/{self.f.id}/concluir").status_code, 200)

    def test_a_tela_e_o_json_veem_o_que_foi_informado(self):
        self.revisar(matricula_informada="951234", nome_informado="FULANO")
        linha = self.cliente.get(f"/folhas/{self.f.id}/linhas").json()[0]
        self.assertEqual((linha["revisao"]["matricula_informada"],
                          linha["revisao"]["nome_informado"]), ("951234", "FULANO"))
        j = self.cliente.get(f"/folhas/{self.f.id}/json").json()
        self.assertEqual(j["linhas"][0]["revisao"]["matricula_informada"], "951234")


if __name__ == "__main__":
    unittest.main()
