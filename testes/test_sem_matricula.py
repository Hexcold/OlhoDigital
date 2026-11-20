"""D26: linha só com o nome nasce "não identificado"; linha só com resíduo nem vira linha.

O pipeline é trocado por um dublê que devolve linhas prontas: o objeto do teste é o que a
API GRAVA a partir delas, não a leitura. Dados inventados, matrículas 9xxxxx.
"""
import unittest
from unittest import mock

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline
from api.esquemas import REVISOR_AUTOMATICO, motivo_rejeicao, sem_matricula
from api.rotas import folhas
from api.tabelas import Folha, Linha, Revisao

VAZIA, RESIDUO, LIDO = "vazia", "sem_caixa", "lido"
PONTO = {"alpha": 0.5, "theta": 0.6, "delta": 0.08, "metrica_nome": "x",
         "tau_nome": 0.65, "recorte": "v3"}


def dados(numero, decisao, motivos_mat, sugerida="900001"):
    return {"numero": numero, "leitura_matricula": "??????", "leitura_nome": "ZULEICA",
            "confiancas_matricula": [], "confiancas_nome": [],
            "motivos_matricula": motivos_mat, "motivos_nome": [LIDO] * 7,
            "score": 0.5, "margem": 0.0, "decisao": decisao,
            "matricula_sugerida": sugerida, "candidatos": [], "duplicada_na_folha": False}


class Predicado(unittest.TestCase):
    def test_sem_digito_lido(self):
        self.assertTrue(sem_matricula([VAZIA] * 6))
        self.assertTrue(sem_matricula([VAZIA, RESIDUO, VAZIA, VAZIA, RESIDUO, VAZIA]))

    def test_matricula_parcial_nao_e_sem_matricula(self):
        self.assertFalse(sem_matricula([LIDO, LIDO, LIDO, VAZIA, VAZIA, VAZIA]))
        self.assertFalse(sem_matricula([LIDO] * 6))

    def test_sem_motivos_nao_afirma_nada(self):
        # linha anulada ou registro antigo sem motivos: não inventa "sem matrícula"
        self.assertFalse(sem_matricula([]))
        self.assertFalse(sem_matricula(None))

    def test_motivo_diz_que_falta_a_matricula(self):
        self.assertEqual(motivo_rejeicao(0.5, 0.0, 0.6, 0.08, [VAZIA] * 6),
                         "sem matrícula: só o nome foi preenchido")
        self.assertIn("3 de 6 células em branco",
                      motivo_rejeicao(0.5, 0.0, 0.6, 0.08, [LIDO] * 3 + [VAZIA] * 3))

    def test_sem_matricula_nao_marca_repetida(self):
        # o print de 20/11: duas linhas sem matrícula empatavam no mesmo palpite
        linhas = [dados(1, "quarentena", [VAZIA] * 6), dados(2, "quarentena", [VAZIA] * 6)]
        self.assertEqual(pipeline.marcar_duplicadas(linhas), 0)
        com_digitos = [dados(1, "aceito", [LIDO] * 6), dados(2, "quarentena", [LIDO] * 6)]
        self.assertEqual(pipeline.marcar_duplicadas(com_digitos), 2)


class Gravacao(ComBanco):
    def ler(self, linhas):
        folha = self.folha(estado="recebida")   # a leitura reivindica só folha na fila
        recursos = {"ponto": PONTO, "modelo_matricula": "m", "modelo_letras": "l"}
        with mock.patch.object(folhas, "sessao", self.Sessao), \
                mock.patch.object(pipeline, "conferir_marcadores", return_value=4), \
                mock.patch.object(pipeline, "carregar_recursos", return_value=recursos), \
                mock.patch.object(pipeline, "processar", return_value=linhas), \
                mock.patch.object(pipeline, "recortar_para_revisao"), \
                mock.patch.object(folhas, "_vincular_evento"):
            folhas._ler_e_gravar(folha.id)
        self.db.expire_all()
        return self.db.get(Folha, folha.id)

    def revisoes(self, folha, numero):
        linha = next(l for l in folha.linhas if l.numero == numero)
        return [(r.matricula_escolhida, r.revisor) for r in linha.revisoes]

    def lote(self):
        return [dados(1, "aceito", [LIDO] * 6),
                dados(2, "quarentena", [VAZIA] * 6),                     # só o nome
                dados(3, "quarentena", [LIDO] * 3 + [VAZIA] * 3),        # matrícula parcial
                dados(4, "quarentena", [VAZIA, RESIDUO] * 3)]            # resíduo na matrícula

    def test_so_nome_nasce_nao_identificado_e_o_resto_nao(self):
        folha = self.ler(self.lote())
        self.assertEqual(folha.estado, "processada")
        self.assertEqual(self.revisoes(folha, 1), [])
        self.assertEqual(self.revisoes(folha, 2), [(None, REVISOR_AUTOMATICO)])
        self.assertEqual(self.revisoes(folha, 3), [])
        self.assertEqual(self.revisoes(folha, 4), [(None, REVISOR_AUTOMATICO)])

    def test_continua_na_quarentena_e_a_tela_ve_o_padrao(self):
        folha = self.ler(self.lote())
        r = self.cliente.get(f"/folhas/{folha.id}/linhas")
        self.assertEqual(r.status_code, 200)
        linha = next(l for l in r.json() if l["numero"] == 2)
        self.assertEqual(linha["decisao"], "quarentena")
        self.assertEqual(linha["motivo_rejeicao"], "sem matrícula: só o nome foi preenchido")
        self.assertEqual(linha["revisao"]["revisor"], REVISOR_AUTOMATICO)
        self.assertIsNone(linha["revisao"]["matricula_escolhida"])

    def test_nao_trava_a_conclusao_mas_a_parcial_trava(self):
        folha = self.ler(self.lote())
        r = self.cliente.post(f"/folhas/{folha.id}/concluir")
        self.assertEqual(r.status_code, 409)
        self.assertIn("1 linha(s)", r.json()["detail"])      # só a 3, a parcial

    def test_revisor_pode_trocar_por_uma_pessoa(self):
        folha = self.ler(self.lote())
        linha = next(l for l in folha.linhas if l.numero == 2)
        r = self.cliente.post(f"/linhas/{linha.id}/revisao",
                              json={"matricula_escolhida": "900001", "revisor": "gente"})
        self.assertIn(r.status_code, (200, 201), r.text)
        r = self.cliente.get(f"/folhas/{folha.id}/linhas")
        self.assertEqual(next(l for l in r.json() if l["numero"] == 2)
                         ["revisao"]["matricula_escolhida"], "900001")

    def test_padrao_automatico_nao_bloqueia_a_releitura(self):
        folha = self.ler(self.lote())
        with mock.patch.object(folhas, "_ler_e_gravar"):
            r = self.cliente.post(f"/folhas/{folha.id}/reprocessar")
        self.assertEqual(r.status_code, 200, r.text)

    def test_decisao_humana_ainda_bloqueia_a_releitura(self):
        folha = self.ler(self.lote())
        linha = next(l for l in folha.linhas if l.numero == 3)
        self.db.add(Revisao(linha_id=linha.id, matricula_escolhida=None, revisor="gente"))
        self.db.commit()
        with mock.patch.object(folhas, "_ler_e_gravar"):
            r = self.cliente.post(f"/folhas/{folha.id}/reprocessar")
        self.assertEqual(r.status_code, 409)
        self.assertIn("1 decisão", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
