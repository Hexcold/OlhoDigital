"""A61: o segundo passe que acha o marcador atravessado por um risco branco fino.

Folha sintética, 4 marcadores do dicionário da folha, risco desenhado na borda do marcador 3.
O caso real foram as p36 e p47 da coleta (vinco ou falha de toner).
"""
import unittest

import cv2
import numpy as np

import apoio  # noqa: F401  (caminhos e isolamento da bateria)
from unittest import mock

import align
from align import Align, _detectar, detectar_marcadores
from api import pipeline
from api.rotas import folhas
from api.tabelas import Folha
from test_grupos import ComBanco                               # noqa: F401

DIC = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
LADO = 90
POSICOES = {0: (60, 60), 1: (1050, 60), 2: (60, 1450), 3: (1050, 1450)}


def folha(risco: int = 0, sem: tuple = ()):
    """`risco`: largura, em px, de um risco branco que corta a borda direita do marcador 3.
    `sem`: marcadores que não são desenhados (o canto fora da área escaneada)."""
    img = np.full((1600, 1200), 255, np.uint8)
    for i, (x, y) in POSICOES.items():
        if i not in sem:
            img[y:y + LADO, x:x + LADO] = cv2.aruco.generateImageMarker(DIC, i, LADO)
    # conteúdo para comparar alinhamentos: uma grade de traços
    for k in range(200, 1400, 100):
        cv2.line(img, (200, k), (1000, k), 0, 2)
    if risco:
        x, y = POSICOES[3]
        cv2.line(img, (x + LADO - 8, y - 5), (x + LADO - 12, y + LADO + 5), 255, risco)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def primeiro_passe(img):
    return _detectar(cv2.aruco.ArucoDetector(DIC, cv2.aruco.DetectorParameters()), img)


class Interruptor(unittest.TestCase):
    """O estado do interruptor do D29 é fixado em cada teste: ele é global ao processo, e
    `pipeline.preparar` o liga."""

    def setUp(self):
        p = mock.patch.object(Align, "ESTIMAR_QUARTO", False)
        p.start()
        self.addCleanup(p.stop)


