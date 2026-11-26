"""A fila de leitura: reivindicação atômica, retomada ao subir e o progresso que a tela mostra.

Em 20/11 um reinício no meio de um envio deixou 79 folhas paradas, e dois servidores leram a
mesma fila ao mesmo tempo. O pipeline é trocado por um dublê: o objeto é a fila, não a leitura.
"""
import time
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

        def efeito(caminho, modelo, base):
            chamadas.append(caminho)
            if len(chamadas) == 1:
                raise RuntimeError("leitura quebrou")
            return [dict(LINHA)]

        # o erro do pipeline vira estado "erro" dentro de `_ler_e_gravar`
        self.ler(a.id, b.id, efeito=efeito)
        self.assertEqual(self.estado(a), "erro")
        self.assertEqual(self.estado(b), "processada")
        # A63: uma frase para quem usa, e a exceção só depois do separador que a tela esconde
        frase, detalhe = self.db.get(Folha, a.id).erro.split(folhas.DETALHE)
        self.assertIn("problema do sistema", frase)
        self.assertNotIn("RuntimeError", frase)
        self.assertEqual(detalhe, "RuntimeError: leitura quebrou")

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

    def test_folhas_em_erro_nao_entram_no_ritmo(self):
        # 22/11: um lote inteiro falhou em milissegundos, e a tela mostrou "0 s por folha"
        # durante a releitura. Quatro leituras a 8 s, e entre elas dez falhas a 0,05 s.
        for i, t in enumerate((32, 24, 16, 8)):
            f = self.folha(estado="processada", nome=f"p{i}.pdf")
            f.processada_em = AGORA - timedelta(seconds=t)
        for i in range(10):
            f = self.folha(estado="erro", nome=f"e{i}.pdf")
            f.processada_em = AGORA - timedelta(seconds=20, milliseconds=50 * i)
        self.db.commit()
        self.assertEqual(self.cliente.get("/folhas/fila").json()["segundos_por_folha"], 8.0)


class RelerErros(ComBanco):
    """A63: um lote inteiro em erro eram 62 cliques em "reavaliar"."""
    def test_rele_so_as_em_erro_do_grupo(self):
        ev_a, ev_b = self.evento("GELEIA", "A"), self.evento("PRATO", "B")
        erro_a = self.folha(ev_a, estado="erro", nome="a1.pdf")
        nao_e = self.folha(ev_a, estado="nao_e_folha", nome="a2.pdf")
        pronta = self.folha(ev_a, estado="processada", nome="a3.pdf")
        erro_b = self.folha(ev_b, estado="erro", nome="b1.pdf")
        with mock.patch.object(folhas, "_ler_e_gravar") as ler:
            r = self.cliente.post("/folhas/reler-erros", params={"grupo": "A"})
        self.assertEqual(r.json(), {"relidas": 1})
        self.assertEqual([c.args[0] for c in ler.call_args_list], [erro_a.id])
        self.db.expire_all()
        estados = {f.id: self.db.get(Folha, f.id).estado for f in (erro_a, nao_e, pronta, erro_b)}
        self.assertEqual(estados, {erro_a.id: "recebida", nao_e.id: "nao_e_folha",
                                   pronta.id: "processada", erro_b.id: "erro"})

    def test_sem_grupo_rele_todas_em_erro(self):
        a = self.folha(self.evento("GELEIA", "A"), estado="erro", nome="a.pdf")
        b = self.folha(None, estado="erro", nome="b.pdf")
        with mock.patch.object(folhas, "_ler_e_gravar"):
            self.assertEqual(self.cliente.post("/folhas/reler-erros").json(), {"relidas": 2})
        self.db.expire_all()
        self.assertEqual({self.db.get(Folha, f.id).estado for f in (a, b)}, {"recebida"})


