"""A folha impressa e o cortador têm que concordar: é disso que a leitura depende."""
import hashlib
import unittest

import numpy as np

from apoio import EU, V4, alinhar, cortador, degradar, gf, lf, px

# sha256 de `dados/folha_v4.svg`. A v4 é congelada (D11): mudar este número é
# mudar a folha que foi medida, e isso nunca pode acontecer sem querer.
SHA_V4 = "dbdd74abfb9ee9fd2a908ec4ca15c06742583e85f00b8bc4a786e2ef90535631"


class FolhaCongelada(unittest.TestCase):
    def test_svg_da_v4_nao_mudou(self):
        self.assertEqual(hashlib.sha256(gf.desenhar_svg().encode()).hexdigest(), SHA_V4)

    def test_numeros_da_folha(self):
        L = gf.LAYOUT
        self.assertEqual((L.linhas, gf.CELULAS_MATRICULA, gf.CELULAS_NOME,
                          gf.CELULAS_CHAVE), (33, 6, 18, 16))
        self.assertAlmostEqual(L.lado_celula, 6.734, places=3)

    def test_fracoes_da_folha_batem_com_as_do_cortador(self):
        L = gf.LAYOUT
        for campo, folha in (("matricula", L.coluna_matricula), ("nome", L.coluna_nome)):
            cortador_ = V4.coluna(campo)
            for chave in ("columns", "x_begin", "x_end"):
                with self.subTest(campo=campo, chave=chave):
                    self.assertAlmostEqual(folha[chave], cortador_[chave], places=6)
        self.assertEqual(L.folha["lines"], V4.linhas)
        self.assertAlmostEqual(L.folha["y_begin"], V4.y_begin, places=6)
        self.assertAlmostEqual(L.folha["y_end"], V4.y_end, places=6)

    def test_banda_da_chave_bate_com_o_cortador(self):
        L, banda = gf.LAYOUT, V4.banda("chave")
        self.assertEqual(banda["columns"], gf.CELULAS_CHAVE)
        self.assertAlmostEqual(banda["x_end"], L._fx(L.tab_x0 + gf.CELULAS_CHAVE * L.lado_celula), places=6)
        self.assertAlmostEqual(banda["y_begin"], L._fy(L.y_chave), places=6)
        self.assertAlmostEqual(banda["y_end"], L._fy(L.y_chave + L.lado_celula), places=6)


def folha_com_marcas() -> str:
    """A folha dos Encontros com um quadrado de 2 mm no centro de cada célula."""
    L, lado, partes = gf.LAYOUT, gf.LAYOUT.lado_celula, []

    def marca(cx: float, cy: float) -> None:
        partes.append(f'<rect x="{cx - 1:.3f}" y="{cy - 1:.3f}" width="2" height="2" '
                      f'fill="#000000"/>')

    for i in range(L.linhas):
        cy = L.tab_y0 + (i + 0.5) * lado
        for j in range(gf.CELULAS_MATRICULA):
            marca(L.tab_x0 + (j + 0.5) * lado, cy)
        for j in range(gf.CELULAS_NOME):
            marca(L.x_nome + (j + 0.5) * lado, cy)
    for k in range(gf.CELULAS_CHAVE):
        marca(L.tab_x0 + (k + 0.5) * lado, L.y_chave + lado / 2)
    return gf.desenhar_svg(cabecalho=EU).replace("</svg>", "".join(partes) + "</svg>")


def desvios(cinza: np.ndarray) -> list[float]:
    """Distância, em px da imagem retificada, entre cada marca e o centro da célula."""
    alinhada = lf_imagem(alinhar(cinza))
    corte = cortador()
    celulas = []
    for campo in ("matricula", "nome"):
        linhas, _ = corte.cut_info(alinhada, V4, campo)
        celulas += [c for linha in linhas for c in linha]
    celulas += corte.cut_banda(alinhada, V4, "chave")

    saida = []
    for celula in celulas:
        dados = celula.data
        altura, largura = dados.shape[:2]
        miolo = dados[4:altura - 4, 4:largura - 4]
        ys, xs = np.nonzero(miolo < 120)
        if xs.size == 0:
            saida.append(float("inf"))           # marca fora da célula recortada
            continue
        dx = xs.mean() + 4 - (largura - 1) / 2
        dy = ys.mean() + 4 - (altura - 1) / 2
        saida.append(float(np.hypot(dx, dy)))
    return saida


def lf_imagem(dados):
    from image import Image
    return Image().set_data(dados)


class CorteCaiNoCentroDaCelula(unittest.TestCase):
    """São 808 células: uma descasada por 1 mm já leria o vizinho."""
    LIMITE_PX = 4.0        # cerca de 0,6 mm na imagem retificada

    def conferir(self, cinza, rotulo):
        medidos = desvios(cinza)
        self.assertEqual(len(medidos), 33 * 24 + 16)
        self.assertLess(max(medidos), self.LIMITE_PX, f"{rotulo}: pior desvio {max(medidos):.2f} px")

    def test_em_cada_resolucao(self):
        svg = folha_com_marcas()
        for dpi in (150, 200, 300):
            with self.subTest(dpi=dpi):
                self.conferir(lf._raster(svg, dpi), f"{dpi} dpi")

    def test_com_a_folha_torta_e_reduzida(self):
        base = lf._raster(folha_com_marcas(), 200)
        for angulo, escala in ((1.5, 1.0), (-1.5, 1.0), (2.5, 1.0), (-2.5, 1.0),
                               (0, 0.96), (2.0, 0.96), (3.0, 0.96)):
            with self.subTest(angulo=angulo, escala=escala):
                self.conferir(degradar(base, angulo=angulo, escala=escala),
                              f"giro {angulo} escala {escala}")

    def test_giro_demais_falha_alto(self):
        """A 3 graus o canto sai da imagem: tem que ser erro, nunca recorte torto."""
        base = lf._raster(gf.desenhar_svg(cabecalho=EU), 200)
        with self.assertRaisesRegex(Exception, "ArUcos"):
            alinhar(degradar(base, angulo=3.0))


class MargemDaImpressora(unittest.TestCase):
    """Tinta dentro da área morta some no papel, e os marcadores ficam a 7,35 mm."""

    def conferir(self, cabecalho):
        cinza = lf._raster(gf.desenhar_svg(chave="WWWWWWWWWWWWWWWW", cabecalho=cabecalho), 300)
        ys, xs = np.nonzero(cinza < 200)
        altura, largura = cinza.shape
        for nome, folga in (("esquerda", xs.min()), ("direita", largura - 1 - xs.max()),
                            ("topo", ys.min()), ("base", altura - 1 - ys.max())):
            with self.subTest(borda=nome):
                self.assertGreaterEqual(folga, px(gf.AREA_MORTA_MM, 300))

    def test_folha_dos_encontros(self):
        self.conferir(EU)

    def test_com_texto_no_limite(self):
        self.conferir(gf.Cabecalho("Á" * 40, "Á" * 60, "Á" * 70, "W" * 60))


if __name__ == "__main__":
    unittest.main()
