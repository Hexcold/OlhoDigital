"""Grupos de eventos e apagar folhas em massa (D24), contra um banco SQLite em memória.

O resto da bateria não toca em banco. Aqui o banco é o objeto do teste: quem é presença,
quem fica pendente, quais folhas saem e quais ficam. Os dados são inventados — nomes que não
existem nas bases e matrículas 9xxxxx, fora da faixa real.
"""
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apoio                                                   # noqa: F401  isola o ambiente
from api.db import dependencia_sessao
from api.main import app
from api.rotas import grupos
from api.tabelas import (Base, BaseDePessoas, Evento, Folha, Grupo, Linha, PessoaDaBase,
                         Revisao)

PESSOAS = {"900001": "ZULEICA AMARANTE FROTA", "900002": "QUITERIA BRANDAO VIEGAS",
           "912345": "OTONIEL BARCELAR FURTADO"}
INICIO = datetime(2026, 11, 11, 8, 0)


class ComBanco(unittest.TestCase):
    def setUp(self):
        motor = create_engine("sqlite://", poolclass=StaticPool,
                              connect_args={"check_same_thread": False})
        Base.metadata.create_all(motor)
        self.addCleanup(motor.dispose)
        self.Sessao = sessionmaker(motor, expire_on_commit=False)
        self.db = self.Sessao()

        def sessao_de_teste():
            s = self.Sessao()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[dependencia_sessao] = sessao_de_teste
        self.addCleanup(app.dependency_overrides.clear)
        # A leitura em segundo plano (`ler_fila`) abre a própria sessão, fora da injeção do
        # FastAPI. Sem isto, um teste que envia folha consultaria o Postgres configurado, e
        # com o banco de verdade no ar leria as folhas DELE.
        from api.rotas import folhas as rotas_folhas
        trocar = mock.patch.object(rotas_folhas, "sessao", self.Sessao)
        trocar.start()
        self.addCleanup(trocar.stop)
        self.addCleanup(self.db.close)
        self.cliente = TestClient(app, raise_server_exceptions=False)
        self.pasta = Path(tempfile.mkdtemp(prefix="olhodigital_grupos_"))
        # 22/11: a base é a geral do sistema (`api/bases.py`), não mais a tabela única
        geral = BaseDePessoas(grupo=None, arquivo_original="inventada.xlsx", sha256="0" * 64,
                              pessoas=len(PESSOAS), ativa=True)
        self.db.add(geral)
        self.db.flush()
        self.db.add_all(PessoaDaBase(base_id=geral.id, matricula=m, nome=n, nome_normalizado=n,
                                     curso="CURSO X") for m, n in PESSOAS.items())
        self.db.commit()
        self.base_geral = geral
        from api import bases
        bases._PARA_LEITURA.clear()         # o cache é por id, e cada teste tem um banco novo

    # --- fábricas -------------------------------------------------------------------------
    def evento(self, chave, grupo=None, horas=0):
        # D35: o grupo é uma tabela; a fábrica cria a linha como a API faria
        if grupo and self.db.scalar(select(Grupo.id).where(Grupo.nome == grupo)) is None:
            self.db.add(Grupo(nome=grupo))
            self.db.flush()
        ev = Evento(chave=chave, tipo="evento", nome=f"evento {chave.lower()}", local="sala 1",
                    inicio=INICIO + timedelta(hours=horas), grupo=grupo)
        self.db.add(ev)
        self.db.commit()
        return ev

    def folha(self, evento=None, estado="processada", revisada=False, nome="lote.pdf",
              pagina=1):
        imagem = self.pasta / f"{nome}_{pagina}_{evento.chave if evento else 'solta'}.png"
        imagem.write_bytes(b"png")
        recortes = imagem.parent / f"{imagem.stem}_linhas"
        recortes.mkdir()
        (recortes / "01.png").write_bytes(b"png")
        f = Folha(evento_id=evento.id if evento else None, nome_original=nome,
                  arquivo=str(imagem), origem_sha256=f"{imagem.name:0>64}"[-64:],
                  pagina=pagina, sha256="0" * 64, modelo_folha="gerada_v2", estado=estado,
                  alpha=0.5, theta=0.60, delta=0.08, tau_nome=0.65,
                  revisada_em=datetime(2026, 11, 18) if revisada else None)
        self.db.add(f)
        self.db.commit()
        return f

    def linha(self, folha, numero, decisao, sugerida=None, duplicada=False):
        candidatos = ([{"matricula": sugerida, "nome": PESSOAS[sugerida], "score": 0.7,
                        "sim_matricula": 0.8, "sim_nome": 0.6}] if sugerida else [])
        ln = Linha(folha_id=folha.id, numero=numero, leitura_matricula="9?????",
                   leitura_nome="X", decisao=decisao, matricula_sugerida=sugerida,
                   duplicada_na_folha=duplicada, score=0.5, margem=0.01,
                   candidatos=candidatos)
        self.db.add(ln)
        self.db.commit()
        return ln

    def revisar(self, linha, matricula, em=None):
        r = Revisao(linha_id=linha.id, matricula_escolhida=matricula, revisor="teste")
        if em is not None:
            r.criado_em = em
        self.db.add(r)
        self.db.commit()
        return r

    def contar(self, tabela):
        return self.db.scalar(select(func.count()).select_from(tabela))


