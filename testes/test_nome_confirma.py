"""D37: o nome tem que apontar para a pessoa, mesmo com a matrícula exata (A67).

Base e linhas fabricadas; nenhum dado real. Os dois casos que a regra pega são reconstruídos: o
apelido com a matrícula certa e a continuação do nome na linha de baixo, com traços na matrícula.
"""
import unittest

import apoio  # noqa: F401  (caminhos e isolamento da bateria)
import fuzzy
import quarentena
from api.esquemas import motivo_rejeicao as motivo_api
from fuzzy import SIM_NOME, METRICA_NOME, fracao_nome, identificar
from identificar_folha import avaliar_linha, conferir_contra_identificar
from test_servidor_v3 import linha

SIM = SIM_NOME[METRICA_NOME]
PRIMEIROS = ("ABELARDO", "BRIGIDA", "CLODOMIRO", "DORALICE", "EPIFANIO", "FILOMENA", "GUMERCINDO",
             "HERMINIA", "IDALINO", "JOVELINA")
ULTIMOS = ("SOUSA", "SOARES", "FROTA", "VIEGAS", "MOURAO", "TELES", "PINHO", "LACERDA", "BRAGA",
           "CUNHA")
# 100 pessoas; o dígito de matrícula varia bastante para a margem não apertar
BASE = [{"matricula": f"{100000 + 7919 * i}", "nome": f"{p} {u}", "curso": "X"}
        for i, (p, u) in enumerate((p, u) for p in PRIMEIROS for u in ULTIMOS)]
ALVO = next(a for a in BASE if a["nome"] == "GUMERCINDO LACERDA")


def decidir(matricula, nome, **k):
    return identificar(matricula, nome, BASE, tau_nome=fuzzy.TAU_NOME, **k)


class Regra(unittest.TestCase):
    def test_corte_vigente(self):
        self.assertEqual(fuzzy.CORTE_ACASO, 0.05)

    def test_apelido_com_a_matricula_certa_vai_para_a_quarentena(self):
        apelido = "XUXU BOLADAO"
        sem = decidir(ALVO["matricula"], apelido)
        # pré-condições: é o dono da matrícula, exata, e todo o resto do gate passa
        self.assertEqual((sem["decisao"], sem["aluno"]["matricula"]), ("aceito", ALVO["matricula"]))
        self.assertEqual(decidir(ALVO["matricula"], apelido, corte_acaso=0.05)["decisao"],
                         "quarentena")

    def test_nome_mal_lido_da_pessoa_certa_continua_aceito(self):
        lido = "GIMFRC NDO LACFPDA"
        com = decidir(ALVO["matricula"], lido, corte_acaso=0.05)
        self.assertEqual((com["decisao"], com["aluno"]["matricula"]), ("aceito", ALVO["matricula"]))

    def test_duas_letras_nao_distinguem_ninguem(self):
        # "SO" cabe em SOUSA e SOARES: similaridade 1,0 com 20 das 100 pessoas
        alvo = next(a for a in BASE if a["nome"] == "IDALINO SOUSA")
        self.assertEqual(SIM("SO", alvo["nome"]), 1.0)
        self.assertEqual(decidir(alvo["matricula"], "SO")["decisao"], "aceito")
        self.assertGreater(fracao_nome("SO", alvo, BASE, SIM), 0.05)
        self.assertEqual(decidir(alvo["matricula"], "SO", corte_acaso=0.05)["decisao"],
                         "quarentena")

    def test_o_campeao_nao_conta_contra_si(self):
        self.assertEqual(fracao_nome(ALVO["nome"], ALVO, BASE, SIM), 0.0)

    def test_fronteira_do_corte(self):
        f = fracao_nome("SO", BASE[0], BASE, SIM)
        self.assertEqual(decidir(BASE[0]["matricula"], "SO", corte_acaso=f)["decisao"], "aceito")
        self.assertEqual(decidir(BASE[0]["matricula"], "SO", corte_acaso=f - 1e-6)["decisao"],
                         "quarentena")

    def test_desligada_por_padrao(self):
        self.assertEqual(decidir(ALVO["matricula"], "XUXU BOLADAO")["decisao"], "aceito")


class Pipeline(unittest.TestCase):
    def avaliar(self, matricula, nome):
        return avaliar_linha(linha(matricula, nome), "chuta", 0.5, BASE, None, False)

    def test_aplica_grava_e_confere(self):
        apelido = self.avaliar(ALVO["matricula"], "XUXU BOLADAO")
        certo = self.avaliar(ALVO["matricula"], ALVO["nome"])
        self.assertEqual((apelido["decisao"], apelido["nome_confirma"]), ("quarentena", False))
        self.assertEqual((certo["decisao"], certo["nome_confirma"]), ("aceito", True))
        conferir_contra_identificar([apelido, certo], BASE)            # estoura se divergirem

    def test_quem_ja_caiu_por_outro_motivo_nao_ganha_este(self):
        r = self.avaliar("999999", "XUXU BOLADAO")
        self.assertEqual((r["decisao"], r["nome_confirma"]), ("quarentena", True))

    def test_registro_antigo_confere_sem_a_regra(self):
        r = self.avaliar(ALVO["matricula"], "XUXU BOLADAO")
        antigo = {k: v for k, v in r.items() if k != "nome_confirma"}
        antigo["decisao"] = "aceito"
        conferir_contra_identificar([antigo], BASE)


class Motivo(unittest.TestCase):
    def test_servidor(self):
        m = motivo_api(0.71, 0.20, 0.60, 0.08, ["lido"] * 6, tau_nome=0.65, nome_nao_confirma=True)
        self.assertIn("o nome lido não confirma", m)
        # sem a marca, o τ continua sendo o motivo que sobra
        self.assertIn("dois canais", motivo_api(0.71, 0.20, 0.60, 0.08, ["lido"] * 6,
                                                tau_nome=0.65))

    def test_pagina_do_experimento(self):
        m = quarentena.motivo_rejeicao(0.71, 0.20, motivos=["lido"] * 6, dois_canais=True,
                                       nome_confirma=False)
        self.assertIn("mais de 5% da base", m)


if __name__ == "__main__":
    unittest.main()
