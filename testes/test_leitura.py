"""O que a folha imprime tem que voltar pela mesma leitura que a coleta vai usar."""
import string
import unittest

from apoio import EU, PIPELINE, cortador, degradar, gf, lf
from casar_chave import casar_chave

TEM_MODELO = (PIPELINE / "modelo/cnn_emnist_maiusculas_28x28.keras").exists()
CHAVES = ["GOIABA", "BANANA", "JABUTICABA", "TAMARINDO", "GRUMIXAMA", "PEQUI"]


@unittest.skipUnless(TEM_MODELO, "modelo de letras ausente")
class LeituraDaChave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from identificar_folha import CORTE_CNN, ler_chave
        from image import Image
        from ocr import OCRCNN
        from apoio import V4, alinhar
        cls.ocr = OCRCNN.de_letras()

        def ler(cinza):
            alinhada = Image().set_data(alinhar(cinza))
            return ler_chave(alinhada, cortador(), V4, cls.ocr, CORTE_CNN)
        cls.ler = staticmethod(ler)

    def imagem(self, chave, cabecalho=EU, dpi=200):
        return lf._raster(gf.desenhar_svg(chave=chave, cabecalho=cabecalho), dpi)

    def conferir(self, lida, certa):
        self.assertEqual(len(lida), len(certa), f"{lida!r} contra {certa!r}")
        for a, b in zip(lida, certa):
            aceitas = ("?", b, "W") if b == lf.LETRA_LIDA_ERRADA else ("?", b)
            self.assertIn(a, aceitas, f"letra errada em {lida!r}, esperado {certa!r}")

    def test_todas_as_letras_em_todas_as_celulas(self):
        """Letra errada é o que manda folha para o evento errado. Abster é aceitável."""
        alfabeto = string.ascii_uppercase
        for inicio in range(0, 26, 3):
            chave = "".join(alfabeto[(inicio + i) % 26] for i in range(16))
            with self.subTest(chave=chave):
                self.conferir(self.ler(self.imagem(chave)).texto, chave)

    def test_chave_em_branco_le_vazio(self):
        chave = self.ler(self.imagem(""))
        self.assertEqual((chave.texto, chave.em_branco), ("", True))

    def test_a_ultima_celula_e_lida(self):
        self.conferir(self.ler(self.imagem("B" * 16)).texto, "B" * 16)
        self.assertEqual(self.ler(self.imagem("B" * 16)).texto[-1], "B")

    def test_letras_seguras_saem_certas_em_qualquer_posicao(self):
        for letra in sorted(lf.LETRAS_SEGURAS):
            with self.subTest(letra=letra):
                lida = self.ler(self.imagem(letra * 16)).texto
                self.assertEqual(lida, letra * 16)

    def test_v_impresso_nao_liga_a_folha_a_outro_evento(self):
        """V vira W ou `?`: o gate manda para a bandeja, ou acerta, mas não erra."""
        chaves = ["VELEIRO", "DEVER", "TAMARINDO", "GOIABA"]
        for chave in ("VELEIRO", "DEVER"):
            with self.subTest(chave=chave):
                r = casar_chave(self.ler(self.imagem(chave)).texto, chaves)
                self.assertIn(r.chave, (chave, None))

    def test_cabecalho_nao_muda_a_leitura(self):
        base = self.ler(self.imagem("TAMARINDO")).texto
        for c in (gf.Cabecalho("X"), gf.Cabecalho("Á" * 40, "Á" * 60, "Á" * 70, "W" * 60)):
            self.assertEqual(self.ler(self.imagem("TAMARINDO", c)).texto, base)

    def test_marcas_de_impressao_e_escaner(self):
        """Simulação, não papel: giro, redução de impressora, ruído, borrão e JPEG."""
        condicoes = [dict(angulo=a, escala=e, ruido=r, borrar=b, jpeg=j) for a, e, r, b, j in (
            (0, 1, 0, 3, 0), (0, 1, 6, 0, 60), (1, 1, 6, 3, 70), (-2, 1, 4, 3, 70),
            (0, .96, 6, 3, 70), (2, .97, 8, 3, 60), (0, 1, 0, 5, 50), (3, .98, 0, 3, 80))]
        erradas = casos = 0
        for condicao in condicoes:
            for chave in CHAVES:
                with self.subTest(chave=chave, **condicao):
                    lida = self.ler(degradar(self.imagem(chave), **condicao)).texto
                    casos += 1
                    resultado = casar_chave(lida, CHAVES)
                    self.assertIn(resultado.chave, (chave, None), f"foi para o evento errado: {lida!r}")
                    erradas += resultado.chave is None
        print(f"\n  simulação: {casos} leituras, {casos - erradas} ligadas, {erradas} na bandeja")

    def test_outro_dpi_de_escaner(self):
        for dpi in (150, 300, 400):
            with self.subTest(dpi=dpi):
                lida = self.ler(self.imagem("TAMARINDO", dpi=dpi)).texto
                self.assertIn(casar_chave(lida, CHAVES).chave, ("TAMARINDO", None))


if __name__ == "__main__":
    unittest.main()
