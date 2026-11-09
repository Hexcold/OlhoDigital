"""Os textos editáveis não podem alcançar nada que o sistema leia."""
import datetime as dt
import json
import random
import threading
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from apoio import ESTADO, EU, gf, lf

CAMPOS = ("titulo", "subtitulo", "complemento", "instrucao")


class Portao(unittest.TestCase):
    def test_folha_dos_encontros_passa(self):
        r = lf.conferir_cabecalho(EU)
        self.assertEqual(r["pixels_fora"], 0)
        self.assertGreater(r["pixels_mudados"], 0)

    def test_um_ponto_em_qualquer_regiao_travada_e_recusado(self):
        L = gf.LAYOUT
        svg = gf.desenhar_svg(cabecalho=EU)
        lado = L.lado_celula
        alvos = {
            "marcador 0": (L.ref_x0 + 3, L.ref_y0 + 3),
            "marcador 1": (L.ref_x0 + L.ref_larg - 6, L.ref_y0 + 3),
            "marcador 2": (L.ref_x0 + 3, L.ref_y0 + L.ref_alt - 6),
            "marcador 3": (L.ref_x0 + L.ref_larg - 6, L.ref_y0 + L.ref_alt - 6),
            "zona de silêncio": (L.ref_x0 + 14, L.ref_y0 + 6),
            "carimbo": (L.ref_x0 + L.ref_larg - 10, L.y_instrucoes + 2.5),
            "rótulo da chave": (L.tab_x0 + 3, L.y_chave - 2.5),
            "rótulo da matrícula": (L.tab_x0 + 5, L.y_rotulos + 3),
            "rótulo do nome": (L.x_nome + 5, L.y_rotulos + 3),
            "rótulo da rubrica": (L.x_rubrica + 5, L.y_rotulos + 3),
            "primeira célula da chave": (L.tab_x0 + 2, L.y_chave + 3),
            "última célula da chave": (L.tab_x0 + 15.5 * lado, L.y_chave + 3),
            "numeração": (L.x_numeracao + 2, L.tab_y0 + 2),
            "matrícula": (L.tab_x0 + 2, L.tab_y0 + 20 * lado + 2),
            "nome": (L.x_nome + 9.5 * lado, L.tab_y0 + 20 * lado + 2),
            "rubrica": (L.x_rubrica + 5, L.tab_y0 + 20 * lado + 2),
            "última linha": (L.x_nome + 2, L.tab_y0 + 32 * lado + 2),
            "abaixo da grade": (100, L.tab_y0 + 33 * lado + 3),
        }
        lf._conferir_svg(svg)        # sem o ponto passa: a recusa abaixo é do ponto
        for nome, (x, y) in alvos.items():
            ponto = f'<rect x="{x}" y="{y}" width="0.5" height="0.5" fill="#777777"/>'
            with self.subTest(regiao=nome):
                with self.assertRaisesRegex(ValueError, "fora das áreas"):
                    lf._conferir_svg(svg.replace("</svg>", ponto + "</svg>"))

    def test_mudar_o_tamanho_da_folha_e_recusado(self):
        svg = gf.desenhar_svg(cabecalho=EU).replace('width="210.0mm"', 'width="200.0mm"')
        with self.assertRaises(ValueError):
            lf._conferir_svg(svg)

    def test_folha_sai_sem_data_e_com_carimbo(self):
        svg = gf.desenhar_svg(cabecalho=EU)
        self.assertNotIn(">DATA<", svg)
        self.assertIn(gf.VERSAO_FOLHA, svg)
        self.assertIn(">DATA<", gf.desenhar_svg())


