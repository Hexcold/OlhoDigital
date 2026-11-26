"""A bateria não escreve no que é real (24/11).

Dois testes de envio deixavam arquivos falsos em `dados/enviadas/`, a pasta da produção: o
`apoio` isolava o banco e as contas, mas não as pastas.
"""
import os
import unittest
from pathlib import Path

import apoio

from api.config import Config, config


class Isolamento(unittest.TestCase):
    def test_todo_caminho_que_a_api_escreve_cai_na_pasta_temporaria(self):
        # `pasta_pipeline` é de onde a API lê os modelos. Toda outra pasta ou arquivo da
        # configuração recebe dado: uma nova, esquecida no `apoio`, falha aqui.
        caminhos = [c for c, campo in Config.model_fields.items()
                    if campo.annotation in (Path, "Path") and c != "pasta_pipeline"]
        self.assertGreaterEqual(len(caminhos), 3, caminhos)
        for nome in caminhos:
            with self.subTest(nome):
                self.assertTrue(
                    Path(getattr(config, nome)).resolve().is_relative_to(apoio.ESTADO.resolve()),
                    f"{nome} aponta para {getattr(config, nome)}")

    def test_o_banco_aponta_para_uma_porta_fechada(self):
        self.assertIn("127.0.0.1:1/", config.database_url)

    def test_o_cabecalho_salvo_e_temporario(self):
        self.assertTrue(os.environ["OLHODIGITAL_CABECALHO"].startswith(str(apoio.ESTADO)))


if __name__ == "__main__":
    unittest.main()
