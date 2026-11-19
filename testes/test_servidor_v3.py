"""D23: o servidor lê com o recorte V3 e decide com a regra dos dois canais (A56–A58).

Célula e base sintéticas; nenhum dado real. A equivalência com o braço medido no teste do
Experimento 03, em folhas reais, é conferida fora da bateria, em
`coleta-eu2026/scripts/conferir_servidor_v3.py`.
"""
import unittest
from collections import Counter

import apoio  # noqa: F401  (caminhos e isolamento da bateria)
import fuzzy
import quarentena
import recorte_celula as rc
from api.esquemas import motivo_rejeicao as motivo_api
from fuzzy import identificar
from identificar_folha import Celula, Linha, avaliar_linha, conferir_contra_identificar

BASE = [{"matricula": "900001", "nome": "ZULEICA AMARANTE FROTA", "curso": "X"},
        {"matricula": "900002", "nome": "QUITERIA BRANDAO VIEGAS", "curso": "X"},
        {"matricula": "912345", "nome": "OTONIEL BARCELAR FURTADO", "curso": "X"}]


def linha(digitos: str, nome: str) -> Linha:
    def cel(c):
        return Celula(None, 0.0, "vazia") if c == " " else Celula(c, 0.99, "lido")
    lin = Linha(folha="sintetica", numero=1)
    lin.celulas = [cel(d) for d in digitos]
    lin.celulas_nome = [cel(c) for c in nome.ljust(18)]
    return lin


def pe(img) -> int:
    """Maior número de pixels de tinta numa das 5 fileiras de baixo."""
    return int((img[-5:] > 0).sum(axis=1).max()) if img.size else 0


class Recorte(unittest.TestCase):
    def test_grade_escura_devolve_o_traco_apoiado_na_linha(self):
        cinza, caixa = rc.celula_sintetica(80)
        self.assertGreaterEqual(pe(rc.filtrar(*rc.limpar(cinza, caixa)).get_image().data), 18)

    def test_a_linha_da_grade_sai_e_fica_so_a_caneta(self):
        cinza, caixa = rc.celula_sintetica(80)
        img, _ = rc.limpar(cinza, caixa)
        # nas fileiras de baixo sobra o pé (24 px), não a linha inteira (~47 px de largura)
        self.assertLessEqual(pe(img.data), 28)

    def test_grade_clara_cai_no_corte_de_sempre(self):
        c = Counter()
        cinza, caixa = rc.celula_sintetica(205)
        img = rc.filtrar(*rc.limpar(cinza, caixa, c)).get_image().data
        self.assertEqual(sum(c.values()), 4)
        self.assertGreater(int((img > 0).sum()), 0)

    def test_parametros_congelados_no_experimento_03(self):
        self.assertEqual((rc.FOLGA, rc.RAIO, rc.NUCLEO, rc.FATOR_CANETA), (6, 5, 0.75, 0.6))


class Gate(unittest.TestCase):
    def test_ponto_vigente(self):
        self.assertEqual((fuzzy.ALPHA, fuzzy.THETA, fuzzy.DELTA, fuzzy.TAU_NOME),
                         (0.5, 0.60, 0.08, 0.65))

    def test_matricula_vizinha_com_nome_fraco_vai_para_quarentena(self):
        sem = identificar("912346", "BENTO", BASE)                       # o padrão: sem a regra
        com = identificar("912346", "BENTO", BASE, tau_nome=fuzzy.TAU_NOME)
        # pré-condições: score e margem passam e o nome fica abaixo de τ — quem barra é a regra
        self.assertGreaterEqual(sem["score"], fuzzy.THETA)
        self.assertGreaterEqual(sem["margem"], fuzzy.DELTA)
        self.assertLess(fuzzy.SIM_NOME[fuzzy.METRICA_NOME]("BENTO", "OTONIEL BARCELAR FURTADO"), fuzzy.TAU_NOME)
        self.assertEqual(sem["decisao"], "aceito")
        self.assertEqual(com["decisao"], "quarentena")

    def test_matricula_exata_dispensa_o_nome(self):
        r = identificar("912345", "BENTO", BASE, tau_nome=fuzzy.TAU_NOME)
        self.assertLess(fuzzy.SIM_NOME[fuzzy.METRICA_NOME]("BENTO", "OTONIEL BARCELAR FURTADO"), fuzzy.TAU_NOME)
        self.assertEqual(r["decisao"], "aceito")

    def test_avaliar_linha_aplica_a_regra_grava_e_confere(self):
        r = avaliar_linha(linha("912346", "BENTO"), "chuta", 0.5, BASE, None, False)
        self.assertEqual(r["decisao"], "quarentena")
        self.assertFalse(r["dois_canais"])
        ok = avaliar_linha(linha("912345", "OTONIEL BARCELAR FURTADO"), "chuta", 0.5, BASE, None, False)
        self.assertEqual(ok["decisao"], "aceito")
        self.assertTrue(ok["dois_canais"])
        conferir_contra_identificar([r, ok], BASE)                     # estoura se divergirem

    def test_registro_antigo_confere_sem_a_regra(self):
        # registro gravado antes do D23 não tem o campo, e foi decidido sem a regra
        r = avaliar_linha(linha("912346", "BENTO"), "chuta", 0.5, BASE, None, False)
        antigo = {k: v for k, v in r.items() if k != "dois_canais"}
        antigo["decisao"] = "aceito"
        conferir_contra_identificar([antigo], BASE)


class Motivo(unittest.TestCase):
    def test_servidor_explica_a_regra(self):
        self.assertEqual(motivo_api(0.70, 0.20, 0.60, 0.08, ["lido"] * 6, tau_nome=0.65),
                         "matrícula não é exata e o nome não a confirma (dois canais, τ=0.65)")

    def test_folha_lida_antes_da_regra_nao_ganha_motivo_que_nao_existia(self):
        self.assertEqual(motivo_api(0.70, 0.20, 0.60, 0.08, ["lido"] * 6), "")

    def test_pagina_do_experimento_explica_a_regra(self):
        m = quarentena.motivo_rejeicao(0.70, 0.20, motivos=["lido"] * 6, dois_canais=False)
        self.assertEqual(m, f"matrícula não é exata e o nome não a confirma "
                            f"(dois canais, τ={fuzzy.TAU_NOME})")


if __name__ == "__main__":
    unittest.main()