class Carimbo(unittest.TestCase):
    """O dia do evento entra no carimbo sem destravar o resto (decisão de 09/11/2026)."""

    def test_dia_mes_ano_e_so_v4_sem_dia(self):
        self.assertEqual(gf.texto_carimbo(dt.date(2026, 11, 3)), "v4 · 03/11/2026")
        self.assertEqual(gf.texto_carimbo(None), "v4")

    def test_dias_da_coleta_passam_no_portao(self):
        for dia in (dt.date(2026, 11, 11), dt.date(2026, 11, 12), dt.date(2026, 11, 13), None):
            with self.subTest(dia=dia):
                self.assertEqual(lf.conferir_cabecalho(EU, carimbo=gf.texto_carimbo(dia))["pixels_fora"], 0)

    def test_carimbo_que_sai_da_caixa_e_recusado(self):
        with self.assertRaisesRegex(ValueError, "sai da caixa"):
            lf.conferir_cabecalho(EU, carimbo="v4 · " + "W" * 30)

    def test_ponto_sobre_o_carimbo_com_dia_e_recusado(self):
        carimbo = gf.texto_carimbo(dt.date(2026, 11, 11))
        svg = gf.desenhar_svg(cabecalho=EU, carimbo=carimbo)
        lf._conferir_svg(svg, carimbo=carimbo)
        L = gf.LAYOUT
        ponto = (f'<rect x="{L.ref_x0 + L.ref_larg - 10}" y="{L.y_instrucoes + 2.5}" '
                 'width="0.5" height="0.5" fill="#777777"/>')
        with self.assertRaisesRegex(ValueError, "fora das áreas"):
            lf._conferir_svg(svg.replace("</svg>", ponto + "</svg>"), carimbo=carimbo)

    def test_a_v4_congelada_continua_com_a_data_da_versao(self):
        self.assertIn(gf.VERSAO_FOLHA, gf.desenhar_svg())


class Textos(unittest.TestCase):
    def test_todos_os_campos_chegam_ao_papel(self):
        c = gf.Cabecalho("TITULO X", "SUBTITULO Y", "COMPLEMENTO Z", "INSTRUCAO W")
        svg = gf.desenhar_svg(cabecalho=c)
        for campo in CAMPOS:
            self.assertIn(getattr(c, campo), svg)

    def test_rotulos_continuam_fixos(self):
        svg = gf.desenhar_svg(cabecalho=gf.Cabecalho("X"))
        for rotulo in ("CHAVE", "MATRÍCULA", "NOME E SOBRENOME", "RUBRICA"):
            self.assertIn(f">{rotulo}<", svg)

    def test_instrucao_vazia_some_sem_erro(self):
        self.assertNotIn("USE LETRA", gf.desenhar_svg(cabecalho=gf.Cabecalho(instrucao="")))
        lf.conferir_cabecalho(gf.Cabecalho(instrucao=""))

    def test_texto_comprido_encolhe_ate_o_minimo_e_depois_recusa(self):
        lf.conferir_cabecalho(gf.Cabecalho("ENCONTROS UNIVERSITÁRIOS DA UNIVERSIDADE FEDERAL DO CEARÁ 2026"))
        for campo, valor in (("titulo", "W" * 60), ("subtitulo", "W" * 80), ("complemento", "W" * 90),
                             ("instrucao", "W" * 90)):
            with self.subTest(campo=campo):
                with self.assertRaisesRegex(ValueError, "não cabe"):
                    gf.desenhar_svg(cabecalho=gf.Cabecalho(**{campo: valor}))

    def test_instrucao_no_limite_nao_alcanca_o_carimbo(self):
        for n in range(30, 62, 4):
            c = gf.Cabecalho(instrucao="W" * n)
            try:
                lf.conferir_cabecalho(c)
            except ValueError as erro:
                self.assertIn("não cabe", str(erro))

    def test_acentos_e_cedilha_no_pior_caso(self):
        lf.conferir_cabecalho(gf.Cabecalho("ÁÉÍÓÚ ÂÊÔ ÃÕ ÇÀ", "ÁÉÍÓÚ ÂÊÔ ÃÕ Çgjpqy", "ÁÉÍÓÚ Çgjpqy", "ÁÇÃÕ gjpqy"))

    def test_marcacao_vira_texto_e_nao_desenho(self):
        base = gf.desenhar_svg(cabecalho=EU).count("<rect")
        for ataque in ('<rect width="999" height="999"/>', '</text><rect width="9" height="9"/>',
                       'A&B<C>"D\'', "]]>", "<!-- x -->", "&amp;", "&#65;"):
            c = gf.Cabecalho(ataque, ataque, ataque, ataque)
            with self.subTest(ataque=ataque):
                svg = gf.desenhar_svg(cabecalho=c)
                ET.fromstring(svg)
                self.assertEqual(svg.count("<rect"), base)
                lf.conferir_cabecalho(c)

    def test_caractere_invisivel_ou_de_controle(self):
        for ruim in ("A\x00B", "A\x07B", "A​B", "A\ud800B", "AB"):
            with self.subTest(ruim=repr(ruim)):
                with self.assertRaises(ValueError):
                    gf.desenhar_svg(cabecalho=gf.Cabecalho(ruim))

    def test_espacos_e_quebras_viram_um_espaco(self):
        self.assertIn(">A B C<", gf.desenhar_svg(cabecalho=gf.Cabecalho(" A \n B\t C ")))

    def test_entrada_aleatoria_so_termina_de_dois_jeitos(self):
        """Ou passa pelo portão, ou é ValueError. Exceção de outro tipo vira erro 500."""
        rng = random.Random(2)
        amostra = ["ÁÇ", "gjpq", "😀", "香", "́" * 6, "W", " ", "<&>", "\x07", "١", "⠀"]
        for _ in range(60):
            c = gf.Cabecalho(*("".join(rng.choice(amostra) for _ in range(rng.randint(1, 18)))
                               for _ in CAMPOS))
            try:
                self.assertEqual(lf.conferir_cabecalho(c)["pixels_fora"], 0)
            except ValueError:
                pass


