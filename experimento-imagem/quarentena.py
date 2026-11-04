"""Interface de revisão da quarentena — protótipo de teste.

A peça crítica da Fase 3. Como o gate manda o erro para quarentena em vez de
afirmar uma identidade errada (D03), **a usabilidade da revisão determina se o
sistema é viável**: um sistema que automatiza 40% e exige revisão penosa dos
outros 60% não se paga.

Isso hoje é uma afirmação sem número, então esta página também é instrumento de
medição: ela cronometra cada decisão. Se revisar 250 linhas leva 2 h, o sistema
não se paga; se leva 12 min, se paga fácil. O tempo por linha sai no JSON de
saída junto com as decisões.

## O que ela mostra, e por quê

  recorte da linha    a evidência primária. O que está no papel manda; a leitura
                      do OCR é palpite sobre ele.
  leitura por canal   matrícula e nome, com a confiança de cada caractere em
                      cor. Mostra ONDE a leitura é frágil.
  motivo da rejeição  score abaixo de θ e margem abaixo de δ são doenças
                      diferentes (A02/D03): a primeira é "não achei ninguém
                      parecido", a segunda é "achei dois e não sei qual". Pedem
                      atenção diferente do revisor.
  candidatos          ranqueados pelo score composto, com NOME e curso. O nome
                      é o que deixa o humano notar a troca de pessoa que o gate
                      não pega (A15).

## Por que aceites também aparecem

Colapsados, para auditoria por amostragem. A15 mostra que o aceite errado existe
e é confiante — score 1,000, margem folgada — porque um dígito errado caiu na
matrícula de outra pessoa real. Uma interface que só mostra quarentena é cega
exatamente para o modo de falha que nenhum limiar alcança.

## LGPD

Contém nome e matrícula reais. Sai em dados/, que o git ignora, e roda em
file://. Não subir para lugar nenhum.

Uso:
    python identificar_folha.py          # gera os registros crus
    python quarentena.py                 # gera a página
    python quarentena.py --alpha 1.0 --modo abstem
"""

from __future__ import annotations
import argparse
import base64
import json
import os
import sys

import cv2
import numpy as np

from image import Image
from align import Align
from sheet_cutter import SheetCutter
from process import folha as modelo_folha

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "experimento-fuzzy"))
from base_alunos import carregar_base          # noqa: E402
from normalizacao import normalizar            # noqa: E402
from fuzzy import ALPHA, THETA, DELTA          # noqa: E402

BASE_XLSX = "../experimento-fuzzy/dados/Lista de Alunos Campus Quixadá.xlsx"
REGISTROS = "dados/identificacao_folhas.json"
SAIDA = "dados/quarentena.html"

# THETA e DELTA vêm do `fuzzy` de propósito: a página tem que mostrar o mesmo
# gate que produziu os registros. Duplicar aqui já deixou a página anunciando
# θ=0,85 enquanto o pipeline rodava outro valor.

# Pelo mesmo motivo do bloco acima, o mapa folha -> modelo NÃO é duplicado aqui.
# A cópia que existia tinha as três folhas antigas e caía em `.get(folha, 3)`,
# então uma folha do modelo gerado seria recortada com a geometria da m1 sem
# erro nenhum — recorte torto é silencioso.
from identificar_folha import MODELO_DA_FOLHA   # noqa: E402


def motivo_rejeicao(score: float, margem: float,
                    theta: float = THETA, delta: float = DELTA) -> str:
    """Por que a linha caiu. As duas causas pedem atenção diferente.

    θ e δ caçam espécies diferentes de erro (D03): θ é o impostor confiante,
    δ é o vencedor ambíguo. Para o revisor a diferença é prática — score baixo
    significa "nenhum candidato se parece", e margem baixa significa "dois
    candidatos se parecem igualmente, olhe o nome".
    """
    baixo, apertado = score < theta, margem < delta
    if baixo and apertado:
        return "score baixo e margem apertada"
    if baixo:
        return f"score {score:.3f} abaixo de θ={theta}"
    if apertado:
        return f"margem {margem:.3f} abaixo de δ={delta}"
    return ""