class SegundoPasse(Interruptor):
    def test_folha_boa_nao_usa_o_segundo_passe(self):
        img = folha()
        achados, recuperados = detectar_marcadores(img)
        self.assertEqual(sorted(achados), [0, 1, 2, 3])
        self.assertEqual(recuperados, set())
        for i, cantos in primeiro_passe(img).items():
            np.testing.assert_array_equal(achados[i], cantos)

    def test_risco_fino_derruba_o_primeiro_e_o_segundo_recupera(self):
        img = folha(risco=2)
        self.assertNotIn(3, primeiro_passe(img))            # a premissa do teste
        achados, recuperados = detectar_marcadores(img)
        self.assertEqual(sorted(achados), [0, 1, 2, 3])
        self.assertEqual(recuperados, {3})

    def test_so_o_que_faltava_vem_da_imagem_erodida(self):
        img = folha(risco=2)
        achados, _ = detectar_marcadores(img)
        for i, cantos in primeiro_passe(img).items():
            np.testing.assert_array_equal(achados[i], cantos)   # cantos originais intactos
        # o recuperado sai perto de onde o marcador está: a erosão desloca ~1 px
        x, y = POSICOES[3]
        esperado = np.float32([[x, y], [x + LADO, y], [x + LADO, y + LADO], [x, y + LADO]])
        self.assertLess(np.abs(achados[3] - esperado).max(), 3.0)

    def test_risco_largo_nao_e_recuperado(self):
        # o limite: a 3×3 fecha até ~2 px; um 5×5 fecharia mais e deformou marcador bom (A61)
        achados, recuperados = detectar_marcadores(folha(risco=4))
        self.assertNotIn(3, achados)
        self.assertEqual(recuperados, set())

    def test_alinhar_usa_o_segundo_passe_e_falha_sem_ele(self):
        alinhada = Align(600, 800).aruco(folha(risco=2))
        self.assertEqual(alinhada.shape[:2], (800, 600))
        with self.assertRaises(Exception):
            Align(600, 800).aruco(folha(risco=4))

    def test_portao_do_servidor_ve_o_mesmo(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "f.png"
            cv2.imwrite(str(caminho), folha(risco=2))
            self.assertEqual(pipeline.marcadores_encontrados(caminho), {0, 1, 2, 3})
            cv2.imwrite(str(caminho), folha(risco=4))
            self.assertEqual(pipeline.marcadores_encontrados(caminho), {0, 1, 2})


class TresMarcadores(Interruptor):
    """D29: o quarto canto estimado pelos outros três, só com o interruptor ligado."""

    def test_desligado_continua_exigindo_os_quatro(self):
        with self.assertRaises(Exception):
            Align(600, 800).aruco(folha(sem=(0,)))

    def test_ligado_estima_e_alinha_como_com_os_quatro(self):
        Align.ESTIMAR_QUARTO = True
        quatro = Align(600, 800).aruco(folha())
        for falta in (0, 1, 2, 3):
            with self.subTest(falta=falta):
                a = Align(600, 800)
                tres = a.aruco(folha(sem=(falta,)))
                self.assertEqual(a.estimado, falta)
                # folha sintética sem distorção: o paralelogramo é exato, o alinhamento também
                # (a diferença é a do marcador que não foi desenhado, no canto)
                miolo = (slice(100, 700), slice(80, 520))
                self.assertLess(np.abs(tres[miolo].astype(int) - quatro[miolo]).mean(), 1.0)

    def test_com_quatro_nada_e_estimado(self):
        Align.ESTIMAR_QUARTO = True
        a = Align(600, 800)
        a.aruco(folha())
        self.assertIsNone(a.estimado)

    def test_com_dois_continua_falhando(self):
        Align.ESTIMAR_QUARTO = True
        with self.assertRaises(Exception):
            Align(600, 800).aruco(folha(sem=(0, 3)))

    def test_o_servidor_liga(self):
        pipeline.preparar()
        self.assertTrue(Align.ESTIMAR_QUARTO)


class TresMarcadoresNoServidor(ComBanco):
    def ler(self, contagem, achados):
        folha_ = self.folha(estado="recebida")
        recursos = {"ponto": {"alpha": 0.5, "theta": 0.6, "delta": 0.08, "metrica_nome": "x",
                              "tau_nome": 0.65, "recorte": "v3"},
                    "modelo_matricula": "m", "modelo_letras": "l"}
        linha = {"numero": 1, "leitura_matricula": "900001", "leitura_nome": "ZULEICA",
                 "confiancas_matricula": [], "confiancas_nome": [],
                 "motivos_matricula": ["lido"] * 6, "motivos_nome": ["lido"] * 7,
                 "score": 0.9, "margem": 0.5, "decisao": "aceito",
                 "matricula_sugerida": "900001", "candidatos": [], "duplicada_na_folha": False}
        with mock.patch.object(folhas, "sessao", self.Sessao), \
                mock.patch.object(pipeline, "conferir_marcadores", return_value=contagem), \
                mock.patch.object(pipeline, "marcadores_encontrados", return_value=achados), \
                mock.patch.object(pipeline, "carregar_recursos", return_value=recursos), \
                mock.patch.object(pipeline, "processar", return_value=[linha]), \
                mock.patch.object(pipeline, "recortar_para_revisao"), \
                mock.patch.object(folhas, "_vincular_evento"):
            folhas._ler_e_gravar(folha_.id)
        self.db.expire_all()
        return self.db.get(Folha, folha_.id)

    def test_tres_le_e_marca_o_canto_estimado(self):
        f = self.ler(3, {1, 2, 3})
        self.assertEqual(f.estado, "processada")
        self.assertEqual(f.marcador_estimado, 0)
        r = self.cliente.get(f"/folhas/{f.id}")
        self.assertEqual(r.json()["marcador_estimado"], 0)
        self.assertEqual(self.cliente.get(f"/folhas/{f.id}/json").json()["folha"]
                         ["marcador_estimado"], 0)

    def test_quatro_nao_marca(self):
        self.assertIsNone(self.ler(4, {0, 1, 2, 3}).marcador_estimado)

    def test_dois_continua_nao_sendo_folha(self):
        f = self.ler(2, {1, 2})
        self.assertEqual(f.estado, "nao_e_folha")
        self.assertIsNone(f.marcador_estimado)


if __name__ == "__main__":
    unittest.main()