class Persistencia(unittest.TestCase):
    def setUp(self):
        self.arquivo = ESTADO / "persistencia.json"
        self.arquivo.unlink(missing_ok=True)

    def test_ida_e_volta(self):
        c = gf.Cabecalho("Á & <B>", "ç", "😀", "")
        lf.salvar_cabecalho(c, self.arquivo)
        self.assertEqual(lf.carregar_cabecalho(self.arquivo), c)

    def test_sem_arquivo_le_o_versionado(self):
        self.assertTrue(lf.carregar_cabecalho(self.arquivo).titulo.startswith("ENCONTROS"))

    def test_recusado_nao_toca_no_arquivo(self):
        lf.salvar_cabecalho(EU, self.arquivo)
        antes = self.arquivo.read_bytes()
        with self.assertRaises(ValueError):
            lf.salvar_cabecalho(gf.Cabecalho("W" * 60), self.arquivo)
        self.assertEqual(self.arquivo.read_bytes(), antes)

    def test_arquivo_de_versao_antiga_ainda_abre(self):
        """Os rótulos já foram campos: um volume salvo naquela época não pode travar."""
        self.arquivo.write_text(json.dumps({"titulo": "X", "rotulo_nome": "Y"}), encoding="utf-8")
        self.assertEqual(lf.carregar_cabecalho(self.arquivo).titulo, "X")

    def test_arquivo_corrompido_explica_o_problema(self):
        for conteudo in ("{nao é json", "[]", '{"titulo": 1}', ""):
            with self.subTest(conteudo=conteudo):
                self.arquivo.write_text(conteudo, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "cabeçalho"):
                    lf.carregar_cabecalho(self.arquivo)

    def test_salvar_ao_mesmo_tempo_nao_quebra_nem_mistura(self):
        erros = []

        def salvar(i):
            try:
                lf.salvar_cabecalho(gf.Cabecalho(f"T{i}", f"S{i}"), self.arquivo)
            except Exception as e:                     # noqa: BLE001
                erros.append(repr(e))

        linhas = [threading.Thread(target=salvar, args=(i,)) for i in range(8)]
        [t.start() for t in linhas]
        [t.join() for t in linhas]
        self.assertEqual(erros, [])
        final = lf.carregar_cabecalho(self.arquivo)
        self.assertEqual(final.titulo[1:], final.subtitulo[1:])

    def test_previa_nao_grava(self):
        self.assertFalse(self.arquivo.exists())
        png = lf.previa_png(gf.Cabecalho("X"), "BANANA")
        self.assertEqual(png[:4], b"\x89PNG")
        self.assertFalse(self.arquivo.exists())

    def test_previa_e_a_folha_sem_marcacao(self):
        import cv2
        png = cv2.imdecode(np.frombuffer(lf.previa_png(EU), np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertEqual(png.ndim, 2, "a prévia não pode ter cor: é a folha como sai")


if __name__ == "__main__":
    unittest.main()
