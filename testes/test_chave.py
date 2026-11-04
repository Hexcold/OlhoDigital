"""A chave vai para o papel uma vez só: errar aqui custa a pilha inteira."""
import random
import re
import string
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from apoio import EU, gf, lf, px


class ValidarChave(unittest.TestCase):
    def test_aceita(self):
        for entrada, saida in (("BANANA", "BANANA"), ("banana", "BANANA"),
                               ("  Banana\t", "BANANA"), ("A", "A"), ("", ""), ("   ", ""),
                               ("A" * 16, "A" * 16)):
            with self.subTest(entrada=entrada):
                self.assertEqual(gf.validar_chave(entrada), saida)

    def test_recusa(self):
        for entrada in ("A" * 17, "SALA2", "2", "SALA 2", "BANANA-1", "BANANA.", "MAÇÃ",
                        "CAFÉ", "Ñ", "ÉRICA", "БАНАН", "香蕉", "<B>", "A&B", "A\nB",
                        "A\tB", "A\x00B", "'; DROP TABLE evento;--"):
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValueError):
                    gf.validar_chave(entrada)

    def test_nao_transforma_letra_estrangeira_em_outra_chave(self):
        """`upper()` vira ß em SS e ﬁ em FI: o que sai no papel não seria o digitado."""
        for entrada in ("straße", "ﬁm", "ſol", "ıma"):
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValueError):
                    gf.validar_chave(entrada)

    def test_entrada_aleatoria_nunca_quebra_nem_escapa_do_alfabeto(self):
        rng = random.Random(1)
        alfabeto = string.ascii_letters + string.digits + " \t\n.,;:-_<>&'\"ÇÁÉßﬁ€😀\x00"
        aceitas = 0
        for _ in range(5000):
            entrada = "".join(rng.choice(alfabeto) for _ in range(rng.randint(0, 20)))
            try:
                saida = gf.validar_chave(entrada)
            except ValueError:
                continue
            aceitas += 1
            self.assertRegex(saida, r"^[A-Z]{0,16}$")
        self.assertGreater(aceitas, 0)


class ChaveNaBanda(unittest.TestCase):
    DPI = 300

    @classmethod
    def setUpClass(cls):
        cls.sem_chave = lf._raster(gf.desenhar_svg(cabecalho=EU), cls.DPI)
        L = gf.LAYOUT
        cls.x0, cls.y0 = L.tab_x0, L.y_chave
        cls.lado = L.lado_celula

    def diferenca(self, chave):
        return lf._raster(gf.desenhar_svg(chave=chave, cabecalho=EU), self.DPI) != self.sem_chave

    def caixa(self, k):
        x0 = px(self.x0 + k * self.lado, self.DPI)
        return x0, px(self.y0, self.DPI), px(self.x0 + (k + 1) * self.lado, self.DPI), px(self.y0 + self.lado, self.DPI)

    def test_chave_so_muda_a_banda(self):
        mudou = self.diferenca("ABCDEFGHIJKLMNOP").copy()
        x0, y0, _, y1 = self.caixa(0)
        x1 = self.caixa(15)[2]
        mudou[y0:y1, x0:x1] = False
        self.assertEqual(int(mudou.sum()), 0)

    def test_cada_letra_fica_dentro_da_sua_celula(self):
        """Letra que encosta na grade é cortada pelo `cut_borders` e lida errada."""
        for letra in string.ascii_uppercase:
            mudou = self.diferenca(letra * 16)
            for k in (0, 7, 15):
                with self.subTest(letra=letra, celula=k):
                    x0, y0, x1, y1 = self.caixa(k)
                    ys, xs = np.nonzero(mudou[y0:y1, x0:x1])
                    self.assertGreater(xs.size, 0)
                    # 4 px a 300 dpi: mais que os 2 px do corte de borda a 166 dpi
                    self.assertGreaterEqual(xs.min(), 4, "encosta à esquerda")
                    self.assertLessEqual(xs.max(), x1 - x0 - 5, "encosta à direita")
                    self.assertGreaterEqual(ys.min(), 4, "encosta em cima")
                    self.assertLessEqual(ys.max(), y1 - y0 - 5, "encosta embaixo")

    def test_chave_curta_deixa_as_outras_celulas_vazias(self):
        mudou = self.diferenca("ABC")
        for k in range(3, 16):
            x0, y0, x1, y1 = self.caixa(k)
            self.assertEqual(int(mudou[y0:y1, x0:x1].sum()), 0)

    def test_svg_continua_valido_para_qualquer_chave(self):
        for chave in ("", "A", "Z" * 16, string.ascii_uppercase[:16]):
            ET.fromstring(gf.desenhar_svg(chave=chave, cabecalho=EU))


if __name__ == "__main__":
    unittest.main()
