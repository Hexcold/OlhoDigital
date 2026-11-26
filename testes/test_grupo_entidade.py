"""D35: o grupo vira entidade e centro do fluxo. Dados inventados."""
import unittest

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api.tabelas import Evento, Grupo

CORPO = {"tipo": "evento", "nome": "x", "local": "sala 1"}


class GrupoEntidade(ComBanco):
    def grupo(self, nome):
        return next((g for g in self.cliente.get("/grupos").json() if g["nome"] == nome), None)

    def test_criar_grupo_vazio_aparece_preparando(self):
        r = self.cliente.post("/grupos", json={"nome": "  EU   2027 ", "descricao": "Encontros"})
        self.assertEqual(r.status_code, 201, r.text)
        g = self.grupo("EU 2027")
        self.assertEqual((g["eventos"], g["folhas"], g["etapa"], g["descricao"]),
                         (0, 0, "preparando", "Encontros"))
        self.assertEqual(self.cliente.get("/grupos/EU 2027").json()["nome"], "EU 2027")

    def test_nome_repetido_vazio_ou_com_barra(self):
        self.cliente.post("/grupos", json={"nome": "EU"})
        self.assertEqual(self.cliente.post("/grupos", json={"nome": "EU"}).status_code, 409)
        for ruim in ("", "   ", "A/B", "G" * 61):
            with self.subTest(nome=ruim):
                self.assertEqual(self.cliente.post("/grupos", json={"nome": ruim}).status_code,
                                 422)

    def test_renomear_leva_os_eventos(self):
        self.evento("TESTEUM", "EU")
        self.evento("TESTEDOIS", "EU")
        self.cliente.post("/grupos", json={"nome": "OUTRO"})
        self.assertEqual(self.cliente.patch("/grupos/EU", json={"nome": "OUTRO"}).status_code,
                         409)
        r = self.cliente.patch("/grupos/EU", json={"nome": "EU 2026"})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual({e.grupo for e in self.db.query(Evento)}, {"EU 2026"})
        self.assertIsNone(self.grupo("EU"))
        self.assertEqual(self.grupo("EU 2026")["eventos"], 2)

    def test_apagar_so_vazio(self):
        self.evento("TESTEUM", "EU")
        self.cliente.post("/grupos", json={"nome": "VAZIO"})
        self.assertEqual(self.cliente.delete("/grupos/EU").status_code, 409)
        self.assertEqual(self.cliente.delete("/grupos/VAZIO").status_code, 204)
        self.assertIsNone(self.grupo("VAZIO"))

    def test_evento_sem_grupo_e_recusado_e_com_grupo_novo_cria_o_grupo(self):
        r = self.cliente.post("/eventos", json={**CORPO, "chave": "TESTEUM"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("grupo", r.text)
        r = self.cliente.post("/eventos", json={**CORPO, "chave": "TESTEUM", "grupo": "NOVO"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.db.query(Grupo).filter_by(nome="NOVO").count(), 1)

    def test_cadastro_em_lote_exige_grupo(self):
        itens = [{"linha": 2, "chave": "TESTEUM", "folhas": 1}]
        self.assertEqual(self.cliente.post("/impressao/cadastrar",
                                           json={"itens": itens}).status_code, 422)
        r = self.cliente.post("/impressao/cadastrar", json={"itens": itens, "grupo": "EU"})
        self.assertEqual(r.json(), {"criados": 1, "ja_existiam": 0})
        self.assertEqual(self.grupo("EU")["eventos"], 1)

    def test_etapas(self):
        ev = self.evento("TESTEUM", "EU")
        self.assertEqual(self.grupo("EU")["etapa"], "preparando")
        f = self.folha(ev, estado="recebida")
        self.assertEqual(self.grupo("EU")["etapa"], "recebendo")
        f.estado = "processada"
        self.db.commit()
        linha = self.linha(f, 1, "quarentena", "900001")
        g = self.grupo("EU")
        self.assertEqual((g["etapa"], g["pendentes"]), ("revisando", 1))
        self.revisar(linha, "900001")
        self.assertEqual(self.grupo("EU")["etapa"], "pronto")
        self.cliente.post("/grupos/EU/situacao", params={"situacao": "encerrado",
                                                         "confirmacao": "EU"})
        self.assertEqual(self.grupo("EU")["etapa"], "encerrado")

    def test_etapa_ve_a_folha_enviada_pelo_grupo_ainda_sem_evento(self):
        # bateria de 22/11: as 62 folhas do lote passaram toda a leitura com o grupo em
        # "preparando", porque a etapa só contava folha já ligada a evento pela chave
        self.evento("TESTEUM", "EU")
        f = self.folha(None, estado="recebida")
        f.grupo_envio = "EU"
        self.db.commit()
        self.assertEqual(self.grupo("EU")["etapa"], "recebendo")
        f.estado = "erro"
        self.db.commit()
        self.assertEqual(self.grupo("EU")["etapa"], "revisando")    # alguém precisa agir
        f.estado = "processada"                                      # na bandeja, sem evento
        self.db.commit()
        g = self.grupo("EU")
        self.assertEqual(g["etapa"], "revisando")
        self.assertEqual(g["folhas"], 0)        # a contagem das folhas do grupo não muda

    def test_a_concluir_nao_conta_copia(self):
        # 22/11: "124 folha(s) ainda não foram concluídas" num grupo de 62 papéis
        ev = self.evento("TESTEUM", "EU")
        vale = self.folha(ev, nome="a.pdf")
        copia = self.folha(ev, nome="b.pdf")
        copia.copia_de = vale.id
        concluida = self.folha(ev, nome="c.pdf", revisada=True)
        self.db.commit()
        self.assertEqual(self.grupo("EU")["a_concluir"], 1)
        self.assertIsNotNone(concluida.revisada_em)


if __name__ == "__main__":
    unittest.main()
