"""A61: trocar o escaneamento de uma folha pelo de outro escaneamento do mesmo papel.

A leitura é trocada por um dublê: o objeto do teste é o que a rota faz com a folha, os
arquivos e as travas. Imagens de mentira, dados inventados.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline
from api.config import config
from api.rotas import folhas
from api.tabelas import Folha, Revisao


class Mensagem(unittest.TestCase):
    def test_com_tres_afirma_e_diz_o_canto(self):
        m = pipeline.mensagem_marcadores({1, 2, 3})
        self.assertIn("é uma folha de presença", m)
        self.assertIn("superior esquerdo", m)
        self.assertIn("trocar escaneamento", m)
        self.assertIn("inferior direito", pipeline.mensagem_marcadores({0, 1, 2}))

    def test_com_menos_de_tres_nao_afirma(self):
        m = pipeline.mensagem_marcadores({1})
        self.assertIn("não parece", m)
        self.assertNotIn("é uma folha de presença", m)
        self.assertIn("0 de 4", pipeline.mensagem_marcadores(set()))


class Substituir(ComBanco):
    def setUp(self):
        super().setUp()
        self.pasta_folhas = Path(tempfile.mkdtemp(prefix="olhodigital_subst_"))
        p = mock.patch.object(config, "pasta_folhas", self.pasta_folhas)
        p.start()
        self.addCleanup(p.stop)
        # a releitura roda em segundo plano; aqui ela não roda: o teste é da troca
        q = mock.patch.object(folhas, "_ler_e_gravar")
        self.ler = q.start()
        self.addCleanup(q.stop)

    def enviar(self, folha, conteudo=b"\x89PNG novo escaneamento", forcar=False):
        dados = {"forcar": "true"} if forcar else {}
        return self.cliente.post(f"/folhas/{folha.id}/substituir",
                                 files={"arquivo": ("reescaneada.png", conteudo, "image/png")},
                                 data=dados)

    def test_troca_a_imagem_e_rele_na_mesma_folha(self):
        folha = self.folha(estado="nao_e_folha", nome="lote.pdf", pagina=28)
        antiga = Path(folha.arquivo)
        r = self.enviar(folha)
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        f = self.db.get(Folha, folha.id)
        self.assertEqual((f.nome_original, f.pagina), ("lote.pdf", 28))   # o rótulo fica
        self.assertEqual(f.estado, "recebida")
        self.assertIsNone(f.erro)
        self.assertEqual(f.arquivo_reescaneado, "reescaneada.png")
        self.assertIsNotNone(f.reescaneada_em)
        self.assertTrue(Path(f.arquivo).exists())
        self.assertEqual(Path(f.arquivo).read_bytes(), b"\x89PNG novo escaneamento")
        self.assertFalse(antiga.exists())                                  # a antiga sai
        self.assertFalse(pipeline.pasta_recortes(antiga).exists())
        self.ler.assert_called_once_with(folha.id)

    def test_reenviar_o_arquivo_novo_devolve_a_mesma_folha(self):
        folha = self.folha(estado="nao_e_folha")
        self.enviar(folha)
        with mock.patch.object(folhas, "_ler_e_gravar"):
            r = self.cliente.post("/folhas", files={"arquivo": ("x.png", b"\x89PNG novo escaneamento",
                                                                "image/png")})
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual([f["id"] for f in r.json()], [folha.id])
        self.assertEqual(self.contar(Folha), 1)

    def test_recusa_o_mesmo_arquivo_e_o_de_outra_folha(self):
        a = self.folha(estado="nao_e_folha", nome="a.pdf")
        b = self.folha(estado="nao_e_folha", nome="b.pdf")
        self.assertEqual(self.enviar(a).status_code, 200)
        r = self.enviar(a)
        self.assertEqual(r.status_code, 409)
        self.assertIn("mesmo arquivo", r.json()["detail"])
        r = self.enviar(b)
        self.assertEqual(r.status_code, 409)
        self.assertIn(f"folha {a.id}", r.json()["detail"])

    def test_recusa_pdf_com_varias_paginas_sem_deixar_arquivo(self):
        folha = self.folha(estado="nao_e_folha")
        antiga = Path(folha.arquivo)

        def duas(conteudo, destino, origem):
            caminhos = [destino / f"{origem}_p{i}.png" for i in (1, 2)]
            for c in caminhos:
                c.write_bytes(b"png")
            return caminhos

        with mock.patch.object(folhas.documentos, "paginas", side_effect=duas):
            r = self.enviar(folha, b"%PDF-lote inteiro")
        self.assertEqual(r.status_code, 422)
        self.assertIn("2 páginas", r.json()["detail"])
        self.assertEqual(list(self.pasta_folhas.iterdir()), [])
        self.assertTrue(antiga.exists())                     # nada mudou
        self.db.expire_all()
        self.assertEqual(self.db.get(Folha, folha.id).estado, "nao_e_folha")

    def test_decisao_humana_exige_forcar(self):
        folha = self.folha()
        linha = self.linha(folha, 1, "quarentena", "900001")
        self.revisar(linha, "900001")
        r = self.enviar(folha)
        self.assertEqual(r.status_code, 409)
        self.assertIn("1 decisão", r.json()["detail"])
        r = self.enviar(folha, forcar=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.contar(Revisao), 0)

    def test_a_conversao_nao_para_o_servidor(self):
        # 20/11: o pdftoppm rodava dentro do laço de eventos, e durante a conversão de um PDF
        # grande o servidor inteiro parava (nem a página inicial carregava). A conversão tem
        # que rodar numa thread, onde não há laço de eventos rodando.
        import asyncio

        def conferir(conteudo, destino, origem):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass                                  # numa thread: o certo
            else:
                raise AssertionError("conversão rodando DENTRO do laço de eventos")
            caminho = Path(destino) / f"{origem}_p1.png"
            caminho.write_bytes(b"png")
            return [caminho]

        folha = self.folha(estado="nao_e_folha")
        with mock.patch.object(folhas.documentos, "paginas", side_effect=conferir):
            r = self.enviar(folha)
            self.assertEqual(r.status_code, 200, r.text)
            r = self.cliente.post("/folhas", files={"arquivo": ("lote.pdf", b"%PDF-lote novo",
                                                                "application/pdf")})
            self.assertIn(r.status_code, (200, 201), r.text)

    def test_folha_sendo_lida_nao_troca(self):
        folha = self.folha(estado="processando")
        self.assertEqual(self.enviar(folha).status_code, 409)


if __name__ == "__main__":
    unittest.main()
