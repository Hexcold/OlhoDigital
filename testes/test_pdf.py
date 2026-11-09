"""O PDF é o que vai para a gráfica: página certa, na ordem certa, no tamanho certo."""
import concurrent.futures as cf
import datetime as dt
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from apoio import EU, LENTOS, PIPELINE, gf, info_pdf, lf, texto_pdf

A4 = (595.276, 841.89)


def item(chave, folhas=1, linha=2):
    return lf.ItemLote(linha, chave, folhas)


class Lote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pdf = os.path.join(cls.tmp.name, "lote.pdf")
        cls.resumo = lf.gerar_pdf([item("GOIABA", 3, 2), item("JABUTICABA", 1, 3), item("TAMARINDO", 2, 4)], EU, cls.pdf)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_paginas(self):
        self.assertEqual(info_pdf(self.pdf)["paginas"], 6)
        self.assertEqual((self.resumo["chaves"], self.resumo["paginas"]), (3, 6))

    def test_toda_pagina_e_a4_em_retrato(self):
        tamanhos = info_pdf(self.pdf)["tamanhos"]
        self.assertEqual(len(tamanhos), 1, tamanhos)
        l, a = next(iter(tamanhos))
        self.assertAlmostEqual(l, A4[0], places=1)
        self.assertAlmostEqual(a, A4[1], places=1)

    def test_cada_pagina_tem_a_chave_certa_na_ordem(self):
        esperado = ["GOIABA"] * 3 + ["JABUTICABA"] + ["TAMARINDO"] * 2
        for pagina, chave in enumerate(esperado, start=1):
            with self.subTest(pagina=pagina):
                texto = texto_pdf(self.pdf, pagina)
                self.assertIn(" ".join(chave), texto)
                outras = {"GOIABA", "JABUTICABA", "TAMARINDO"} - {chave}
                for outra in outras:
                    self.assertNotIn(" ".join(outra), texto)

    def test_toda_pagina_tem_cabecalho_e_carimbo_e_nao_tem_data(self):
        for pagina in range(1, 7):
            texto = texto_pdf(self.pdf, pagina)
            with self.subTest(pagina=pagina):
                self.assertIn("ENCONTROS UNIVERSITÁRIOS 2026", texto)
                self.assertIn("FOLHA DE PRESENÇA", texto)
                # sem dia no lote, o carimbo é só a geometria: a data da versão
                # não sai mais no papel (decisão do autor, 09/11/2026)
                self.assertIn(gf.texto_carimbo(), texto)
                self.assertNotIn(gf.VERSAO_FOLHA, texto)
                self.assertNotIn("DATA", texto)

    def test_fontes_embutidas(self):
        """Fonte não embutida muda na máquina da gráfica, e muda a chave impressa."""
        saida = subprocess.run(["pdffonts", self.pdf], capture_output=True, text=True, check=True).stdout
        linhas = saida.splitlines()[2:]
        self.assertTrue(linhas)
        for linha in linhas:
            self.assertRegex(linha, r"\byes\s+yes\b", linha)

    def test_marcadores_em_toda_pagina(self):
        for pagina in (1, 4, 6):
            with tempfile.TemporaryDirectory() as tmp:
                base = os.path.join(tmp, "p")
                subprocess.run(["pdfseparate", "-f", str(pagina), "-l", str(pagina), self.pdf, base + "%d.pdf"], check=True)
                for dpi, ids in gf.conferir_deteccao(f"{base}{pagina}.pdf", (200,)).items():
                    self.assertEqual(sorted(ids), [0, 1, 2, 3])


