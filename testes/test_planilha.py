"""A planilha vem de fora, feita à mão: o erro tem que dizer a linha, não quebrar."""
import datetime as dt
import io
import time
import unittest

from openpyxl import Workbook

from apoio import lf


def xlsx(*abas, titulos=None) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for i, linhas in enumerate(abas):
        ws = wb.create_sheet((titulos or {}).get(i, f"aba{i}"))
        for linha in linhas:
            ws.append(linha)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def csv(texto: str, codificacao="utf-8") -> bytes:
    return texto.encode(codificacao)


class Formatos(unittest.TestCase):
    def conferir(self, lote, esperado):
        self.assertEqual(lote.erros, [])
        self.assertEqual([(i.chave, i.folhas) for i in lote.itens], esperado)

    def test_xlsx_simples(self):
        self.conferir(lf.ler_planilha("a.xlsx", xlsx([["chave", "folhas"], ["BANANA", 3], ["GOIABA", 2]])),
                      [("BANANA", 3), ("GOIABA", 2)])

    def test_extensao_em_maiuscula(self):
        self.conferir(lf.ler_planilha("A.XLSX", xlsx([["chave", "folhas"], ["BANANA", 3]])), [("BANANA", 3)])
        self.conferir(lf.ler_planilha("A.CSV", csv("chave,folhas\nBANANA,3\n")), [("BANANA", 3)])

    def test_csv_com_cada_separador(self):
        for sep in (",", ";", "\t"):
            with self.subTest(sep=repr(sep)):
                self.conferir(lf.ler_planilha("a.csv", csv(f"chave{sep}folhas\nBANANA{sep}3\n")), [("BANANA", 3)])

    def test_csv_do_excel_em_portugues(self):
        texto = "﻿Chave;Nº de folhas;Local\r\nBANANA;3;Auditório\r\n"
        self.conferir(lf.ler_planilha("a.csv", csv(texto)), [("BANANA", 3)])

    def test_csv_em_latin1(self):
        lote = lf.ler_planilha("a.csv", csv("Chave;Folhas;Sala\nBANANA;3;Auditório\n", "latin-1"))
        self.assertEqual(lote.itens[0].local, "Auditório")

    def test_csv_com_aspas(self):
        self.conferir(lf.ler_planilha("a.csv", csv('"chave","folhas"\n"BANANA","3"\n')), [("BANANA", 3)])

    def test_sinonimos_do_cabecalho(self):
        for chave, folhas in (("CHAVE", "FOLHAS"), ("Chave", "Quantidade"), (" chave ", " qtd "),
                              ("chaves", "Qtde."), ("Chave do evento", "Nº de folhas"),
                              ("CHAVE", "Quantidade de folhas"), ("chave", "Total de folhas")):
            with self.subTest(chave=chave, folhas=folhas):
                self.conferir(lf.ler_planilha("a.xlsx", xlsx([[chave, folhas], ["BANANA", 3]])), [("BANANA", 3)])

    def test_colunas_em_qualquer_ordem_e_com_sobra(self):
        lote = lf.ler_planilha("a.xlsx", xlsx([["id", "responsavel", "folhas", "x", "chave", "local", "nome"],
                                               [1, "Ana", 3, "y", "BANANA", "Sala 1", "Abertura"]]))
        self.conferir(lote, [("BANANA", 3)])
        item = lote.itens[0]
        self.assertEqual((item.nome, item.local, item.responsavel), ("Abertura", "Sala 1", "Ana"))

    def test_titulo_acima_do_cabecalho(self):
        lote = lf.ler_planilha("a.xlsx", xlsx([["Programação dos Encontros"], [], ["chave", "folhas"], ["BANANA", 3]]))
        self.conferir(lote, [("BANANA", 3)])
        self.assertEqual(lote.itens[0].linha, 4, "a linha informada é a da planilha")

    def test_linhas_vazias_no_meio_e_no_fim(self):
        self.conferir(lf.ler_planilha("a.xlsx", xlsx([["chave", "folhas"], ["BANANA", 3], [None, None], ["GOIABA", 2], [None, None], ["  ", ""]])),
                      [("BANANA", 3), ("GOIABA", 2)])

    def test_numero_em_cada_forma(self):
        for valor in (3, 3.0, "3", " 3 "):
            with self.subTest(valor=repr(valor)):
                self.conferir(lf.ler_planilha("a.xlsx", xlsx([["chave", "folhas"], ["BANANA", valor]])), [("BANANA", 3)])

    def test_chave_minuscula_e_com_espaco(self):
        self.conferir(lf.ler_planilha("a.csv", csv("chave,folhas\n  banana ,3\n")), [("BANANA", 3)])

    def test_aba_certa_entre_varias(self):
        self.conferir(lf.ler_planilha("a.xlsx", xlsx([["outra", "coisa"], [1, 2]], [["chave", "folhas"], ["BANANA", 3]])), [("BANANA", 3)])

    def test_planilha_modelo_e_valida_e_sem_alerta(self):
        lote = lf.ler_planilha("m.xlsx", lf.planilha_modelo())
        self.assertEqual((lote.erros, lote.alertas), ([], []))

    def test_sem_a_coluna_folhas_sai_uma_por_chave(self):
        lote = lf.ler_planilha("a.csv", csv("chave\nBANANA\n\n  \nGOIABA\n"))
        self.conferir(lote, [("BANANA", 1), ("GOIABA", 1)])
        self.assertEqual(lote.alertas[0], lf.SEM_FOLHAS, "o padrão tem que pedir confirmação")

    def test_titulo_com_a_palavra_chave_nao_esconde_as_folhas(self):
        self.conferir(lf.ler_planilha("a.xlsx", xlsx([["Chave do evento"], ["chave", "folhas"], ["BANANA", 3]])),
                      [("BANANA", 3)])

    def test_aba_com_folhas_vence_aba_so_com_chave(self):
        self.conferir(lf.ler_planilha("a.xlsx", xlsx([["chave"], ["GOIABA"]], [["chave", "folhas"], ["BANANA", 3]])),
                      [("BANANA", 3)])

    def test_dia_em_cada_forma(self):
        lote = lf.ler_planilha("a.csv", csv("chave;dia\nBANANA;11/11/2026\nGOIABA;2026-11-12\nMANGA;\nCAJU;amanhã\n"))
        self.assertEqual([(i.chave, i.dia) for i in lote.itens],
                         [("BANANA", dt.date(2026, 11, 11)), ("GOIABA", dt.date(2026, 11, 12)), ("MANGA", None)])
        self.assertEqual(len(lote.erros), 1, lote.erros)
        self.assertIn("linha 5: dia inválido", lote.erros[0])


