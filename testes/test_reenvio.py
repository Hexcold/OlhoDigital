"""D30: o mesmo papel escaneado mais de uma vez conta uma vez só.

Folhas montadas no banco com leituras prontas: o objeto do teste é a detecção, qual cópia vale
e o que conta na frequência. Dados inventados, matrículas 9xxxxx.
"""
import unittest
from unittest import mock

from test_grupos import ComBanco                               # noqa: F401  isola o ambiente

from api import pipeline, reenvio
from api.rotas import folhas as rotas_folhas
from api.tabelas import Folha, Linha

PAPEL_A = {1: ("900001", "ZULEICA AMARANTE FROTA"), 2: ("900002", "QUITERIA BRANDAO VIEGAS"),
           3: ("912345", "OTONIEL BARCELAR FURTADO")}
# o mesmo papel escaneado de novo: o OCR troca um dígito aqui, uma letra ali
PAPEL_A_DE_NOVO = {1: ("900001", "ZULEICA AMARANTE FROTA"),
                   2: ("900007", "QUITERIA BRANDAO VIEGAS"),
                   3: ("912345", "OTONIEL BARCELAR FURTAOO")}
PAPEL_B = {1: ("912345", "OTONIEL BARCELAR FURTADO"), 2: ("900001", "ZULEICA AMARANTE FROTA")}