class Grupos(ComBanco):
    def test_lista_os_grupos_em_uso_com_as_contagens(self):
        a, b = self.evento("TESTEUM", "EU"), self.evento("TESTEDOIS", "EU")
        self.evento("TESTETRES", "OUTRO")
        self.evento("TESTEQUATRO")                        # sem grupo: não aparece
        self.folha(a, revisada=True)
        self.folha(b, nome="b.pdf")
        r = self.cliente.get("/grupos")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [
            # D30: sem cópia, as 2 folhas do EU são de leitura única
            {"nome": "EU", "eventos": 2, "folhas": 2, "folhas_revisadas": 1, "eventos_abertos": 2,
             "copias": 0, "leitura_unica": 2, "com_copia": 0,
             "etapa": "pronto", "descricao": None, "pendentes": 0,           # D35
             "a_concluir": 1},
            {"nome": "OUTRO", "eventos": 1, "folhas": 0, "folhas_revisadas": 0,
             "eventos_abertos": 1, "copias": 0, "leitura_unica": 0, "com_copia": 0,
             "etapa": "preparando", "descricao": None, "pendentes": 0, "a_concluir": 0}])

    def test_por_no_grupo_e_todos_ou_nenhum(self):
        a, b = self.evento("TESTEUM"), self.evento("TESTEDOIS")
        r = self.cliente.post("/grupos/eventos", json={"evento_ids": [a.id, 9999], "grupo": "EU"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("9999", r.json()["detail"])
        self.db.expire_all()
        self.assertIsNone(self.db.get(Evento, a.id).grupo)

        r = self.cliente.post("/grupos/eventos",
                              json={"evento_ids": [a.id, b.id], "grupo": "  EU   2026 "})
        self.assertEqual(r.json(), {"alterados": 2, "grupo": "EU 2026"})
        self.db.expire_all()
        self.assertEqual({self.db.get(Evento, i).grupo for i in (a.id, b.id)}, {"EU 2026"})

        # D35: todo evento pertence a um grupo; "tirar do grupo" é recusado
        r = self.cliente.post("/grupos/eventos", json={"evento_ids": [a.id], "grupo": "   "})
        self.assertEqual(r.status_code, 422)
        self.db.expire_all()
        self.assertEqual(self.db.get(Evento, a.id).grupo, "EU 2026")

    def test_nome_de_grupo_invalido(self):
        a = self.evento("TESTEUM")
        for corpo in ({"evento_ids": [], "grupo": "EU"}, {"evento_ids": [a.id], "grupo": "A/B"},
                      {"evento_ids": [a.id], "grupo": "G" * 61}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.cliente.post("/grupos/eventos", json=corpo).status_code, 422)

    def test_editar_sem_o_campo_preserva_o_grupo(self):
        a = self.evento("TESTEUM", "EU")
        corpo = {"chave": "TESTEUM", "tipo": "evento", "nome": "outro nome"}
        r = self.cliente.patch(f"/eventos/{a.id}", json=corpo)
        self.assertEqual((r.status_code, r.json()["grupo"], r.json()["nome"]),
                         (200, "EU", "outro nome"))
        r = self.cliente.patch(f"/eventos/{a.id}", json={**corpo, "grupo": None})
        self.assertEqual(r.status_code, 422)                 # D35: não fica sem grupo
        r = self.cliente.patch(f"/eventos/{a.id}", json={**corpo, "grupo": "OUTRO"})
        self.assertEqual(r.json()["grupo"], "OUTRO")

    def test_filtrar_eventos_pelo_grupo(self):
        self.evento("TESTEUM", "EU")
        self.evento("TESTEDOIS", "OUTRO")
        r = self.cliente.get("/eventos", params={"grupo": "EU"})
        self.assertEqual([e["chave"] for e in r.json()], ["TESTEUM"])

    def test_cadastro_de_chaves_poe_no_grupo_so_os_novos(self):
        self.evento("TESTEUM", "ANTIGO")
        itens = [{"linha": 2, "chave": "TESTEUM", "folhas": 1},
                 {"linha": 3, "chave": "TESTEDOIS", "folhas": 1}]
        r = self.cliente.post("/impressao/cadastrar", json={"itens": itens, "grupo": "EU"})
        self.assertEqual(r.json(), {"criados": 1, "ja_existiam": 1})
        grupos_por_chave = dict(self.db.execute(select(Evento.chave, Evento.grupo)).all())
        self.assertEqual(grupos_por_chave, {"TESTEUM": "ANTIGO", "TESTEDOIS": "EU"})


class Frequencia(ComBanco):
    def montar(self):
        """Um grupo com cada situação possível, em dois eventos fora de ordem de cadastro."""
        tarde = self.evento("TESTEDOIS", "EU", horas=4)
        manha = self.evento("TESTEUM", "EU")
        fora = self.evento("TESTETRES", "OUTRO")
        f1 = self.folha(manha, nome="manha.pdf")
        aceita = self.linha(f1, 1, "aceito", "900001")
        self.linha(f1, 2, "quarentena")                            # pendente
        revisada = self.linha(f1, 3, "quarentena")
        self.revisar(revisada, "900002")
        sem_pessoa = self.linha(f1, 4, "quarentena")
        self.revisar(sem_pessoa, None)
        self.linha(f1, 5, "anulada")
        corrigida = self.linha(f1, 6, "aceito", "900001", duplicada=True)
        # duas revisões na mesma hora: vale a de id maior, a última gravada
        self.revisar(corrigida, "900002", em=datetime(2026, 11, 18, 10))
        self.revisar(corrigida, "912345", em=datetime(2026, 11, 18, 10))
        anulada_revista = self.linha(f1, 7, "anulada")
        self.revisar(anulada_revista, "912345")
        f2 = self.folha(tarde, nome="tarde.pdf", revisada=True)
        self.linha(f2, 1, "aceito", "912345")
        self.linha(self.folha(fora, nome="fora.pdf"), 1, "aceito", "900002")
        return aceita

    def test_situacao_de_cada_linha(self):
        self.montar()
        linhas = grupos.linhas_da_frequencia(self.db, "EU")
        resumo = [(l["chave"], l["linha"], l["situacao"], l["matricula"]) for l in linhas]
        self.assertEqual(resumo, [
            ("TESTEUM", 1, "presente (automático)", "900001"),
            ("TESTEUM", 2, "pendente de revisão", None),
            ("TESTEUM", 3, "presente (revisado)", "900002"),
            ("TESTEUM", 4, "não identificado", None),
            ("TESTEUM", 5, "anulada", None),
            ("TESTEUM", 6, "presente (revisado)", "912345"),
            ("TESTEUM", 7, "presente (revisado)", "912345"),
            ("TESTEDOIS", 1, "presente (automático)", "912345"),
        ])
        self.assertEqual(linhas[0]["nome"], PESSOAS["900001"])
        self.assertEqual(linhas[0]["curso"], "CURSO X")
        self.assertIsNone(linhas[1]["nome"])
        self.assertEqual([l["duplicada_na_folha"] for l in linhas].count(True), 1)
        self.assertEqual([l["folha_concluida"] for l in linhas], [False] * 7 + [True])

    def test_planilha(self):
        self.montar()
        r = self.cliente.get("/grupos/EU/frequencia.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("frequencia_EU.xlsx", r.headers["content-disposition"])
        aba = load_workbook(io.BytesIO(r.content)).active
        valores = list(aba.values)
        self.assertEqual(valores[0], tuple(t for _, t in grupos.COLUNAS))
        self.assertEqual(len(valores), 1 + 8)
        coluna = {t: i for i, (_, t) in enumerate(grupos.COLUNAS)}
        self.assertEqual(valores[1][coluna["situação"]], "presente (automático)")
        self.assertEqual(valores[1][coluna["matrícula"]], "900001")
        self.assertEqual(valores[1][coluna["folha concluída"]], "não")
        self.assertEqual(valores[8][coluna["folha concluída"]], "sim")

    def test_json_traz_a_frequencia_e_a_procedencia(self):
        self.montar()
        r = self.cliente.get("/grupos/EU/frequencia.json")
        self.assertEqual(r.status_code, 200)
        corpo = r.json()
        self.assertEqual(corpo["grupo"], "EU")
        self.assertEqual(corpo["frequencia"], grupos.linhas_da_frequencia(self.db, "EU"))
        self.assertEqual([e["chave"] for e in corpo["eventos"]], ["TESTEUM", "TESTEDOIS"])
        folha = corpo["eventos"][0]["folhas"][0]
        self.assertIn("ponto_de_operacao", folha)
        self.assertEqual(len(folha["linhas"]), 7)
        self.assertEqual(folha["linhas"][5]["revisao"]["matricula_escolhida"], "912345")

    def test_nome_com_acento_e_espaco_no_download(self):
        a = self.evento("TESTEUM", "Encontros Universitários")
        self.linha(self.folha(a), 1, "aceito", "900001")
        r = self.cliente.get("/grupos/Encontros Universitários/frequencia.xlsx")
        self.assertEqual(r.status_code, 200)
        disposicao = r.headers["content-disposition"]
        self.assertIn('filename="frequencia_Encontros_Universit_rios.xlsx"', disposicao)
        self.assertIn("filename*=UTF-8''frequencia_Encontros%20Universit%C3%A1rios.xlsx",
                      disposicao)

    def test_grupo_inexistente(self):
        for rota in ("frequencia.json", "frequencia.xlsx", "apagar-folhas", "quarentena"):
            with self.subTest(rota=rota):
                self.assertEqual(self.cliente.get(f"/grupos/NADA/{rota}").status_code, 404)


class QuarentenaDoGrupo(ComBanco):
    def test_junta_as_quarentenas_do_grupo_em_ordem(self):
        tarde = self.evento("TESTEDOIS", "EU", horas=4)
        manha = self.evento("TESTEUM", "EU")
        fora = self.evento("TESTETRES", "OUTRO")
        f_tarde = self.folha(tarde, nome="tarde.pdf", revisada=True)
        f_manha2 = self.folha(manha, nome="manha.pdf", pagina=2)
        f_manha1 = self.folha(manha, nome="manha.pdf", pagina=1)
        self.linha(f_manha2, 3, "quarentena", "900001")
        self.linha(f_manha2, 1, "aceito", "900002")                # aceite não entra
        self.linha(f_manha2, 2, "anulada")                         # anulada não entra
        decidida = self.linha(f_manha1, 9, "quarentena", "912345")
        self.revisar(decidida, "912345")
        self.linha(f_tarde, 4, "quarentena")
        self.linha(self.folha(fora, nome="fora.pdf"), 1, "quarentena", "900002")

        r = self.cliente.get("/grupos/EU/quarentena")
        self.assertEqual(r.status_code, 200)
        linhas = r.json()
        self.assertEqual([(l["evento_chave"], l["folha_pagina"], l["numero"]) for l in linhas],
                         [("TESTEUM", 1, 9), ("TESTEUM", 2, 3), ("TESTEDOIS", 1, 4)])
        self.assertEqual({l["decisao"] for l in linhas}, {"quarentena"})
        # a decidida vem com a revisão, para a tela saber; a da folha concluída vem travada
        self.assertEqual(linhas[0]["revisao"]["matricula_escolhida"], "912345")
        self.assertIsNone(linhas[1]["revisao"])
        self.assertEqual([l["folha_concluida"] for l in linhas], [False, False, True])
        self.assertEqual(linhas[0]["folha_nome"], "manha.pdf")
        self.assertEqual(linhas[0]["evento_nome"], "evento testeum")
        # o motivo é calculado com o θ e o δ da folha, como na revisão de uma folha só
        self.assertIn("margem", linhas[1]["motivo_rejeicao"])

    def test_igual_a_revisao_de_uma_folha(self):
        """A mesma linha, vista no grupo e na folha, traz os mesmos campos de revisão."""
        a = self.evento("TESTEUM", "EU")
        f = self.folha(a)
        self.linha(f, 1, "quarentena", "900001")
        do_grupo = self.cliente.get("/grupos/EU/quarentena").json()[0]
        da_folha = self.cliente.get(f"/folhas/{f.id}/linhas").json()[0]
        self.assertEqual({k: do_grupo[k] for k in da_folha}, da_folha)

    def test_grupo_sem_quarentena_e_inexistente(self):
        a = self.evento("TESTEUM", "EU")
        self.linha(self.folha(a), 1, "aceito", "900001")
        self.assertEqual(self.cliente.get("/grupos/EU/quarentena").json(), [])
        self.assertEqual(self.cliente.get("/grupos/NADA/quarentena").status_code, 404)


class ApagarEmMassa(ComBanco):
    def montar(self):
        eu1, eu2 = self.evento("TESTEUM", "EU"), self.evento("TESTEDOIS", "EU")
        fora = self.evento("TESTETRES", "OUTRO")
        self.f = {
            "lida": self.folha(eu1, nome="a.pdf"),
            "revisada": self.folha(eu1, nome="b.pdf", revisada=True),
            "na_fila": self.folha(eu2, nome="c.pdf", estado="processando"),
            "com_erro": self.folha(eu2, nome="d.pdf", estado="erro"),
            "fora": self.folha(fora, nome="e.pdf"),
            "solta": self.folha(None, nome="f.pdf", estado="suspeita"),
        }
        self.revisar(self.linha(self.f["lida"], 1, "quarentena"), "900001")
        self.linha(self.f["revisada"], 1, "aceito", "900002")
        # guardados antes: depois de apagar, ler o atributo de uma folha que saiu vai ao banco
        self.ids = {k: f.id for k, f in self.f.items()}
        self.arquivos = {k: Path(f.arquivo) for k, f in self.f.items()}

    def restantes(self):
        self.db.expire_all()
        return {k for k, i in self.ids.items() if self.db.get(Folha, i) is not None}

    def test_previa_nao_apaga(self):
        self.montar()
        r = self.cliente.get("/grupos/EU/apagar-folhas")
        self.assertEqual(r.json(), {"apagar": 2, "revisadas_mantidas": 1, "na_fila_mantidas": 1})
        r = self.cliente.get("/grupos/EU/apagar-folhas", params={"incluir_revisadas": True})
        self.assertEqual(r.json(), {"apagar": 3, "revisadas_mantidas": 0, "na_fila_mantidas": 1})
        self.assertEqual(self.restantes(), set(self.f))

    def test_confirmacao_errada_nao_apaga_nada(self):
        self.montar()
        for numero in (0, 1, 3, -2):
            with self.subTest(confirmacao=numero):
                r = self.cliente.post("/grupos/EU/apagar-folhas", json={"confirmacao": numero})
                self.assertEqual(r.status_code, 409)
        self.assertEqual(self.restantes(), set(self.f))
        self.assertTrue(all(a.exists() for a in self.arquivos.values()))

    def test_apaga_so_o_grupo_e_preserva_revisada_e_fila(self):
        self.montar()
        r = self.cliente.post("/grupos/EU/apagar-folhas", json={"confirmacao": 2})
        self.assertEqual(r.json(), {"apagar": 2, "revisadas_mantidas": 1, "na_fila_mantidas": 1})
        self.assertEqual(self.restantes(), {"revisada", "na_fila", "fora", "solta"})
        for nome in ("lida", "com_erro"):
            imagem = self.arquivos[nome]
            self.assertFalse(imagem.exists())
            self.assertFalse((imagem.parent / f"{imagem.stem}_linhas").exists())
        self.assertTrue(self.arquivos["revisada"].exists())
        # a linha e a revisão da folha apagada saem junto; os eventos ficam
        self.assertEqual((self.contar(Linha), self.contar(Revisao)), (1, 0))
        self.assertEqual(self.contar(Evento), 3)

    def test_incluir_revisadas(self):
        self.montar()
        r = self.cliente.post("/grupos/EU/apagar-folhas",
                              json={"confirmacao": 3, "incluir_revisadas": True})
        self.assertEqual(r.json()["apagar"], 3)
        self.assertEqual(self.restantes(), {"na_fila", "fora", "solta"})

    def test_apagar_todas(self):
        self.montar()
        r = self.cliente.get("/folhas/apagar-todas")
        self.assertEqual(r.json(), {"apagar": 4, "revisadas_mantidas": 1, "na_fila_mantidas": 1})
        r = self.cliente.post("/folhas/apagar-todas", json={"confirmacao": 4})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.restantes(), {"revisada", "na_fila"})
        self.assertEqual(self.contar(Evento), 3)

    def test_apagar_todas_sem_folhas(self):
        r = self.cliente.post("/folhas/apagar-todas", json={"confirmacao": 0})
        self.assertEqual(r.json(), {"apagar": 0, "revisadas_mantidas": 0, "na_fila_mantidas": 0})

    def test_apagar_uma_continua_igual(self):
        self.montar()
        self.assertEqual(self.cliente.delete(f"/folhas/{self.ids['revisada']}").status_code, 409)
        r = self.cliente.delete(f"/folhas/{self.ids['revisada']}", params={"forcar": True})
        self.assertIn(r.status_code, (200, 204))
        self.assertNotIn("revisada", self.restantes())
        self.assertFalse(self.arquivos["revisada"].exists())


if __name__ == "__main__":
    unittest.main()
