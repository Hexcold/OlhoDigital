"""D35, fase 3: o modelo da folha por grupo, com ajuste por evento, e o pacote de impressão.

O banco guarda o modelo; a impressão (sem banco, D19) recebe o modelo no pedido ou no pacote.
O PDF é conferido pelo texto de cada página (`pdftotext`). Dados inventados.
"""
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api.tabelas import Evento

MODELO = {"titulo": "TITULO DO GRUPO TESTE", "subtitulo": "SUB DO GRUPO",
          "complemento": "", "instrucao": "USE LETRA DE FORMA"}


class ModeloDoGrupo(ComBanco):
    def setUp(self):
        super().setUp()
        self.cliente.post("/grupos", json={"nome": "EU"})

    def modelo(self):
        return self.cliente.get("/grupos/EU/modelo").json()

    def test_sem_modelo_proprio_vale_o_padrao(self):
        m = self.modelo()
        padrao = self.cliente.get("/impressao/cabecalho").json()["cabecalho"]
        self.assertEqual((m["proprio"], m["cabecalho"]), (False, padrao))

    def test_salvar_e_voltar_ao_padrao(self):
        r = self.cliente.put("/grupos/EU/modelo", json={"cabecalho": MODELO})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.modelo()["proprio"], self.modelo()["cabecalho"]), (True, MODELO))
        self.cliente.put("/grupos/EU/modelo", json={"cabecalho": None})
        self.assertFalse(self.modelo()["proprio"])

    def test_modelo_que_nao_cabe_e_recusado(self):
        r = self.cliente.put("/grupos/EU/modelo",
                             json={"cabecalho": {**MODELO, "titulo": "W" * 200}})
        self.assertEqual(r.status_code, 422)
        self.assertFalse(self.modelo()["proprio"])

    def test_modelo_nao_mexe_no_que_o_sistema_le(self):
        r = self.cliente.put("/grupos/EU/modelo",
                             json={"cabecalho": {**MODELO, "marcador": "x"}})
        self.assertEqual(r.status_code, 422)

    # --- ajuste por evento ----------------------------------------------------------------
    def test_ajuste_do_evento(self):
        ev = self.evento("PRATO", "EU")
        r = self.cliente.put(f"/eventos/{ev.id}/ajuste", json={"subtitulo": "SESSAO DE POSTERES"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cabecalho_ajuste"], {"subtitulo": "SESSAO DE POSTERES"})
        self.assertEqual(self.modelo()["ajustes"], {"PRATO": {"subtitulo": "SESSAO DE POSTERES",
                                                               "titulo": None, "complemento": None,
                                                               "instrucao": None}})
        r = self.cliente.put(f"/eventos/{ev.id}/ajuste", json={})
        self.assertIsNone(r.json()["cabecalho_ajuste"])          # tudo vazio tira o ajuste

    def test_ajuste_que_nao_cabe_ou_fora_dos_textos(self):
        ev = self.evento("PRATO", "EU")
        self.assertEqual(self.cliente.put(f"/eventos/{ev.id}/ajuste",
                                          json={"titulo": "W" * 200}).status_code, 422)
        self.assertEqual(self.cliente.put(f"/eventos/{ev.id}/ajuste",
                                          json={"grade": "x"}).status_code, 422)

    # --- pacote ---------------------------------------------------------------------------
    def test_pacote_ida_e_volta(self):
        itens = [{"linha": 2, "chave": "PRATO", "folhas": 3, "dia": "2027-11-11"},
                 {"linha": 3, "chave": "GELEIA", "folhas": 2}]
        r = self.cliente.post("/impressao/cadastrar", json={"itens": itens, "grupo": "EU"})
        self.assertEqual(r.json()["criados"], 2)
        self.cliente.put("/grupos/EU/modelo", json={"cabecalho": MODELO})
        geleia = self.db.query(Evento).filter_by(chave="GELEIA").one()
        self.cliente.put(f"/eventos/{geleia.id}/ajuste", json={"subtitulo": "SUB DO EVENTO"})
        encerrado = self.evento("QUILOMBO", "EU")              # encerrado não vai no pacote
        self.cliente.post(f"/eventos/{encerrado.id}/situacao", params={"situacao": "encerrado"})

        r = self.cliente.get("/grupos/EU/pacote")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("pacote_EU.json", r.headers["content-disposition"])
        pacote = r.json()
        self.assertEqual((pacote["formato"], pacote["versao"], pacote["grupo"]),
                         ("olhodigital-pacote", 1, "EU"))
        self.assertEqual(pacote["cabecalho"], MODELO)
        por_chave = {i["chave"]: i for i in pacote["itens"]}
        self.assertEqual(set(por_chave), {"PRATO", "GELEIA"})
        self.assertEqual((por_chave["PRATO"]["folhas"], por_chave["PRATO"]["dia"]),
                         (3, "2027-11-11"))
        self.assertEqual(pacote["ajustes"]["GELEIA"]["subtitulo"], "SUB DO EVENTO")

        # o PC de impressão abre o pacote, sem banco
        r = self.cliente.post("/impressao/pacote",
                              files={"arquivo": ("pacote_EU.json", r.content, "application/json")})
        self.assertEqual(r.status_code, 200, r.text)
        aberto = r.json()
        self.assertEqual((aberto["grupo"], aberto["lote"]["total_folhas"], aberto["lote"]["erros"]),
                         ("EU", 5, []))

    def test_pacote_invalido(self):
        for corpo in (b"isto nao e json", json.dumps({"formato": "outro", "versao": 1,
                                                      "grupo": "X", "cabecalho": MODELO,
                                                      "itens": []}).encode()):
            with self.subTest(corpo=corpo[:20]):
                r = self.cliente.post("/impressao/pacote",
                                      files={"arquivo": ("p.json", corpo, "application/json")})
                self.assertEqual(r.status_code, 422)

    def test_grupo_sem_evento_aberto_nao_tem_pacote(self):
        self.assertEqual(self.cliente.get("/grupos/EU/pacote").status_code, 409)


class PortaoPorTexto(unittest.TestCase):
    """O portão da folha (D16) roda para CADA texto distinto do lote, não só para o do grupo.

    O desenho e a detecção são dublês: o objeto é com que textos o portão é chamado.
    """
    def test_um_portao_por_texto_distinto(self):
        from dataclasses import replace
        from unittest import mock
        import lote_folhas as lf
        base = lf.carregar_cabecalho()
        com_ajuste = lf.combinar(base, {"subtitulo": "SUB DO EVENTO"})
        with mock.patch.object(lf, "conferir_cabecalho") as portao, \
                mock.patch.object(lf.gf, "gerar"), \
                mock.patch.object(lf.gf, "conferir_deteccao", return_value={}), \
                mock.patch.object(lf.subprocess, "run"), mock.patch.object(lf.shutil, "move"), \
                mock.patch.object(lf.os.path, "getsize", return_value=1):
            lf._montar_pdf([("PRATO", 1, None), ("GELEIA", 2, None), ("QUILOMBO", 1, None)],
                           base, "x.pdf", {"GELEIA": {"subtitulo": "SUB DO EVENTO"}})
        conferidos = [c.args[0] for c in portao.call_args_list]
        self.assertEqual(len(conferidos), 2)                 # um por texto distinto, sem repetir
        self.assertEqual({replace(t) == base for t in conferidos}, {True, False})
        self.assertIn(com_ajuste, conferidos)


class PdfDoGrupo(ComBanco):
    """Todas as folhas do grupo num PDF só (pedido do autor, 20/11)."""

    def test_um_pdf_com_as_folhas_previstas_dos_abertos(self):
        self.cliente.post("/grupos", json={"nome": "EU"})
        itens = [{"linha": 1, "chave": "PRATO", "folhas": 2},
                 {"linha": 2, "chave": "GELEIA", "folhas": 1}]
        self.cliente.post("/impressao/cadastrar", json={"itens": itens, "grupo": "EU"})
        self.cliente.put("/grupos/EU/modelo", json={"cabecalho": MODELO})
        geleia = self.db.query(Evento).filter_by(chave="GELEIA").one()
        self.cliente.put(f"/eventos/{geleia.id}/ajuste", json={"subtitulo": "SUB DO EVENTO"})
        fechado = self.evento("QUILOMBO", "EU")              # encerrado não se imprime
        self.cliente.post(f"/eventos/{fechado.id}/situacao", params={"situacao": "encerrado"})

        r = self.cliente.get("/grupos/EU/pdf")
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.content[:4], b"%PDF")
        self.assertEqual(r.headers["x-paginas"], "3")        # 2 de PRATO + 1 de GELEIA
        self.assertIn("folhas_EU", r.headers["content-disposition"])
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "g.pdf"
            pdf.write_bytes(r.content)
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
            paginas = next(l.split(":")[1].strip() for l in info.splitlines()
                           if l.startswith("Pages:"))
            self.assertEqual(paginas, "3")
            texto = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True,
                                   text=True).stdout
        self.assertEqual(texto.count("TITULO DO GRUPO TESTE"), 3)
        self.assertEqual(texto.count("SUB DO EVENTO"), 1)    # só a página do GELEIA

    def test_grupo_sem_evento_aberto(self):
        self.cliente.post("/grupos", json={"nome": "VAZIO"})
        self.assertEqual(self.cliente.get("/grupos/VAZIO/pdf").status_code, 409)


