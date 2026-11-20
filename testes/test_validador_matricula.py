"""D22: só aceita a linha cuja matrícula tem as 6 células preenchidas (A59).

Sem a regra, seis células em branco viram seis curingas, que casam com a base inteira, e o
nome decide sozinho: no teste do Experimento 03 uma linha vazia, com 3 letras de resíduo de
grade no nome, virou pessoa. Base e nomes daqui são fictícios.
"""
import unittest

import apoio  # noqa: F401  (caminhos e isolamento da bateria)
import quarentena
from api.esquemas import motivo_rejeicao as motivo_api
from fuzzy import identificar
from identificar_folha import (Celula, Linha, avaliar_linha,
                               conferir_contra_identificar)

BASE = [{"matricula": "900001", "nome": "ZULEICA AMARANTE FROTA", "curso": "X"},
        {"matricula": "900002", "nome": "QUITERIA BRANDAO VIEGAS", "curso": "X"},
        {"matricula": "912345", "nome": "OTONIEL BARCELAR FURTADO", "curso": "X"}]


def linha(digitos: str, nome: str, conf: float = 0.99) -> Linha:
    """Espaço = célula em branco; '#' = célula com tinta e sem caixa."""
    def cel(c):
        if c == " ":
            return Celula(None, 0.0, "vazia")
        if c == "#":
            return Celula(None, 0.0, "sem_caixa")
        return Celula(c, conf, "lido")
    lin = Linha(folha="sintetica", numero=1)
    lin.celulas = [cel(d) for d in digitos]
    lin.celulas_nome = [cel(c) for c in nome.ljust(18)]
    return lin


class MatriculaCompleta(unittest.TestCase):
    def test_seis_celulas_com_tinta(self):
        self.assertTrue(linha("912345", "OTONIEL").matricula_completa)

    def test_uma_celula_em_branco_basta_para_barrar(self):
        self.assertFalse(linha("91234 ", "OTONIEL").matricula_completa)

    def test_matricula_toda_em_branco(self):
        self.assertFalse(linha("      ", "OTONIEL").matricula_completa)

    def test_sem_caixa_tem_tinta_e_conta(self):
        self.assertTrue(linha("9123#5", "OTONIEL").matricula_completa)


class LinhaEmBranco(unittest.TestCase):
    """D26: resíduo de grade (`sem_caixa`) não faz uma linha vazia existir."""

    def test_so_residuo_no_nome_e_linha_em_branco(self):
        # o caso da revisão de 20/11: matrícula sem tinta, nome com 5 células de resíduo
        self.assertTrue(linha("      ", "#####").em_branco)
        self.assertTrue(linha("      ", "########").em_branco)

    def test_nome_lido_sem_matricula_continua_linha(self):
        self.assertFalse(linha("      ", "ANA").em_branco)
        self.assertFalse(linha("      ", "OTONIEL").em_branco)

    def test_matricula_lida_sem_nome_continua_linha(self):
        self.assertFalse(linha("9123##", "").em_branco)

    def test_residuo_nao_completa_o_minimo(self):
        # duas letras lidas e resíduo: abaixo do mínimo nos dois campos
        self.assertTrue(linha("      ", "AN###").em_branco)

    def test_tinta_da_linha_de_cima_nao_faz_linha(self):
        # D27: o caso de 20/11, três dígitos lidos que eram a perna da escrita de cima
        lin = linha("5  4 1", "###")
        self.assertFalse(lin.em_branco)                 # sem a marca, a linha existe
        for c in lin.celulas:
            c.so_topo = c.motivo == "lido"
        self.assertTrue(lin.em_branco)

    def test_so_topo_numa_linha_real_nao_a_apaga(self):
        lin = linha("912345", "OTONIEL")
        lin.celulas_nome[7].so_topo = True              # uma perna no fim do nome
        lin.celulas[0].so_topo = True
        self.assertFalse(lin.em_branco)

    def test_anulacao_continua_contando(self):
        lin = linha("      ", "")
        lin.celulas_nome[:3] = [Celula(None, 1.0, "anulada")] * 3
        self.assertFalse(lin.em_branco)