class CarimboComDia(unittest.TestCase):
    """O dia do evento vai no carimbo, em dia/mês/ano; sem dia, sai só v4."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pdf = os.path.join(cls.tmp.name, "lote.pdf")
        itens = [lf.ItemLote(2, "GOIABA", 1, dia=dt.date(2026, 11, 11)),
                 lf.ItemLote(3, "JABUTICABA", 2, dia=dt.date(2026, 11, 13)),
                 lf.ItemLote(4, "TAMARINDO", 1)]
        with mock.patch.object(lf, "conferir_cabecalho", wraps=lf.conferir_cabecalho) as portao:
            lf.gerar_pdf(itens, EU, cls.pdf)
        cls.portoes = sorted(c.kwargs.get("carimbo") for c in portao.call_args_list)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cada_pagina_com_o_seu_dia(self):
        esperado = ["v4 · 11/11/2026", "v4 · 13/11/2026", "v4 · 13/11/2026", None]
        self.assertEqual(info_pdf(self.pdf)["paginas"], len(esperado))
        for pagina, carimbo in enumerate(esperado, start=1):
            texto = texto_pdf(self.pdf, pagina)
            with self.subTest(pagina=pagina):
                self.assertNotIn(gf.VERSAO_FOLHA, texto)
                if carimbo:
                    self.assertIn(carimbo, texto)
                else:
                    self.assertIn(gf.texto_carimbo(), texto)
                    self.assertNotIn("/2026", texto)

    def test_portao_roda_uma_vez_por_carimbo(self):
        self.assertEqual(self.portoes, ["v4", "v4 · 11/11/2026", "v4 · 13/11/2026"])

    def test_marcadores_em_toda_pagina(self):
        for pagina in range(1, 5):
            with tempfile.TemporaryDirectory() as tmp:
                base = os.path.join(tmp, "p")
                subprocess.run(["pdfseparate", "-f", str(pagina), "-l", str(pagina), self.pdf, base + "%d.pdf"], check=True)
                for dpi, ids in gf.conferir_deteccao(f"{base}{pagina}.pdf", (200,)).items():
                    self.assertEqual(sorted(ids), [0, 1, 2, 3])


class Avulsas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = os.path.join(self.tmp.name, "f.pdf")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uma_e_varias(self):
        for n in (1, 2, 7):
            with self.subTest(n=n):
                self.assertEqual(lf.gerar_avulsas("GOIABA", n, EU, self.pdf)["paginas"], n)
                self.assertEqual(info_pdf(self.pdf)["paginas"], n)

    def test_chave_em_branco_deixa_as_celulas_vazias(self):
        lf.gerar_avulsas("", 2, EU, self.pdf)
        texto = texto_pdf(self.pdf)
        self.assertIn("CHAVE", texto)
        self.assertNotIn("G O I", texto)

    def test_chave_minuscula_sai_em_maiuscula(self):
        lf.gerar_avulsas("goiaba", 1, EU, self.pdf)
        self.assertIn("G O I A B A", texto_pdf(self.pdf))

    def test_recusas(self):
        for chave, folhas in (("SALA2", 1), ("A" * 17, 1), ("GOIABA", 0), ("GOIABA", -1), ("GOIABA", lf.LIMITE_PAGINAS + 1)):
            with self.subTest(chave=chave, folhas=folhas):
                with self.assertRaises(ValueError):
                    lf.gerar_avulsas(chave, folhas, EU, self.pdf)
                self.assertFalse(os.path.exists(self.pdf), "falha não pode deixar PDF pela metade")

    def test_caminho_com_ponto_pdf_no_meio(self):
        pasta = os.path.join(self.tmp.name, "lote.pdf.final")
        os.makedirs(pasta)
        destino = os.path.join(pasta, "folha.pdf")
        gf.gerar(destino, "GOIABA", EU)
        self.assertTrue(os.path.exists(destino))


class Portoes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = os.path.join(self.tmp.name, "f.pdf")

    def tearDown(self):
        self.tmp.cleanup()

    def sem_pdf(self):
        self.assertFalse(os.path.exists(self.pdf))

    def test_alerta_sem_confirmacao_nao_gera(self):
        with self.assertRaises(lf.AlertasPendentes):
            lf.gerar_pdf([item("BANANA", 1, 2), item("BANANO", 1, 3)], EU, self.pdf)
        self.sem_pdf()

    def test_alerta_confirmado_gera(self):
        lf.gerar_pdf([item("BANANA", 1, 2), item("BANANO", 1, 3)], EU, self.pdf, aceitar_alertas=True)
        self.assertEqual(info_pdf(self.pdf)["paginas"], 2)

    def test_erro_nao_gera_nem_confirmado(self):
        for itens in ([item("SALA2")], [item("GOIABA", 0)], [], [item("GOIABA", 1, 2), item("GOIABA", 1, 3)], [item("GOIABA", lf.LIMITE_PAGINAS + 1)]):
            with self.subTest(itens=itens):
                with self.assertRaises(ValueError):
                    lf.gerar_pdf(itens, EU, self.pdf, aceitar_alertas=True)
                self.sem_pdf()

    def test_cabecalho_invalido_nao_gera(self):
        with self.assertRaises(ValueError):
            lf.gerar_pdf([item("GOIABA")], gf.Cabecalho("W" * 60), self.pdf)
        self.sem_pdf()

    def test_marcador_que_nao_detecta_bloqueia_a_folha(self):
        """D10: folha que não detecta não vai para a impressora."""
        with mock.patch.object(gf, "conferir_deteccao", return_value={200: [0, 1, 2]}):
            with self.assertRaisesRegex(ValueError, "não detecta"):
                lf.gerar_pdf([item("GOIABA")], EU, self.pdf)
        self.sem_pdf()

    def test_falha_na_decima_chave_nao_deixa_lote_parcial(self):
        chamadas = {"n": 0}
        original = gf.conferir_deteccao

        def falha_na_terceira(caminho, dpis=(200,)):
            chamadas["n"] += 1
            return {200: []} if chamadas["n"] == 3 else original(caminho, (200,))

        itens = [item(c, 1, i + 2) for i, c in enumerate(("GOIABA", "JABUTICABA", "TAMARINDO", "PEQUI"))]
        with mock.patch.object(gf, "conferir_deteccao", side_effect=falha_na_terceira):
            with self.assertRaises(ValueError):
                lf.gerar_pdf(itens, EU, self.pdf)
        self.sem_pdf()


class Reproducao(unittest.TestCase):
    def test_mesma_entrada_mesmo_desenho(self):
        self.assertEqual(gf.desenhar_svg(chave="GOIABA", cabecalho=EU), gf.desenhar_svg(chave="GOIABA", cabecalho=EU))

    def test_geracao_simultanea_nao_mistura_chaves(self):
        """A API roda rotas em threads. Duas pessoas gerando ao mesmo tempo é o caso."""
        chaves = ["GOIABA", "JABUTICABA", "TAMARINDO", "PEQUI", "BURITI", "MANGABA"]

        def gerar(chave):
            with tempfile.TemporaryDirectory() as tmp:
                pdf = os.path.join(tmp, "f.pdf")
                lf.gerar_avulsas(chave, 2, EU, pdf)
                return chave, info_pdf(pdf)["paginas"], texto_pdf(pdf)

        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for chave, paginas, texto in ex.map(gerar, chaves):
                with self.subTest(chave=chave):
                    self.assertEqual(paginas, 2)
                    self.assertIn(" ".join(chave), texto)
                    for outra in set(chaves) - {chave}:
                        self.assertNotIn(" ".join(outra), texto)


class LinhaDeComando(unittest.TestCase):
    def test_gerar_folha_com_chave(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "f.pdf")
            r = subprocess.run([sys.executable, "-W", "ignore", "gerar_folha.py", pdf, "goiaba"], cwd=PIPELINE, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertIn("SIM", r.stdout)
            self.assertIn(">DATA<", open(pdf.replace(".pdf", ".svg"), encoding="utf-8").read(), "a linha de comando é a v4 congelada")

    def test_chave_invalida_aborta(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-W", "ignore", "gerar_folha.py", os.path.join(tmp, "f.pdf"), "SALA2"], cwd=PIPELINE, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


@unittest.skipUnless(LENTOS, "defina OLHODIGITAL_TESTES_LENTOS=1")
class Escala(unittest.TestCase):
    def medir(self, itens, limite_s):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "f.pdf")
            t = time.perf_counter()
            r = lf.gerar_pdf(itens, EU, pdf, aceitar_alertas=True)
            duracao = time.perf_counter() - t
            print(f"\n  {r['chaves']} chaves, {r['paginas']} páginas: {duracao:.1f} s, {r['bytes'] / 1e6:.1f} MB")
            self.assertEqual(info_pdf(pdf)["paginas"], r["paginas"])
            self.assertLess(duracao, limite_s)

    def test_coleta_dos_encontros(self):
        import itertools, string
        chaves = ["".join(p) for p in itertools.islice(itertools.permutations("BDFGKLMNPQRSTZ", 7), 0, 40 * 997, 997)]
        self.medir([item(c, 13, i + 2) for i, c in enumerate(chaves)], 180)

    def test_limite_de_paginas(self):
        self.medir([item("GOIABA", lf.LIMITE_PAGINAS)], 120)


if __name__ == "__main__":
    unittest.main()