class PdfComModelo(ComBanco):
    def test_ajuste_que_nao_cabe_barra_o_lote_inteiro(self):
        # o caminho do PC de impressão: o ajuste chega no pedido, sem passar pela rota do evento
        itens = [{"linha": 1, "chave": "PRATO", "folhas": 1},
                 {"linha": 2, "chave": "GELEIA", "folhas": 1}]
        r = self.cliente.post("/impressao/pdf", json={
            # dentro do limite de caracteres, largo demais em pixels: só o portão (D16) pega
            "itens": itens, "cabecalho": MODELO, "ajustes": {"GELEIA": {"instrucao": "W" * 90}}})
        self.assertEqual(r.status_code, 422, r.text[:200])
        self.assertIn("não cabe", r.json()["detail"])

    def test_cada_pagina_sai_com_o_texto_do_seu_evento(self):
        itens = [{"linha": 1, "chave": "PRATO", "folhas": 1},
                 {"linha": 2, "chave": "GELEIA", "folhas": 1}]
        r = self.cliente.post("/impressao/pdf", json={
            "itens": itens, "cabecalho": MODELO,
            "ajustes": {"geleia": {"subtitulo": "SUB DO EVENTO"}}})   # chave em minúscula também
        self.assertEqual(r.status_code, 200, r.text)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "x.pdf"
            pdf.write_bytes(r.content)
            texto = [subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), str(pdf), "-"],
                                    capture_output=True, text=True).stdout for p in (1, 2)]
        self.assertIn("TITULO DO GRUPO TESTE", texto[0])
        self.assertIn("SUB DO GRUPO", texto[0])
        self.assertNotIn("SUB DO EVENTO", texto[0])
        self.assertIn("TITULO DO GRUPO TESTE", texto[1])
        self.assertIn("SUB DO EVENTO", texto[1])
        self.assertNotIn("SUB DO GRUPO", texto[1])


if __name__ == "__main__":
    unittest.main()