class Recusas(unittest.TestCase):
    def linha_com_erro(self, chave, folhas):
        return lf.ler_planilha("a.xlsx", xlsx([["chave", "folhas"], [chave, folhas]]))

    def test_chave_ruim_vira_erro_com_a_linha(self):
        for chave in ("SALA2", "MAÇÃ", "A B", "A" * 17, 123, dt.date(2026, 11, 5)):
            with self.subTest(chave=chave):
                lote = self.linha_com_erro(chave, 3)
                self.assertEqual(lote.itens, [])
                self.assertTrue(lote.erros and "linha 2" in lote.erros[0], lote.erros)

    def test_quantidade_ruim_vira_erro_com_a_linha(self):
        for folhas in (0, -1, 1.5, "abc", "12 folhas", "1.000", "²", "", None, True, dt.date(2026, 11, 5)):
            with self.subTest(folhas=repr(folhas)):
                lote = self.linha_com_erro("BANANA", folhas)
                self.assertEqual(lote.itens, [])
                self.assertTrue(lote.erros and "linha 2" in lote.erros[0], lote.erros)

    def test_repetida_mesmo_com_caixa_diferente(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\nbanana,1\nBANANA,2\n"))
        self.assertTrue(any("repete" in e for e in lote.erros), lote.erros)

    def test_quantidade_sem_chave(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\n,4\n"))
        self.assertIn("não tem chave", lote.erros[0])

    def test_so_o_cabecalho(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\n"))
        self.assertEqual(lote.itens, [])
        self.assertTrue(lote.erros)

    def test_acumula_todos_os_erros_de_uma_vez(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\nSALA2,1\nGOIABA,0\nMANGA,x\n"))
        self.assertEqual(len(lote.erros), 3)

    def test_arquivo_que_nao_e_planilha(self):
        for nome, conteudo in (("a.xlsx", b"nao sou zip"), ("a.xlsx", b""), ("a.csv", b""),
                               ("a.csv", b"\x00\xff\xfe\x00"), ("a.csv", b"oi"),
                               ("a.xls", b"\xd0\xcf\x11"), ("a.ods", b"x"), ("a.txt", b"chave,folhas"),
                               ("semextensao", b"chave,folhas"), ("", b"x")):
            with self.subTest(nome=nome, conteudo=conteudo[:8]):
                with self.assertRaises(ValueError):
                    lf.ler_planilha(nome, conteudo)

    def test_zip_que_nao_e_xlsx(self):
        import zipfile
        saida = io.BytesIO()
        with zipfile.ZipFile(saida, "w") as z:
            z.writestr("oi.txt", "oi")
        with self.assertRaises(ValueError):
            lf.ler_planilha("a.xlsx", saida.getvalue())

    def test_duas_abas_com_as_colunas_e_erro(self):
        with self.assertRaisesRegex(ValueError, "mais de uma aba"):
            lf.ler_planilha("a.xlsx", xlsx([["chave", "folhas"], ["A", 1]], [["chave", "folhas"], ["B", 1]]))

    def test_sem_a_coluna_chave(self):
        with self.assertRaisesRegex(ValueError, "não achei"):
            lf.ler_planilha("a.xlsx", xlsx([["folhas", "nome"], [3, "BANANA"]]))


class Corte(unittest.TestCase):
    """`ate_linha` é a última linha que entra, contada como o Excel mostra."""

    def test_corta_na_linha_da_planilha(self):
        conteudo = xlsx([["Programação"], ["chave", "folhas"], ["BANANA", 3], ["GOIABA", 2], ["MANGA", 1]])
        for ate, chaves in ((2, []), (3, ["BANANA"]), (4, ["BANANA", "GOIABA"]), (5, ["BANANA", "GOIABA", "MANGA"]),
                            (999, ["BANANA", "GOIABA", "MANGA"]), (None, ["BANANA", "GOIABA", "MANGA"])):
            with self.subTest(ate=ate):
                self.assertEqual([i.chave for i in lf.ler_planilha("a.xlsx", conteudo, ate).itens], chaves)

    def test_o_que_vem_depois_do_corte_nem_e_lido(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\nBANANA,3\nSALA2,x\n"), ate_linha=2)
        self.assertEqual((lote.erros, [(i.chave, i.folhas) for i in lote.itens]), ([], [("BANANA", 3)]))

    def test_corte_antes_da_primeira_chave_e_lote_vazio(self):
        lote = lf.ler_planilha("a.csv", csv("chave,folhas\nBANANA,3\n"), ate_linha=1)
        self.assertEqual(lote.itens, [])
        self.assertIn(lf.SEM_CHAVES, lote.erros)


class PlanilhaDosEncontros(unittest.TestCase):
    """A planilha de controle dos EU 2026: key, tag e day, e nenhuma coluna de folhas."""
    CABECALHO = ["day", "hour", "duration", "tag", "tag_check", "key", "key_trim", "key_check",
                 "certificate", "certificate description"]

    def ler(self, ate_linha=None):
        d = dt.datetime
        conteudo = xlsx([self.CABECALHO,
                         [d(2026, 11, 11), dt.time(10), dt.timedelta(hours=2), "palestra01", "valid",
                          "mangaba", "mangaba", "valid", "no", None],
                         [d(2026, 11, 13), dt.time(13, 30), dt.timedelta(hours=1), "oficina02", "valid",
                          "acerola", "acerola", "valid", "yes", "Descrição do certificado"],
                         [None, None, dt.timedelta(hours=1), None, "error", "cajarana", "cajarana", "valid", None, None],
                         [None, None, None, None, "error", "cupuacu", "cupuacu", "valid", None, None],
                         [None, None, None, None, "error", None, None, None, None, None],
                         [None, None, None, None, "error", None, None, None, None, None]],
                        titulos={0: "eventos"})
        return lf.ler_planilha("Controle de Frequência EU 2026.xlsx", conteudo, ate_linha)

    def test_uma_folha_por_chave_com_tag_e_dia(self):
        lote = self.ler()
        self.assertEqual(lote.erros, [])
        self.assertEqual([(i.linha, i.chave, i.folhas, i.nome, i.dia) for i in lote.itens], [
            (2, "MANGABA", 1, "palestra01", dt.date(2026, 11, 11)),
            (3, "ACEROLA", 1, "oficina02", dt.date(2026, 11, 13)),
            (4, "CAJARANA", 1, None, None),
            (5, "CUPUACU", 1, None, None)])

    def test_linhas_so_com_a_formula_nao_viram_erro(self):
        self.assertEqual(self.ler().erros, [])

    def test_o_padrao_de_uma_folha_vira_alerta(self):
        self.assertEqual(self.ler().alertas[0], lf.SEM_FOLHAS)

    def test_ate_a_linha(self):
        for ate, chaves in ((2, ["MANGABA"]), (4, ["MANGABA", "ACEROLA", "CAJARANA"]),
                            (7, ["MANGABA", "ACEROLA", "CAJARANA", "CUPUACU"])):
            with self.subTest(ate=ate):
                self.assertEqual([i.chave for i in self.ler(ate).itens], chaves)


class Alertas(unittest.TestCase):
    def itens(self, *chaves):
        return [lf.ItemLote(i + 2, c, 1) for i, c in enumerate(chaves)]

    def test_fronteira_da_distancia(self):
        self.assertTrue(lf.validar_lote(self.itens("ABCDEF", "ABCXYF"))[1], "distância 2 alerta")
        self.assertTrue(lf.validar_lote(self.itens("ABCDEF", "ABCXYZ"))[1], "distância 3 alerta")
        self.assertEqual(lf.validar_lote(self.itens("ABCDEF", "ABWXYZ"))[1], [], "distância 4 não alerta")

    def test_letra_lida_errada_alerta(self):
        _, alertas = lf.validar_lote(self.itens("VELEIRO", "GRUMIXAMA"))
        self.assertEqual(len(alertas), 1)
        self.assertIn("lido como W", alertas[0])

    def test_chave_gemea_com_w_alerta_duas_vezes(self):
        """É o único caminho de V para o evento errado, então tem que gritar."""
        _, alertas = lf.validar_lote(self.itens("VELEIRO", "WELEIRO"))
        self.assertTrue(any("distância" in a for a in alertas))
        self.assertTrue(any("lido como W" in a for a in alertas))

    def test_chave_curta_demais_alerta(self):
        _, alertas = lf.validar_lote(self.itens("ZZ", "GRUMIXAMA"))
        self.assertTrue(any("nunca liga" in a for a in alertas))

    def test_letras_seguras_sao_as_medidas(self):
        self.assertEqual(lf.LETRAS_SEGURAS, set("BDEFGHKLMPQRSTWZ"))
        self.assertNotIn(lf.LETRA_LIDA_ERRADA, lf.LETRAS_SEGURAS)

    def test_muitos_pares_resume(self):
        chaves = [f"AAAAAA{a}{b}" for a in "ABCDE" for b in "ABCDE"]
        _, alertas = lf.validar_lote(self.itens(*chaves))
        self.assertLessEqual(len(alertas), lf.LIMITE_ALERTAS + 1)
        self.assertIn("mais", alertas[-1])

    def test_teto_de_paginas(self):
        erros, _ = lf.validar_lote([lf.ItemLote(2, "BANANA", lf.LIMITE_PAGINAS)])
        self.assertEqual(erros, [])
        erros, _ = lf.validar_lote([lf.ItemLote(2, "BANANA", lf.LIMITE_PAGINAS + 1)])
        self.assertTrue(erros)

    def test_planilha_grande_nao_trava(self):
        """Sem isto, colar uma lista enorme travaria o servidor na conta de distância."""
        import itertools
        import string
        chaves = ["".join(p) for p in itertools.islice(itertools.product(string.ascii_uppercase, repeat=4), 3000)]
        linhas = "chave,folhas\n" + "".join(f"{c},1\n" for c in chaves)
        inicio = time.perf_counter()
        lote = lf.ler_planilha("a.csv", csv(linhas))
        self.assertLess(time.perf_counter() - inicio, 15)
        self.assertTrue(any("limite" in e for e in lote.erros), lote.erros[:2])


if __name__ == "__main__":
    unittest.main()
