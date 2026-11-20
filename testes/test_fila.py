"""A fila de leitura: reivindicação atômica, retomada ao subir e o progresso que a tela mostra.

Em 20/11 um reinício no meio de um envio deixou 79 folhas paradas, e dois servidores leram a
mesma fila ao mesmo tempo. O pipeline é trocado por um dublê: o objeto é a fila, não a leitura.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline
from api.rotas import folhas
from api.tabelas import Folha, Linha

RECURSOS = {"ponto": {"alpha": 0.5, "theta": 0.6, "delta": 0.08, "metrica_nome": "x",
                      "tau_nome": 0.65, "recorte": "v3"},
            "modelo_matricula": "m", "modelo_letras": "l"}
LINHA = {"numero": 1, "leitura_matricula": "900001", "leitura_nome": "ZULEICA",
         "confiancas_matricula": [], "confiancas_nome": [], "motivos_matricula": ["lido"] * 6,
         "motivos_nome": ["lido"] * 7, "score": 0.9, "margem": 0.5, "decisao": "aceito",
         "matricula_sugerida": "900001", "candidatos": [], "duplicada_na_folha": False}
AGORA = datetime.now(timezone.utc).replace(tzinfo=None)


class Fila(ComBanco):
    def ler(self, *ids, efeito=None):
        processar = mock.Mock(return_value=[dict(LINHA)], side_effect=efeito)
        with mock.patch.object(folhas, "sessao", self.Sessao), \
                mock.patch.object(pipeline, "conferir_marcadores", return_value=4), \
                mock.patch.object(pipeline, "carregar_recursos", return_value=RECURSOS), \
                mock.patch.object(pipeline, "processar", processar), \
                mock.patch.object(pipeline, "recortar_para_revisao"), \
                mock.patch.object(folhas, "_vincular_evento"):
            folhas.ler_em_sequencia(list(ids))
        self.db.expire_all()
        return processar

    def estado(self, folha):
        return self.db.get(Folha, folha.id).estado

    # --- reivindicação ------------------------------------------------------------------
    def test_so_le_folha_na_fila(self):
        f = self.folha(estado="processada")
        self.assertEqual(self.ler(f.id).call_count, 0)
        self.assertEqual(self.estado(f), "processada")

    def test_a_mesma_folha_duas_vezes_e_lida_uma_vez(self):
        f = self.folha(estado="recebida")
        processar = self.ler(f.id, f.id)
        self.assertEqual(processar.call_count, 1)
        self.assertEqual(self.estado(f), "processada")
        self.assertEqual(len(self.db.get(Folha, f.id).linhas), 1)

    def test_uma_folha_que_falha_nao_para_a_fila(self):
        a, b = self.folha(estado="recebida", nome="a.pdf"), self.folha(estado="recebida",
                                                                         nome="b.pdf")
        chamadas = []

        def efeito(caminho, modelo):
            chamadas.append(caminho)
            if len(chamadas) == 1:
                raise RuntimeError("leitura quebrou")
            return [dict(LINHA)]

        # o erro do pipeline vira estado "erro" dentro de `_ler_e_gravar`
        self.ler(a.id, b.id, efeito=efeito)
        self.assertEqual(self.estado(a), "erro")
        self.assertEqual(self.estado(b), "processada")

    def test_uma_excecao_fora_do_pipeline_nao_para_a_fila(self):
        # o que escapa de `_ler_e_gravar` (o banco caiu, um defeito) vai para o log, e a fila segue
        ler = mock.Mock(side_effect=[RuntimeError("banco caiu"), None])
        with mock.patch.object(folhas, "_ler_e_gravar", ler), \
                mock.patch.object(folhas.log, "exception") as registro:
            folhas.ler_em_sequencia([1, 2])
        self.assertEqual([c.args[0] for c in ler.call_args_list], [1, 2])
        registro.assert_called_once()

    # --- retomada ao subir --------------------------------------------------------------
    def test_retomar_devolve_a_interrompida_e_mantem_a_com_linhas(self):
        interrompida = self.folha(estado="processando", nome="a.pdf")
        estranha = self.folha(estado="processando", nome="b.pdf")
        self.linha(estranha, 1, "aceito", "900001")
        na_fila = self.folha(estado="recebida", nome="c.pdf")
        pronta = self.folha(estado="processada", nome="d.pdf")
        with mock.patch.object(folhas.log, "warning") as aviso:
            ids = folhas.retomar_fila(self.db)
        self.assertEqual(ids, sorted([interrompida.id, na_fila.id]))
        self.db.expire_all()
        self.assertEqual(self.estado(interrompida), "recebida")
        self.assertEqual(self.estado(estranha), "processando")      # não adivinha
        self.assertEqual(self.estado(pronta), "processada")
        aviso.assert_called_once()
        self.assertIsNotNone(self.db.get(Folha, na_fila.id).na_fila_desde)

    def test_retomada_le_tudo(self):
        fs = [self.folha(estado="processando", nome=f"{i}.pdf") for i in range(3)]
        self.ler(*folhas.retomar_fila(self.db))
        self.assertEqual([self.estado(f) for f in fs], ["processada"] * 3)

    # --- progresso ----------------------------------------------------------------------
    def test_progresso_do_lote(self):
        lote = AGORA - timedelta(minutes=5)
        for i, t in enumerate((40, 30, 20)):                   # 3 prontas, 10 s entre elas
            f = self.folha(estado="processada", nome=f"p{i}.pdf")
            f.na_fila_desde, f.processada_em = lote, AGORA - timedelta(seconds=t)
        antiga = self.folha(estado="processada", nome="antiga.pdf")  # de outro lote
        antiga.na_fila_desde = lote - timedelta(days=3)
        lendo = self.folha(estado="processando", nome="lote.pdf", pagina=4)
        for f in (lendo, self.folha(estado="recebida", nome="q1.pdf"),
                  self.folha(estado="recebida", nome="q2.pdf")):
            f.na_fila_desde = lote
        self.db.commit()
        r = self.cliente.get("/folhas/fila")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["na_fila"], d["lendo"], d["lote_total"], d["lote_prontas"]),
                         (2, 1, 6, 3))
        self.assertEqual(d["lendo_agora"], "lote.pdf, página 4")
        self.assertEqual(d["segundos_por_folha"], 10.0)
        self.assertEqual(d["estimativa_s"], 30)

    def test_prontas_sobem_e_o_total_fica(self):
        # o caso do autor (20/11): cada folha do envio com a sua hora, microssegundos depois da
        # anterior; as primeiras já lidas não podiam sumir do lote ("0 de 33", "0 de 23")
        inicio = AGORA - timedelta(minutes=3)
        folhas_ = [self.folha(estado="recebida", nome=f"f{i}.pdf") for i in range(5)]
        for i, f in enumerate(folhas_):
            f.na_fila_desde = inicio + timedelta(microseconds=i)
        for i, f in enumerate(folhas_[:2]):                       # duas já lidas
            f.estado, f.processada_em = "processada", inicio + timedelta(seconds=10 * (i + 1))
        self.db.commit()
        d = self.cliente.get("/folhas/fila").json()
        self.assertEqual((d["lote_prontas"], d["lote_total"]), (2, 5))

    def test_dois_envios_em_sequencia_sao_um_lote(self):
        a, b = AGORA - timedelta(minutes=5), AGORA - timedelta(minutes=2)
        primeiro = [self.folha(estado="processada", nome=f"a{i}.pdf") for i in range(3)]
        for i, f in enumerate(primeiro):
            # a última do primeiro envio terminou DEPOIS de o segundo entrar na fila
            f.na_fila_desde, f.processada_em = a, b + timedelta(seconds=-20 + 15 * i)
        segundo = [self.folha(estado="recebida", nome=f"b{i}.pdf") for i in range(2)]
        for f in segundo:
            f.na_fila_desde = b
        self.db.commit()
        d = self.cliente.get("/folhas/fila").json()
        self.assertEqual((d["lote_prontas"], d["lote_total"]), (3, 5))

    def test_lote_anterior_com_a_fila_vazia_no_meio_nao_entra(self):
        antes = AGORA - timedelta(hours=2)
        velha = self.folha(estado="processada", nome="velha.pdf")
        velha.na_fila_desde, velha.processada_em = antes, antes + timedelta(seconds=9)
        nova = self.folha(estado="recebida", nome="nova.pdf")
        nova.na_fila_desde = AGORA
        self.db.commit()
        d = self.cliente.get("/folhas/fila").json()
        self.assertEqual((d["lote_prontas"], d["lote_total"]), (0, 1))

    def test_fila_vazia(self):
        self.folha(estado="processada")
        d = self.cliente.get("/folhas/fila").json()
        self.assertEqual((d["na_fila"], d["lendo"], d["lote_total"]), (0, 0, 0))
        self.assertEqual(d["segundos_por_folha"], 9.0)         # sem histórico recente

    def test_ritmo_nao_depende_do_relogio(self):
        # o banco do autor guarda a hora em Brazil/East: 3 h atrás do UTC do Python. O ritmo
        # tem que sair das leituras entre si, não da comparação com o agora.
        fuso = AGORA - timedelta(hours=3)
        for i, t in enumerate((24, 16, 8)):
            f = self.folha(estado="processada", nome=f"p{i}.pdf")
            f.processada_em = fuso - timedelta(seconds=t)
        self.db.commit()
        self.assertEqual(self.cliente.get("/folhas/fila").json()["segundos_por_folha"], 8.0)

    def test_historico_velho_fica_de_fora(self):
        for i, t in enumerate((7200 + 20, 7200 + 10, 0)):   # duas de 2 h antes da mais recente
            f = self.folha(estado="processada", nome=f"p{i}.pdf")
            f.processada_em = AGORA - timedelta(seconds=t)
        self.db.commit()
        self.assertEqual(self.cliente.get("/folhas/fila").json()["segundos_por_folha"], 9.0)

    def test_pausa_entre_envios_nao_e_ritmo(self):
        for i, t in enumerate((600, 300, 10)):                 # intervalos de 5 min: pausas
            f = self.folha(estado="processada", nome=f"p{i}.pdf")
            f.processada_em = AGORA - timedelta(seconds=t)
        self.db.commit()
        self.assertEqual(self.cliente.get("/folhas/fila").json()["segundos_por_folha"], 9.0)


if __name__ == "__main__":
    unittest.main()