def vizinhos_a_um_digito(matricula: str, matriculas: set[str]) -> list[str]:
    """Matrículas reais que distam UMA substituição da dada.

    É a medida de perigo de A15: um único erro de OCR transforma a matrícula de
    alguém na de outra pessoa real, o score sai 1,000, a margem sai folgada, e
    nem θ nem δ têm como perceber. 62,6% da base tem ao menos um vizinho assim.

    Serve para dirigir a auditoria. Na primeira revisão com um humano de
    verdade, os DOIS únicos erros foram linhas ACEITAS pelo gate, auditadas em
    1,5 s e 1,1 s — a amostragem cega não pega o que é confiante por
    construção. Sabendo quais aceites são perigosos, a interface pede atenção
    onde ela vale.
    """
    achados = []
    for i, atual in enumerate(matricula):
        for d in "0123456789":
            if d == atual:
                continue
            candidata = matricula[:i] + d + matricula[i + 1:]
            if candidata in matriculas:
                achados.append(candidata)
    return achados


def recortar_linhas(folha: str, modelo: int | str) -> dict[int, str]:
    """Recorta a faixa horizontal de cada linha da folha, em JPEG base64.

    Uma faixa por linha, da primeira célula da matrícula ao fim do nome: é a
    unidade que o revisor precisa ver inteira para decidir.
    """
    caminho = f"dados/{folha}.png"
    if not os.path.exists(caminho):
        return {}

    geo = modelo_folha(modelo)
    alinhada = Image().set_data(Align(*geo.warp).aruco(
        Image().load_from_file(caminho).data))
    h, w = alinhada.get_h_w()
    x0, x1 = geo.faixa_x(w)

    saida = {}
    for i in range(geo.linhas):
        ya, yb = geo.faixa_y(h, i, folga=2)
        recorte = alinhada.data[max(0, ya):yb, x0:x1]
        # JPEG e não PNG: a tira é uma foto de papel, com textura e gradiente de
        # iluminação, e o PNG sem perdas não comprime nada disso — 111 KB por
        # linha, ou mais de 1 GB nas 12.500 linhas de 500 folhas. Em JPEG q=82 a
        # mesma tira cabe em ~15 KB, e o que o revisor precisa enxergar é a
        # forma da letra, que sobrevive de sobra.
        ok, buf = cv2.imencode(".jpg", recorte, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            saida[i + 1] = base64.b64encode(buf).decode("ascii")
    return saida


def montar(registros: list[dict], alpha: float, modo: str,
           base: list[dict] | None = None) -> list[dict]:
    """Seleciona os registros de um (alpha, modo) e anexa imagem e alerta."""
    escolhidos = [r for r in registros
                  if r["alpha"] == alpha and r["modo"] == modo]
    matriculas = {a["matricula"] for a in (base or [])}

    cache: dict[str, dict[int, str]] = {}
    for r in escolhidos:
        folha = r["folha"]
        if folha not in cache:
            if folha not in MODELO_DA_FOLHA:
                raise KeyError(
                    f"{folha} não tem modelo de recorte registrado em "
                    f"identificar_folha.CONJUNTOS — falhar aqui é melhor que "
                    f"recortar com a geometria de outra versão da folha")
            cache[folha] = recortar_linhas(folha, MODELO_DA_FOLHA[folha])
        r["img"] = cache[folha].get(r["linha"], "")
        r["motivo_rejeicao"] = ("" if r["decisao"] == "aceito"
                                else motivo_rejeicao(r["score"], r["margem"]))

        # aceite perigoso: o campeão tem sósia a um dígito na base (A15)
        r["vizinhos"] = []
        if r["decisao"] == "aceito" and r.get("top3") and matriculas:
            r["vizinhos"] = vizinhos_a_um_digito(r["top3"][0]["matricula"],
                                                 matriculas)

    # os aceites perigosos sobem para logo depois da quarentena: é onde a
    # atenção do revisor ainda existe. Deixados no fim, foram auditados em
    # 1,1 s e o erro passou.
    def ordem(r: dict) -> tuple:
        if r["decisao"] == "quarentena":
            return (0, r["folha"], r["linha"])
        if r["decisao"] == "aceito" and r["vizinhos"]:
            return (1, r["folha"], r["linha"])
        return (2, r["folha"], r["linha"])

    escolhidos.sort(key=ordem)
    return escolhidos


ESTILO = """
:root{--bg:#fbfaf8;--fg:#1c1a17;--linha:#e2ddd5;--caixa:#fff;--sub:#6b645b;
      --ok:#1a7f4b;--duvida:#b06a00;--ruim:#b3261e;--marca:#3d5a8a;
      --aceito:#1a7f4b;--quar:#b06a00;--sel:#e8f2ea;--barra:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#eae7e1;--linha:#2f2c34;
      --caixa:#1e1d23;--sub:#9a938a;--ok:#57c98a;--duvida:#e0a34a;--ruim:#f2857d;
      --marca:#8fabd8;--aceito:#57c98a;--quar:#e0a34a;--sel:#1d2f24;--barra:#1e1d23}}
*{box-sizing:border-box}
body{margin:0;padding:0 0 80px;background:var(--bg);color:var(--fg);
     font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.topo{position:sticky;top:0;z-index:10;background:var(--barra);
      border-bottom:1px solid var(--linha);padding:12px 24px;
      display:flex;flex-wrap:wrap;gap:16px;align-items:center}
h1{font-size:17px;margin:0;flex:1 1 auto}
.contador{font-family:ui-monospace,Menlo,monospace;font-size:14px;color:var(--sub)}
.btn{font:inherit;font-size:13px;padding:6px 12px;border:1px solid var(--linha);
     border-radius:6px;background:var(--caixa);color:var(--fg);cursor:pointer}
.btn:hover{border-color:var(--marca)}
.btn.primario{background:var(--marca);color:#fff;border-color:var(--marca)}
main{padding:20px 24px;max-width:1100px}
.aviso{background:var(--caixa);border:1px solid var(--linha);border-left:3px solid var(--duvida);
       border-radius:8px;padding:12px 14px;margin-bottom:20px;font-size:14px;max-width:80ch}
.secao{font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
       color:var(--sub);margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--linha)}
.linha{background:var(--caixa);border:1px solid var(--linha);border-radius:10px;
       padding:12px 14px;margin-bottom:12px}
.linha.resolvida{border-left:3px solid var(--ok)}
.linha.aceito{opacity:.62}
.linha.aceito:hover{opacity:1}
.cab{display:flex;flex-wrap:wrap;gap:14px;align-items:baseline;margin-bottom:8px}
.id{font-family:ui-monospace,Menlo,monospace;font-size:13px;color:var(--sub)}
.tag{font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;
     border-radius:99px;border:1px solid currentColor}
.tag.q{color:var(--quar)}.tag.a{color:var(--aceito)}
.porque{font-size:13px;color:var(--quar)}
.tira{overflow-x:auto;margin:2px 0 10px}
.tira img{display:block;height:46px;image-rendering:crisp-edges;border-radius:4px;
          background:#fff;min-width:0}
.leituras{display:flex;flex-wrap:wrap;gap:20px;margin-bottom:8px}
.rot{color:var(--sub);font-size:11px;letter-spacing:.05em;text-transform:uppercase;
     display:block;margin-bottom:1px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:17px;letter-spacing:.1em}
.ok{color:var(--ok)}.duvida{color:var(--duvida)}.ruim{color:var(--ruim)}
.cands{margin:8px 0 0;padding:0;list-style:none}
.cands li{border-top:1px dashed var(--linha)}
.cands li:first-child{border-top:0}
.cand{display:flex;gap:10px;align-items:baseline;width:100%;text-align:left;
      padding:7px 8px;border:0;border-radius:6px;background:none;color:inherit;
      font:inherit;font-size:14px;cursor:pointer}
.cand:hover{background:var(--sel)}
.cand.escolhido{background:var(--sel);box-shadow:inset 3px 0 0 var(--ok)}
.score{font-family:ui-monospace,Menlo,monospace;color:var(--sub);font-size:12px;min-width:3.4em}
.acoes{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;
       padding-top:10px;border-top:1px solid var(--linha)}
.busca{font:inherit;font-size:14px;padding:6px 10px;border:1px solid var(--linha);
       border-radius:6px;background:var(--bg);color:var(--fg);flex:1 1 240px;min-width:0}
.achados{margin:6px 0 0;padding:0;list-style:none;max-height:180px;overflow-y:auto}
.decidido{font-size:13px;color:var(--ok);margin-top:8px}
.vazio{color:var(--sub);font-size:14px;padding:12px 0}
.linha.perigo{opacity:1;border-left:3px solid var(--ruim)}
.alerta{font-size:13px;color:var(--ruim);margin:6px 0 2px}
.alerta code{font-family:ui-monospace,Menlo,monospace;font-size:12px}
"""

SCRIPT = """
const DEC = 'olhodigital.quarentena.' + CHAVE;
let estado = {};
try { estado = JSON.parse(localStorage.getItem(DEC) || '{}'); } catch (e) { estado = {}; }

// Cronometragem: o instante em que a linha entrou em foco. A afirmacao de que
// "a usabilidade da revisao determina a viabilidade" precisa de numero, e o
// numero e este.
let foco = null, focoEm = 0;

function salvar() {
  try { localStorage.setItem(DEC, JSON.stringify(estado)); } catch (e) {}
  render();
}

function marcarFoco(id) {
  if (foco === id) return;
  foco = id; focoEm = performance.now();
}

function decidir(id, escolha, rotulo) {
  const ms = (foco === id && focoEm) ? Math.round(performance.now() - focoEm) : null;
  const antes = estado[id];
  estado[id] = {
    escolha: escolha, rotulo: rotulo,
    ms: (antes && antes.ms) ? antes.ms : ms,      // guarda o primeiro tempo
    revisoes: (antes ? (antes.revisoes || 1) + 1 : 1),
    em: new Date().toISOString()
  };
  foco = null; focoEm = 0;
  salvar();
}

function limpar(id) { delete estado[id]; salvar(); }

function buscar(id, termo) {
  const alvo = document.getElementById('achados-' + id);
  const t = termo.trim().toUpperCase();
  if (t.length < 2) { alvo.innerHTML = ''; return; }
  const hits = BASE.filter(p => p.m.includes(t) || p.n.includes(t)).slice(0, 8);
  alvo.innerHTML = hits.length ? hits.map(p =>
    '<li><button class="cand" onclick="decidir(\\'' + id + '\\',\\'' + p.m + '\\',\\'' +
    p.n.replace(/'/g, '') + '\\')"><span class="score">busca</span>' +
    '<strong>' + p.m + '</strong><span>' + p.n + '</span>' +
    '<span class="score">' + (p.c || '') + '</span></button></li>').join('')
    : '<li class="vazio">nada encontrado</li>';
}

function render() {
  let feitas = 0;
  LINHAS.forEach(id => {
    const el = document.getElementById('linha-' + id);
    const d = estado[id];
    if (!el) return;
    el.classList.toggle('resolvida', !!d);
    const aviso = document.getElementById('decidido-' + id);
    if (d) {
      feitas++;
      aviso.textContent = '✓ ' + (d.escolha === '?' ? 'nao identificado'
        : d.escolha + ' — ' + d.rotulo) + (d.ms ? '  (' + (d.ms / 1000).toFixed(1) + ' s)' : '');
      aviso.innerHTML += ' <button class="btn" onclick="limpar(\\'' + id + '\\')">desfazer</button>';
    } else { aviso.textContent = ''; }
    el.querySelectorAll('.cand[data-mat]').forEach(b =>
      b.classList.toggle('escolhido', !!d && d.escolha === b.dataset.mat));
  });
  const tempos = Object.values(estado).filter(d => d.ms).map(d => d.ms);
  const med = tempos.length
    ? (tempos.reduce((a, b) => a + b, 0) / tempos.length / 1000).toFixed(1) : '—';
  document.getElementById('contador').textContent =
    feitas + '/' + LINHAS.length + ' revisadas · mediana ' + med + ' s/linha';
}

function exportar() {
  const dados = {
    gerado_em: new Date().toISOString(), config: CHAVE,
    total_linhas: LINHAS.length,
    decisoes: LINHAS.filter(id => estado[id]).map(id => Object.assign({id: id}, estado[id]))
  };
  const blob = new Blob([JSON.stringify(dados, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'revisao_' + ROTULO + '_' + CHAVE + '.json';
  a.click();
}

document.addEventListener('DOMContentLoaded', render);
"""


def cor(c: float, motivo: str) -> str:
    """Confiança do caractere em cor. Mostra ONDE a leitura é frágil."""
    if motivo in ("vazia", "sem_caixa"):
        return "sub"
    if c >= 0.999:
        return "ok"
    if c >= 0.6:
        return "duvida"
    return "ruim"


def leitura_colorida(texto: str, confs: list[float], motivos: list[str]) -> str:
    """Pinta cada caractere pela confiança do KNN naquela célula."""
    saida = []
    for i, ch in enumerate(texto):
        c = confs[i] if i < len(confs) else 0.0
        m = motivos[i] if i < len(motivos) else "lido"
        saida.append(f'<span class="{cor(c, m)}">{esc(ch)}</span>')
    return "".join(saida) or '<span class="sub">—</span>'


def esc(s: str) -> str:
    import html as _h
    return _h.escape(str(s or ""))


def render(linhas: list[dict], base: list[dict], alpha: float, modo: str) -> str:
    chave = f"a{alpha}_{modo}"
    # O nome do arquivo exportado leva as FOLHAS, não só a configuração. Revisar
    # duas folhas na mesma configuração produzia dois `revisao_a0.5_chuta.json`
    # na pasta de downloads, e o segundo substituía o primeiro sem aviso — nome
    # que não descreve o conteúdo, o mesmo defeito do cache de células.
    #
    # Só o nome do download muda. `chave` continua sendo a chave do localStorage:
    # as decisões são indexadas por `folha-linha`, que já é único entre folhas,
    # e mexer nela descartaria revisão em andamento.
    rotulo = "_".join(sorted({r["folha"] for r in linhas}))
    quarentena = [r for r in linhas if r["decisao"] == "quarentena"]
    aceitos = [r for r in linhas if r["decisao"] == "aceito"]
    anuladas = [r for r in linhas if r["decisao"] == "anulada"]

    # base embutida para a busca livre: ~1.334 pessoas, ~80 KB
    base_js = json.dumps([{"m": p["matricula"], "n": normalizar(p["nome"]),
                           "c": p.get("curso") or ""} for p in base],
                         ensure_ascii=False, separators=(",", ":"))
    ids = [f'{r["folha"]}-{r["linha"]:02d}' for r in linhas]

    p = [f'<meta charset="utf-8"><title>Revisão de quarentena</title>',
         f'<style>{ESTILO}</style>',
         '<div class="topo">',
         f'<h1>Revisão de quarentena <span class="id">· α={alpha} · {esc(modo)} '
         f'· θ={THETA} δ={DELTA}</span></h1>',
         '<span class="contador" id="contador"></span>',
         '<button class="btn primario" onclick="exportar()">exportar decisões</button>',
         '</div><main>',
         '<div class="aviso"><strong>Contém dado pessoal real.</strong> Gerado em '
         '<code>dados/</code>, que o git ignora. Não subir para lugar nenhum.<br>'
         'As decisões ficam no navegador (localStorage) até você exportar.</div>']

    def bloco(r: dict, aceito: bool) -> None:
        rid = f'{r["folha"]}-{r["linha"]:02d}'
        classe = "linha aceito" if aceito else "linha"
        if r.get("vizinhos"):
            classe += " perigo"
        p.append(f'<div class="{classe}" id="linha-{rid}" '
                 f'onmouseenter="marcarFoco(\'{rid}\')">')
        p.append('<div class="cab">')
        p.append(f'<span class="id">{esc(r["folha"])} · linha {r["linha"]:02d}</span>')
        p.append(f'<span class="tag {"a" if aceito else "q"}">'
                 f'{"aceito" if aceito else "quarentena"}</span>')
        if r.get("motivo_rejeicao"):
            p.append(f'<span class="porque">{esc(r["motivo_rejeicao"])}</span>')
        p.append(f'<span class="id">score {r["score"]:.3f} · margem {r["margem"]:.3f}</span>')
        p.append('</div>')

        if r.get("vizinhos"):
            v = ", ".join(esc(x) for x in r["vizinhos"][:5])
            p.append(f'<div class="alerta">⚠ o campeão tem '
                     f'{len(r["vizinhos"])} matrícula(s) real(is) a UM dígito: '
                     f'<code>{v}</code> — um erro de leitura cai em pessoa '
                     f'existente sem o gate perceber. <strong>Confira o nome.</strong></div>')
        if r.get("img"):
            p.append(f'<div class="tira"><img src="data:image/jpeg;base64,{r["img"]}" '
                     f'alt="linha {r["linha"]} da folha {esc(r["folha"])}"></div>')

        p.append('<div class="leituras">')
        p.append('<div><span class="rot">matrícula lida</span><span class="mono">'
                 + leitura_colorida(r["leitura"], r.get("confiancas", []),
                                    r.get("motivos", [])) + '</span></div>')
        p.append('<div><span class="rot">nome lido</span><span class="mono">'
                 + leitura_colorida(r.get("leitura_nome", ""),
                                    r.get("confiancas_nome", []),
                                    r.get("motivos_nome", [])) + '</span></div>')
        p.append('</div>')

        p.append('<ul class="cands">')
        for c in r.get("top3", []):
            rot = esc(c["nome"])
            p.append(f'<li><button class="cand" data-mat="{esc(c["matricula"])}" '
                     f'onclick="decidir(\'{rid}\',\'{esc(c["matricula"])}\',\'{rot}\')">'
                     f'<span class="score">{c["score"]:.3f}</span>'
                     f'<strong>{esc(c["matricula"])}</strong><span>{rot}</span>'
                     f'<span class="score">{esc(c.get("curso"))}</span></button></li>')
        p.append('</ul>')

        p.append('<div class="acoes">')
        p.append(f'<input class="busca" placeholder="buscar na base por nome ou matrícula…" '
                 f'oninput="buscar(\'{rid}\', this.value)">')
        p.append(f'<button class="btn" onclick="decidir(\'{rid}\',\'?\',\'\')">'
                 f'não identificado</button>')
        p.append('</div>')
        p.append(f'<ul class="achados" id="achados-{rid}"></ul>')
        p.append(f'<div class="decidido" id="decidido-{rid}"></div>')
        p.append('</div>')

    p.append(f'<div class="secao">quarentena — {len(quarentena)} linhas para revisar</div>')
    if quarentena:
        for r in quarentena:
            bloco(r, aceito=False)
    else:
        p.append('<p class="vazio">Nenhuma linha em quarentena nesta configuração.</p>')

    if anuladas:
        p.append(f'<div class="secao">anuladas pelo protocolo — {len(anuladas)}</div>')
        for r in anuladas:
            bloco(r, aceito=True)

    perigosos = [r for r in aceitos if r.get("vizinhos")]
    tranquilos = [r for r in aceitos if not r.get("vizinhos")]

    if perigosos:
        p.append(f'<div class="secao">aceites com sósia na base — {len(perigosos)}, '
                 f'confira o nome (A15)</div>')
        p.append('<div class="aviso">Estes o gate aceitou com score alto, mas a '
                 'matrícula escolhida tem vizinho a um dígito que também é pessoa '
                 'real. É o único modo de falha que nem θ nem δ alcançam, e na '
                 'primeira revisão com humano ele passou batido em 1,1 s. '
                 '<strong>O nome é a única evidência que denuncia a troca.</strong></div>')
        for r in perigosos:
            bloco(r, aceito=True)

    p.append(f'<div class="secao">demais aceites — {len(tranquilos)}</div>')
    for r in tranquilos:
        bloco(r, aceito=True)

    p.append('</main><script>')
    p.append(f'const CHAVE = {json.dumps(chave)};')
    p.append(f'const ROTULO = {json.dumps(rotulo)};')
    p.append(f'const LINHAS = {json.dumps(ids)};')
    p.append(f'const BASE = {base_js};')
    p.append(SCRIPT)
    p.append('</script>')
    return "\n".join(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gera a página de revisão de quarentena.")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--modo", default="chuta", choices=("chuta", "abstem"))
    ap.add_argument("--registros", default=REGISTROS)
    ap.add_argument("--saida", default=SAIDA)
    args = ap.parse_args()

    if not os.path.exists(args.registros):
        raise SystemExit(f"{args.registros} não existe — rode identificar_folha.py antes")

    with open(args.registros) as f:
        registros = json.load(f)

    alphas = sorted({r["alpha"] for r in registros})
    if args.alpha not in alphas:
        raise SystemExit(f"alpha {args.alpha} não está nos registros (há {alphas})")

    print(f"base: carregando...")
    base = carregar_base(BASE_XLSX)

    linhas = montar(registros, args.alpha, args.modo, base)
    q = sum(1 for r in linhas if r["decisao"] == "quarentena")
    a = sum(1 for r in linhas if r["decisao"] == "aceito")
    n = sum(1 for r in linhas if r["decisao"] == "anulada")

    os.makedirs("dados", exist_ok=True)
    with open(args.saida, "w") as f:
        f.write(render(linhas, base, args.alpha, args.modo))

    tam = os.path.getsize(args.saida) / 1024
    perig = sum(1 for r in linhas if r.get("vizinhos"))
    print(f"alpha={args.alpha} modo={args.modo}: {q} em quarentena, {a} aceitas"
          + (f" ({perig} com sósia a 1 dígito — auditoria dirigida)" if perig else "")
          + (f", {n} anuladas" if n else ""))
    print(f"{len(linhas)} linhas em {args.saida} ({tam:.0f} KB)")
    print(f"\nabrir com: xdg-open {args.saida}")
    print("contém dado pessoal real — não subir para lugar nenhum (LGPD)")