class LeitorUnico(ComBanco):
    """`ler_fila`: um leitor só, que não segura conexão de quem chega depois (22/11).

    Antes, cada "reavaliar" agendava a própria leitura; 15 esperando a trava do pipeline
    esgotaram o pool do banco, e o servidor inteiro passou a responder 500.
    """
    def ler_fila(self, ler=None):
        ler = ler or mock.Mock()
        with mock.patch.object(folhas, "_ler_e_gravar", ler):
            folhas.ler_fila()
        return [c.args[0] for c in ler.call_args_list]

    def test_le_todas_na_ordem_da_fila(self):
        a = self.folha(estado="recebida", nome="a.pdf")
        b = self.folha(estado="recebida", nome="b.pdf")
        c = self.folha(estado="recebida", nome="c.pdf")
        pronta = self.folha(estado="processada", nome="d.pdf")
        # b entrou primeiro; c e a depois, nessa ordem
        for f, s in ((b, 30), (c, 20), (a, 10)):
            f.na_fila_desde = AGORA - timedelta(seconds=s)
        self.db.commit()
        self.assertEqual(self.ler_fila(), [b.id, c.id, a.id])
        self.assertNotIn(pronta.id, self.ler_fila())

    def test_com_alguem_lendo_volta_sem_ler(self):
        self.folha(estado="recebida")
        self.assertTrue(folhas._LEITOR.acquire(blocking=False))
        try:
            inicio = time.monotonic()
            self.assertEqual(self.ler_fila(), [])
            # voltar na hora é o ponto: esperar a vez, mesmo sem conexão, prende uma thread
            # do servidor por pedido, e o pool de threads também acaba
            self.assertLess(time.monotonic() - inicio, 0.2)
        finally:
            folhas._LEITOR.release()

    def test_folha_que_continua_na_fila_nao_prende_o_laco(self):
        # o dublê não muda o estado: sem `ja_tentadas`, o laço leria a mesma folha para sempre
        a = self.folha(estado="recebida", nome="a.pdf")
        b = self.folha(estado="recebida", nome="b.pdf")
        self.assertEqual(sorted(self.ler_fila()), sorted([a.id, b.id]))

    def test_uma_falha_nao_para_a_fila(self):
        a = self.folha(estado="recebida", nome="a.pdf")
        b = self.folha(estado="recebida", nome="b.pdf")
        ler = mock.Mock(side_effect=[RuntimeError("banco caiu"), None])
        with mock.patch.object(folhas.log, "exception"):
            self.assertEqual(sorted(self.ler_fila(ler)), sorted([a.id, b.id]))

    def test_folha_que_chega_durante_a_leitura_tambem_e_lida(self):
        a = self.folha(estado="recebida", nome="a.pdf")
        chegou = []

        def ler(folha_id):
            # enquanto a primeira é lida, outro envio põe uma folha na fila e pede leitura;
            # esse pedido volta na hora (há leitor), e quem tem de pegá-la é o leitor atual
            if not chegou:
                chegou.append(self.folha(estado="recebida", nome="b.pdf"))
                folhas.ler_fila()
            self.db.get(Folha, folha_id).estado = "processada"
            self.db.commit()

        self.assertEqual(self.ler_fila(mock.Mock(side_effect=ler)), [a.id, chegou[0].id])

    def test_releituras_pedidas_durante_a_leitura_nao_abrem_outro_leitor(self):
        # o cenário de 22/11 pela rota: enquanto uma folha é lida, chegam 20 "reavaliar".
        # Cada pedido tem de voltar sem ler (antes, cada um abria uma leitura própria, que
        # segurava uma conexão esperando a vez), e o leitor que já roda lê todas depois.
        ev = self.evento("GELEIA", "A")
        primeira = self.folha(ev, estado="recebida", nome="primeira.pdf")
        lista = [self.folha(ev, estado="erro", nome=f"f{i}.pdf") for i in range(20)]
        lendo, maximo, lidas = [0], [0], []

        def ler(folha_id):
            lendo[0] += 1
            maximo[0] = max(maximo[0], lendo[0])
            lidas.append(folha_id)
            if folha_id == primeira.id:
                for f in lista:
                    r = self.cliente.post(f"/folhas/{f.id}/reprocessar")
                    self.assertEqual(r.status_code, 200, r.text)
            s = self.Sessao()
            s.get(Folha, folha_id).estado = "processada"
            s.commit()
            s.close()
            lendo[0] -= 1

        with mock.patch.object(folhas, "_ler_e_gravar", mock.Mock(side_effect=ler)):
            folhas.ler_fila()
        self.assertEqual(maximo[0], 1)
        self.assertEqual(lidas, [primeira.id] + [f.id for f in lista])


if __name__ == "__main__":
    unittest.main()