class Semelhanca(unittest.TestCase):
    def test_mesmo_papel_bate(self):
        self.assertEqual(reenvio.semelhanca(PAPEL_A, PAPEL_A), 1.0)
        self.assertGreaterEqual(reenvio.semelhanca(PAPEL_A, PAPEL_A_DE_NOVO), reenvio.LIMIAR_REENVIO)

    def test_papeis_diferentes_nao_batem(self):
        # as mesmas pessoas, em posições diferentes: não é o mesmo papel
        self.assertEqual(reenvio.semelhanca(PAPEL_A, PAPEL_B), 0.0)

    def test_precisa_dos_dois_canais(self):
        # o mesmo nome na mesma posição com outra matrícula não é a mesma linha, e vice-versa
        outra_matricula = {n: ("123456", nm) for n, (m, nm) in PAPEL_A.items()}
        outro_nome = {n: (m, "XAVIER YURI WANDERLEY") for n, (m, nm) in PAPEL_A.items()}
        self.assertEqual(reenvio.semelhanca(PAPEL_A, outra_matricula), 0.0)
        self.assertEqual(reenvio.semelhanca(PAPEL_A, outro_nome), 0.0)

    def test_funciona_num_processo_novo(self):
        # Num processo em que ninguém importou `identificar_folha` antes, `fuzzy` não estava
        # no caminho e a detecção quebrava. A bateria não via: algum teste já o tinha importado.
        import subprocess
        import sys
        from pathlib import Path
        raiz = Path(__file__).resolve().parent.parent
        codigo = ("import sys; sys.path.insert(0, %r)\n"
                  "from api import reenvio\n"
                  "print(reenvio.semelhanca({1: ('900001', 'ANA')}, {1: ('900001', 'ANA')}))"
                  % str(raiz))
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                           cwd="/", env={"OLHODIGITAL_DATABASE_URL": "sqlite://",
                                         "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertEqual(r.stdout.strip(), "1.0")

    def test_sem_linhas_nao_afirma(self):
        self.assertIsNone(reenvio.semelhanca({}, {}))


class Copias(ComBanco):
    def papel(self, evento, leituras, aceites=None, nome="lote.pdf", pagina=1):
        f = self.folha(evento, nome=nome, pagina=pagina)
        aceitas = set(leituras) if aceites is None else set(aceites)
        for n, (m, nm) in leituras.items():
            self.db.add(Linha(folha_id=f.id, numero=n, leitura_matricula=m, leitura_nome=nm,
                              decisao="aceito" if n in aceitas else "quarentena",
                              matricula_sugerida=m if n in aceitas else None,
                              score=0.9, margem=0.5, candidatos=[]))
        f.linhas_preenchidas = len(leituras)
        self.db.commit()
        return f

    def detectar(self, folha):
        r = reenvio.detectar(self.db, folha)
        self.db.commit()
        self.db.expire_all()
        return r

    def copia_de(self, folha):
        return self.db.get(Folha, folha.id).copia_de

    def test_tres_escaneamentos_contam_uma_vez(self):
        ev = self.evento("TESTEUM", "EU")
        a = self.papel(ev, PAPEL_A, nome="lote1.pdf")
        b = self.papel(ev, PAPEL_A_DE_NOVO, nome="lote2.pdf")
        c = self.papel(ev, PAPEL_A, nome="lote3.pdf")
        for f in (a, b, c):
            self.detectar(f)
        valem = [f for f in (a, b, c) if self.copia_de(f) is None]
        self.assertEqual(len(valem), 1)
        for f in (a, b, c):
            if f.id != valem[0].id:
                self.assertEqual(self.copia_de(f), valem[0].id)
        freq = self.cliente.get("/grupos/EU/frequencia.json").json()
        self.assertEqual(len(freq["frequencia"]), 3)          # 3 assinaturas, não 9
        self.assertEqual(len(freq["eventos"][0]["folhas"]), 1)

    def test_o_grupo_conta_copias_e_leituras_unicas(self):
        um, dois = self.evento("TESTEUM", "EU"), self.evento("TESTEDOIS", "EU")
        a = self.papel(um, PAPEL_A, nome="lote1.pdf")
        b = self.papel(um, PAPEL_A, nome="lote2.pdf")
        c = self.papel(um, PAPEL_A, nome="lote3.pdf")
        self.papel(dois, PAPEL_B, nome="lote1.pdf", pagina=2)   # escaneado uma vez só
        for f in (a, b, c):
            self.detectar(f)
        g = next(x for x in self.cliente.get("/grupos").json() if x["nome"] == "EU")
        self.assertEqual((g["folhas"], g["copias"], g["com_copia"], g["leitura_unica"]),
                         (4, 2, 1, 1))

    def test_empatado_fica_a_que_ja_estava(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        self.detectar(b)
        self.assertIsNone(self.copia_de(a))
        self.assertEqual(self.copia_de(b), a.id)
        self.assertEqual(self.db.get(Folha, b.id).semelhanca_copia, 1.0)

    def test_vale_a_de_mais_aceites(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A, aceites=[1])
        b = self.papel(ev, PAPEL_A, nome="b.pdf")              # 3 aceites
        self.detectar(b)
        self.assertIsNone(self.copia_de(b))
        self.assertEqual(self.copia_de(a), b.id)

    def test_trabalho_humano_nao_troca_de_lado(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A, aceites=[1])
        linha = next(l for l in a.linhas if l.numero == 2)
        self.revisar(linha, "900002")
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        self.detectar(b)
        self.assertIsNone(self.copia_de(a))
        self.assertEqual(self.copia_de(b), a.id)

    def test_eventos_diferentes_nao_sao_copia(self):
        a = self.papel(self.evento("TESTEUM"), PAPEL_A)
        b = self.papel(self.evento("TESTEDOIS"), PAPEL_A, nome="b.pdf")
        self.assertIsNone(self.detectar(b))
        self.assertIsNone(self.copia_de(b))
        self.assertIsNone(self.copia_de(a))

    def test_papeis_diferentes_do_mesmo_evento_contam_os_dois(self):
        ev = self.evento("TESTEUM")
        self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_B, nome="b.pdf")
        self.assertIsNone(self.detectar(b))

    def test_situacao_e_o_que_a_quarentena_do_grupo_ve(self):
        ev = self.evento("TESTEUM", "EU")
        a = self.papel(ev, PAPEL_A, aceites=[])
        b = self.papel(ev, PAPEL_A, aceites=[], nome="b.pdf")
        self.detectar(b)
        situacoes = {f["id"]: f["situacao"] for f in self.cliente.get("/folhas").json()}
        self.assertEqual(situacoes[b.id], "copia")
        self.assertEqual(situacoes[a.id], "aguardando_revisao")
        fila = self.cliente.get("/grupos/EU/quarentena").json()
        self.assertEqual(len(fila), 3)                         # só as da folha que conta
        r = self.cliente.get(f"/folhas/{b.id}").json()
        self.assertEqual(r["copia_de_rotulo"], "lote.pdf, página 1")

    def test_usar_esta_troca_quem_vale(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        c = self.papel(ev, PAPEL_A, nome="c.pdf")
        self.detectar(b)
        self.detectar(c)
        r = self.cliente.post(f"/folhas/{c.id}/usar-copia")
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertIsNone(self.copia_de(c))
        self.assertEqual(self.copia_de(a), c.id)
        self.assertEqual(self.copia_de(b), c.id)
        self.assertEqual(self.cliente.post(f"/folhas/{c.id}/usar-copia").status_code, 409)

    def test_nao_e_copia_conta_e_nao_e_remarcada(self):
        ev = self.evento("TESTEUM")
        self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        self.detectar(b)
        r = self.cliente.post(f"/folhas/{b.id}/nao-e-copia")
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertIsNone(self.detectar(self.db.get(Folha, b.id)))
        self.assertIsNone(self.copia_de(b))

    def test_apagar_a_que_vale_promove_a_copia(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        c = self.papel(ev, PAPEL_A, nome="c.pdf")
        self.detectar(b)
        self.detectar(c)
        r = self.cliente.delete(f"/folhas/{a.id}")
        self.assertIn(r.status_code, (200, 204), r.text)
        self.db.expire_all()
        restam = [f for f in (b, c) if self.copia_de(f) is None]
        self.assertEqual(len(restam), 1)                       # uma passa a contar, não as duas

    def test_mudar_o_evento_refaz_a_relacao(self):
        ev, outro = self.evento("TESTEUM"), self.evento("TESTEDOIS")
        self.papel(ev, PAPEL_A)
        b = self.papel(ev, PAPEL_A, nome="b.pdf")
        self.detectar(b)
        r = self.cliente.post(f"/folhas/{b.id}/evento", json={"evento_id": outro.id})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertIsNone(self.copia_de(b))

    def test_a_leitura_do_servidor_detecta(self):
        ev = self.evento("TESTEUM")
        a = self.papel(ev, PAPEL_A)
        b = self.folha(ev, estado="recebida", nome="b.pdf")
        linhas = [{"numero": n, "leitura_matricula": m, "leitura_nome": nm,
                   "confiancas_matricula": [], "confiancas_nome": [],
                   "motivos_matricula": ["lido"] * 6, "motivos_nome": ["lido"] * 7,
                   "score": 0.9, "margem": 0.5, "decisao": "aceito", "matricula_sugerida": m,
                   "candidatos": [], "duplicada_na_folha": False}
                  for n, (m, nm) in PAPEL_A.items()]
        recursos = {"ponto": {"alpha": 0.5, "theta": 0.6, "delta": 0.08, "metrica_nome": "x",
                              "tau_nome": 0.65, "recorte": "v3"},
                    "modelo_matricula": "m", "modelo_letras": "l"}
        with mock.patch.object(rotas_folhas, "sessao", self.Sessao), \
                mock.patch.object(pipeline, "conferir_marcadores", return_value=4), \
                mock.patch.object(pipeline, "carregar_recursos", return_value=recursos), \
                mock.patch.object(pipeline, "processar", return_value=linhas), \
                mock.patch.object(pipeline, "recortar_para_revisao"), \
                mock.patch.object(rotas_folhas, "_vincular_evento"):
            rotas_folhas._ler_e_gravar(b.id)
        self.db.expire_all()
        self.assertEqual(self.copia_de(b), a.id)


if __name__ == "__main__":
    unittest.main()