class Gate(unittest.TestCase):
    def test_identificar_barra_sem_a_matricula(self):
        ok = identificar("912345", "OTONIEL BARCELAR FURTADO", BASE)
        self.assertEqual(ok["decisao"], "aceito")
        barrada = identificar("912345", "OTONIEL BARCELAR FURTADO", BASE, matricula_completa=False)
        self.assertEqual(barrada["decisao"], "quarentena")
        # a regra não mexe no ranking: mesmo campeão, mesmo score
        self.assertEqual(barrada["aluno"]["matricula"], "912345")
        self.assertAlmostEqual(barrada["score"], ok["score"])

    def test_a59_matricula_em_branco_com_nome_perfeito_vai_para_quarentena(self):
        r = avaliar_linha(linha("      ", "QUITERIA BRANDAO VIEGAS"), "chuta", 0.5, BASE, None, False)
        self.assertEqual(r["leitura"], "??????")
        # sem a regra passaria: os curingas casam com todos e o nome é exato
        self.assertGreaterEqual(r["score"], 0.99)
        self.assertEqual(r["decisao"], "quarentena")
        self.assertFalse(r["matricula_completa"])

    def test_linha_completa_continua_aceita(self):
        r = avaliar_linha(linha("912345", "OTONIEL BARCELAR FURTADO"), "chuta", 0.5, BASE, None, False)
        self.assertEqual(r["decisao"], "aceito")
        self.assertTrue(r["matricula_completa"])

    def test_abstencao_nao_e_celula_em_branco(self):
        # no modo abstém, dígito escrito e incerto vira '?', mas a célula tem tinta (D02)
        lin = linha("912345", "OTONIEL BARCELAR FURTADO", conf=0.5)
        r = avaliar_linha(lin, "abstem", 0.5, BASE, None, False)
        self.assertEqual(r["leitura"], "??????")
        self.assertTrue(r["matricula_completa"])

    def test_os_dois_gates_concordam(self):
        regs = [avaliar_linha(linha(d, n), "chuta", 0.5, BASE, None, False)
                for d, n in (("      ", "QUITERIA BRANDAO VIEGAS"), ("912345", "OTONIEL BARCELAR FURTADO"),
                             ("9  345", "ZULEICA AMARANTE FROTA"))]
        conferir_contra_identificar(regs, BASE)          # estoura se divergirem


class Motivo(unittest.TestCase):
    def test_servidor_diz_matricula_incompleta(self):
        m = motivo_api(0.95, 0.30, 0.60, 0.05, ["lido"] * 4 + ["vazia"] * 2)
        self.assertEqual(m, "matrícula incompleta: 2 de 6 células em branco")

    def test_servidor_diz_sem_matricula(self):
        # D26: nenhum dígito lido não é matrícula incompleta, é linha só com o nome
        m = motivo_api(0.95, 0.30, 0.60, 0.05, ["vazia"] * 6)
        self.assertEqual(m, "sem matrícula: só o nome foi preenchido")

    def test_servidor_sem_branco_mantem_o_motivo_de_antes(self):
        self.assertEqual(motivo_api(0.5, 0.30, 0.60, 0.05, ["lido"] * 6),
                         "score 0.500 abaixo de θ=0.6")
        self.assertEqual(motivo_api(0.5, 0.30, 0.60, 0.05), "score 0.500 abaixo de θ=0.6")

    def test_pagina_do_experimento_diz_o_mesmo(self):
        m = quarentena.motivo_rejeicao(0.95, 0.30, motivos=["lido"] * 5 + ["vazia"])
        self.assertEqual(m, "matrícula incompleta: 1 de 6 células em branco")


if __name__ == "__main__":
    unittest.main()
