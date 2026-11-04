"""Evento errado não aparece em nenhuma métrica: o gate não pode errar calado."""
import random
import string
import unittest

from apoio import gf                                                # noqa: F401
from casar_chave import MINIMO_LETRAS, MOTIVOS, casar_chave
from fuzzy import dist_levenshtein


class Regras(unittest.TestCase):
    CHAVES = ["BANANA", "GOIABA", "CACAU", "PITANGA", "PITAYA", "TAMARINDO"]

    def test_tabela(self):
        for lida, chave, motivo in (
                ("BANANA", "BANANA", "unica"), ("BA?ANA", "BANANA", "unica"),
                ("PITA?A", "PITAYA", "unica"), ("TAMARIND?", "TAMARINDO", "unica"),
                ("?A?A?", None, "poucas_letras"), ("JABUTICABA", None, "sem_candidata"),
                ("GOIABB", None, "sem_candidata"), ("BANAN", None, "sem_candidata"),
                ("BANANAS", None, "sem_candidata"), ("", None, "em_branco"), (None, None, "sem_banda"),
                ("??????", None, "poucas_letras")):
            with self.subTest(lida=lida):
                r = casar_chave(lida, self.CHAVES)
                self.assertEqual((r.chave, r.motivo), (chave, motivo))

    def test_empate(self):
        self.assertEqual(casar_chave("BA?ANA", ["BANANA", "BACANA"]).motivo, "empate")

    def test_sem_eventos(self):
        self.assertEqual(casar_chave("BANANA", []).motivo, "sem_eventos")

    def test_todo_motivo_tem_explicacao(self):
        for lida in ("BANANA", "?A?A?", "ZZZZZZ", "", None):
            self.assertIn(casar_chave(lida, self.CHAVES).motivo, MOTIVOS)

    def test_sugestao_vem_ordenada(self):
        r = casar_chave("GOIABB", self.CHAVES)
        distancias = [d for _, d in r.candidatas]
        self.assertEqual(distancias, sorted(distancias))
        self.assertEqual(r.candidatas[0], ("GOIABA", 1))


class Propriedades(unittest.TestCase):
    """Simula a leitura: letras certas, com até 3 `?`, que é o pior caso medido."""

    @staticmethod
    def chaves_esparsas(rng, n):
        """`n` chaves a distância >= 4 entre si: o que o alerta da planilha pede."""
        saida = []
        while len(saida) < n:
            k = "".join(rng.choice(string.ascii_uppercase) for _ in range(rng.randint(5, 12)))
            if all(dist_levenshtein(k, o) >= 4 for o in saida):
                saida.append(k)
        return saida

    @staticmethod
    def ler(rng, chave, maximo=3):
        letras = list(chave)
        for i in rng.sample(range(len(letras)), rng.randint(0, min(maximo, len(letras)))):
            letras[i] = "?"
        return "".join(letras)

    def test_chave_cadastrada_nunca_vai_para_outro_evento(self):
        rng = random.Random(3)
        for _ in range(300):
            chaves = self.chaves_esparsas(rng, 40)
            certa = rng.choice(chaves)
            r = casar_chave(self.ler(rng, certa), chaves)
            self.assertIn(r.chave, (certa, None))

    def test_chave_fora_do_cadastro_nunca_gruda_em_outra(self):
        rng = random.Random(4)
        for _ in range(300):
            chaves = self.chaves_esparsas(rng, 41)
            fora = chaves.pop()
            self.assertIsNone(casar_chave(self.ler(rng, fora), chaves).chave)

    def test_com_distancia_minima_a_chave_certa_sempre_casa(self):
        rng = random.Random(5)
        for _ in range(300):
            chaves = self.chaves_esparsas(rng, 40)
            certa = rng.choice(chaves)
            lida = self.ler(rng, certa)
            if len(lida) - lida.count("?") >= MINIMO_LETRAS:
                self.assertEqual(casar_chave(lida, chaves).chave, certa)

    def test_uma_letra_errada_nunca_e_aceita_sozinha(self):
        rng = random.Random(6)
        for _ in range(300):
            chaves = self.chaves_esparsas(rng, 40)
            certa = rng.choice(chaves)
            i = rng.randrange(len(certa))
            trocada = rng.choice([c for c in string.ascii_uppercase if c != certa[i]])
            r = casar_chave(certa[:i] + trocada + certa[i + 1:], chaves)
            self.assertNotEqual(r.chave, certa)

    def test_aceito_e_sempre_distancia_zero_e_unico(self):
        rng = random.Random(7)
        for _ in range(500):
            chaves = ["".join(rng.choice("ABCD") for _ in range(rng.randint(3, 6))) for _ in range(rng.randint(1, 12))]
            chaves = sorted(set(chaves))
            lida = "".join(rng.choice("ABCD?") for _ in range(rng.randint(1, 7)))
            r = casar_chave(lida, chaves)
            if r.chave is not None:
                zeros = [k for k in chaves if dist_levenshtein(lida, k) == 0]
                self.assertEqual(zeros, [r.chave])
                self.assertGreaterEqual(len(lida) - lida.count("?"), MINIMO_LETRAS)


if __name__ == "__main__":
    unittest.main()
