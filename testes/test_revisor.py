"""Quem decidiu: a revisão e a conclusão da folha levam o usuário logado (bateria de 22/11).

Com login (D18), a tela nunca mandou o nome do revisor, e o servidor gravava o que viesse
do cliente: todas as decisões da bateria saíram com `revisor` vazio. Agora, com login, vale
quem está logado, e o nome mandado pelo cliente não substitui isso.
"""
import unittest

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente
from test_login import SENHA, entrar, porta

from api import sessao, usuarios
from api.config import config
from api.tabelas import Folha, Revisao


class Revisor(ComBanco):
    def setUp(self):
        super().setUp()
        config.arquivo_usuarios.unlink(missing_ok=True)
        usuarios.adicionar("chefe", SENHA, "admin")
        sessao.limitador._falhas.clear()
        ev = self.evento("GELEIA", "A")
        self.f = self.folha(ev)
        self.ln = self.linha(self.f, 1, "quarentena", "900001")

    def ultima_revisao(self):
        self.db.expire_all()
        return self.db.query(Revisao).order_by(Revisao.id.desc()).first()

    def test_com_login_vale_quem_esta_logado(self):
        c = porta()
        entrar(c, "chefe")
        r = c.post(f"/api/linhas/{self.ln.id}/revisao",
                   json={"matricula_escolhida": "900001", "revisor": "outra pessoa"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.ultima_revisao().revisor, "chefe")

        r = c.post(f"/api/folhas/{self.f.id}/concluir", params={"revisor": "outra pessoa"})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, self.f.id).revisada_por, "chefe")

    def test_sem_login_fica_o_que_o_pedido_trouxe(self):
        # `api.main:app`, só desenvolvimento: não há quem esteja logado
        r = self.cliente.post(f"/linhas/{self.ln.id}/revisao",
                              json={"matricula_escolhida": None, "revisor": "henrique"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.ultima_revisao().revisor, "henrique")


if __name__ == "__main__":
    unittest.main()
