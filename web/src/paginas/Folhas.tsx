import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ErroApi } from "../api";
import { ApagarEmMassa } from "../componentes/ApagarEmMassa";
import type { Fila, Folha, SugestaoEventos } from "../tipos";

/** O canto de cada marcador da folha, como o alinhamento os usa (D29). */
const CANTOS: Record<number, string> = {
  0: "superior esquerdo",
  1: "superior direito",
  2: "inferior esquerdo",
  3: "inferior direito",
};

const ROTULO: Record<Folha["situacao"], { texto: string; classe: string }> = {
  recebida: { texto: "na fila", classe: "bg-slate-100 text-slate-700" },
  processando: { texto: "lendo…", classe: "bg-amber-100 text-amber-800" },
  em_branco: { texto: "em branco", classe: "bg-slate-100 text-slate-600" },
  aguardando_revisao: {
    texto: "esperando revisão",
    classe: "bg-amber-100 text-amber-900",
  },
  pronta: { texto: "pronta, sem revisão", classe: "bg-sky-100 text-sky-900" },
  revisada: { texto: "revisada", classe: "bg-emerald-100 text-emerald-800" },
  copia: { texto: "outro escaneamento", classe: "bg-violet-100 text-violet-800" },
  suspeita: { texto: "suspeita", classe: "bg-orange-100 text-orange-900" },
  nao_e_folha: {
    texto: "não é folha de presença",
    classe: "bg-slate-200 text-slate-700",
  },
  erro: { texto: "erro", classe: "bg-rose-100 text-rose-800" },
};

function Selo({ situacao }: { situacao: Folha["situacao"] }) {
  const { texto, classe } = ROTULO[situacao];
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${classe}`}>
      {texto}
    </span>
  );
}

const ORIGEM: Record<NonNullable<Folha["origem_vinculo"]>, string> = {
  automatico: "pela chave lida",
  confirmado: "confirmado por uma pessoa",
  corrigido: "corrigido por uma pessoa",
  informado: "informado no envio",
  removido: "tirado do evento por uma pessoa",
};

/** O evento da folha, ou a bandeja quando ela não foi ligada a nenhum.
 *
 *  Folha sem evento não some nem cai no palpite: fica à vista até alguém
 *  decidir, olhando a chave como ela foi escaneada. Errar o evento não aparece
 *  em precisão nem em cobertura, então essa decisão não pode ser silenciosa. */
function Vinculo({ folha, aoMudar }: { folha: Folha; aoMudar: () => void }) {
  const [trocando, setTrocando] = useState(false);
  const [sugestao, setSugestao] = useState<SugestaoEventos | null>(null);
  const [escolha, setEscolha] = useState("");
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [semTira, setSemTira] = useState(false);
  const naBandeja = folha.evento_id === null;
  const aberto = naBandeja || trocando;

  useEffect(() => {
    if (!aberto) return;
    let vivo = true;
    api
      .eventosSugeridos(folha.id)
      .then((s) => {
        if (!vivo) return;
        setSugestao(s);
        setEscolha(String(folha.evento_id ?? s.candidatos[0]?.id ?? ""));
      })
      .catch((e) => vivo && setErro(e instanceof ErroApi ? e.message : String(e)));
    return () => {
      vivo = false;
    };
  }, [aberto, folha.id, folha.evento_id]);

  async function vincular() {
    setOcupado(true);
    setErro("");
    try {
      await api.vincularEvento(folha.id, Number(escolha));
      setTrocando(false);
      aoMudar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  }

  if (!aberto) {
    return (
      <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-sm text-slate-600">
        <span>Evento</span>
        <strong className="font-mono text-slate-900">{folha.evento_chave}</strong>
        {folha.origem_vinculo && (
          <span className="text-xs text-slate-500">
            {ORIGEM[folha.origem_vinculo]}
          </span>
        )}
        <button
          type="button"
          onClick={() => setTrocando(true)}
          className="text-xs text-slate-500 underline underline-offset-2 hover:text-marca-700"
        >
          trocar
        </button>
      </p>
    );
  }

  return (
    <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
      <p className="font-medium">
        {naBandeja ? "Sem evento" : "Trocar o evento"}
        {folha.chave_lida && (
          <span className="ml-2 font-mono font-normal">
            lido: {folha.chave_lida}
          </span>
        )}
      </p>
      {sugestao && <p className="mt-0.5 text-xs">{sugestao.motivo}.</p>}
      {sugestao?.aviso && (
        // D33: a chave já foi de um evento encerrado; a folha pode ser atrasada
        <p className="mt-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900">
          {sugestao.aviso}
        </p>
      )}
      {!semTira && (
        <img
          src={api.urlTiraChave(folha.id)}
          alt="A chave como foi escaneada"
          onError={() => setSemTira(true)}
          className="mt-2 max-w-full rounded border border-amber-200"
        />
      )}
      {erro && <p className="mt-2 text-xs text-rose-800">{erro}</p>}
      {sugestao && sugestao.candidatos.length === 0 ? (
        <p className="mt-2 text-xs">
          Nenhum evento aberto. Cadastre em{" "}
          <Link to="/eventos" className="underline">
            eventos
          </Link>
          .
        </p>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            aria-label="Evento desta folha"
            value={escolha}
            onChange={(e) => setEscolha(e.target.value)}
            className="min-w-0 max-w-full rounded border border-amber-300 bg-white px-2 py-1 text-sm"
          >
            {sugestao?.candidatos.map((c) => (
              <option key={c.id} value={c.id}>
                {c.chave} · {c.nome}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={vincular}
            disabled={ocupado || !escolha}
            className="rounded bg-marca-500 px-3 py-1 text-sm font-medium text-white hover:bg-marca-600 disabled:opacity-50"
          >
            {ocupado ? "…" : "Vincular"}
          </button>
          {trocando && (
            <button
              type="button"
              onClick={() => setTrocando(false)}
              className="text-xs underline"
            >
              cancelar
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Cartao({
  folha,
  aoMudar,
}: {
  folha: Folha;
  aoMudar: () => void;
}) {
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [modelos, setModelos] = useState<string[]>([]);
  const seletor = useRef<HTMLInputElement>(null);
  const temLinhas =
    folha.estado === "processada" || folha.estado === "suspeita";
  const revisada = folha.situacao === "revisada";

  /** Tenta sem forçar; só pergunta se o SERVIDOR disser que há decisões humanas a perder.
   *  Antes, a tela adivinhava pelo número de pendentes, e uma folha em erro (sem linha
   *  nenhuma) avisava que "as decisões humanas são descartadas" (A63). */
  async function comConfirmacao(acao: (forcar: boolean) => Promise<unknown>, oQue: string) {
    setOcupado(true);
    setErro(null);
    try {
      try {
        await acao(false);
      } catch (e) {
        if (!(e instanceof ErroApi && e.status === 409 && /decis/.test(e.message))) throw e;
        if (!confirm(`${oQue} "${folha.nome_original}"? ${e.message.split(";")[0]}.`)) return;
        await acao(true);
      }
      aoMudar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
      setModelos([]);
    }
  }

  const reavaliar = (modelo?: string) =>
    comConfirmacao((forcar) => api.reprocessar(folha.id, modelo, forcar),
                   modelo ? `Reler como ${modelo}` : "Reler");

  // A61: o mesmo papel escaneado de novo, na MESMA folha — sem criar uma segunda folha
  // que contaria todo mundo em dobro.
  const trocarEscaneamento = (arquivo: File) =>
    comConfirmacao((forcar) => api.substituirEscaneamento(folha.id, arquivo, forcar),
                   "Trocar o escaneamento de");

  async function decidirCopia(acao: "usar" | "nao") {
    const texto =
      acao === "usar"
        ? "Usar ESTE escaneamento no lugar do outro? As decisões tomadas no outro ficam guardadas, mas deixam de contar."
        : "Marcar como papel diferente? As duas folhas passam a contar na frequência.";
    if (!confirm(texto)) return;
    setOcupado(true);
    setErro(null);
    try {
      await (acao === "usar" ? api.usarCopia(folha.id) : api.naoECopia(folha.id));
      aoMudar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  }

  async function apagar() {
    const aviso = revisada
      ? `A folha "${folha.nome_original}" JÁ FOI REVISADA e carrega frequência apurada. Apagar destrói esse registro. Continuar?`
      : `Apagar "${folha.nome_original}"? A imagem e as leituras saem do disco.`;
    if (!confirm(aviso)) return;
    setOcupado(true);
    setErro(null);
    try {
      await api.apagar(folha.id, revisada);
      aoMudar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
      setOcupado(false);
    }
  }

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1 truncate font-medium">
          {folha.nome_original}
          {folha.pagina > 1 && (
            <span className="ml-1 text-slate-500">· página {folha.pagina}</span>
          )}
        </span>
        <Selo situacao={folha.situacao} />
      </div>

      {folha.copia_de != null && (
        <div className="mt-2 rounded bg-violet-50 px-2 py-1.5 text-xs text-violet-900">
          <p>
            Outro escaneamento de{" "}
            <strong>{folha.copia_de_rotulo ?? `folha ${folha.copia_de}`}</strong>
            {folha.semelhanca_copia != null && (
              <> ({Math.round(folha.semelhanca_copia * 100)}% das linhas batem)</>
            )}
            . Esta cópia não conta na frequência nem na quarentena.
          </p>
          <div className="mt-1 flex flex-wrap gap-3">
            <button
              onClick={() => decidirCopia("usar")}
              disabled={ocupado}
              className="underline hover:text-violet-700 disabled:opacity-50"
            >
              usar esta no lugar
            </button>
            <button
              onClick={() => decidirCopia("nao")}
              disabled={ocupado}
              className="underline hover:text-violet-700 disabled:opacity-50"
            >
              não é o mesmo papel
            </button>
          </div>
        </div>
      )}

      {folha.marcador_estimado != null && (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-900">
          Lida com 3 marcadores: o do canto{" "}
          {CANTOS[folha.marcador_estimado] ?? folha.marcador_estimado} não foi lido e a
          posição dele foi estimada pelos outros três. A leitura costuma sair um pouco pior;
          se esta folha tiver muita quarentena, reescaneie-a reta e use “trocar escaneamento”.
        </p>
      )}

      {folha.reescaneada_em && (
        <p className="mt-1 text-xs text-slate-500">
          reescaneada em {new Date(folha.reescaneada_em).toLocaleString("pt-BR")}
          {folha.arquivo_reescaneado && <> · {folha.arquivo_reescaneado}</>}
        </p>
      )}

      {folha.erro && <ErroDaFolha texto={folha.erro} />}
      {erro && (
        <p className="mt-2 rounded bg-rose-50 px-2 py-1 text-xs text-rose-800">
          {erro}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-600">
        {temLinhas && (
          <>
            <span className="tabular-nums">
              <strong className="text-slate-900">{folha.linhas_preenchidas}</strong>{" "}
              linhas
            </span>
            {folha.pendentes > 0 && (
              <span className="tabular-nums text-amber-800">
                {folha.pendentes} em quarentena
              </span>
            )}
            {folha.duplicadas > 0 && (
              <span className="tabular-nums text-rose-700">
                {folha.duplicadas} repetida{folha.duplicadas > 1 ? "s" : ""}
              </span>
            )}
            {folha.linhas_anuladas > 0 && (
              <span className="tabular-nums">
                {folha.linhas_anuladas} anulada
                {folha.linhas_anuladas > 1 ? "s" : ""}
              </span>
            )}
          </>
        )}
        <span className="font-mono text-xs text-slate-400">
          {folha.modelo_folha}
        </span>

        <span className="ml-auto flex flex-wrap items-center gap-2">
          <button
            onClick={() => reavaliar()}
            disabled={ocupado}
            title={`Ler de novo com a mesma geometria (${folha.modelo_folha})`}
            className={
              // erro é do sistema, não do papel: o conserto é reler, não escanear de novo
              folha.estado === "erro"
                ? "rounded bg-marca-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-marca-600 disabled:opacity-50"
                : "rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700 disabled:opacity-50"
            }
          >
            {ocupado ? "…" : "reavaliar"}
          </button>
          <button
            onClick={async () => {
              // Reler com OUTRA geometria é o conserto do lixo silencioso: a folha lida com
              // o modelo errado não dá erro, dá 33 linhas de nada. É exceção, por isso fica
              // fora do clique principal (A63: toda releitura perguntava a geometria).
              if (modelos.length) return setModelos([]);
              try {
                setModelos(await api.modelosFolha());
              } catch (e) {
                setErro(e instanceof ErroApi ? e.message : String(e));
              }
            }}
            disabled={ocupado}
            className="rounded px-1 py-1.5 text-xs text-slate-400 hover:text-marca-700 disabled:opacity-50"
          >
            outra geometria
          </button>
          <input
            ref={seletor}
            type="file"
            accept="image/*,application/pdf"
            className="hidden"
            onChange={(e) => {
              const arquivo = e.target.files?.[0];
              e.target.value = "";
              if (arquivo) trocarEscaneamento(arquivo);
            }}
          />
          <button
            onClick={() => seletor.current?.click()}
            disabled={ocupado}
            title="Enviar outro escaneamento deste mesmo papel (imagem ou PDF de uma página)"
            className={
              folha.estado === "nao_e_folha"
                ? "rounded bg-marca-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-marca-600 disabled:opacity-50"
                : "rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700 disabled:opacity-50"
            }
          >
            trocar escaneamento
          </button>
          <button
            onClick={apagar}
            disabled={ocupado}
            className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50"
          >
            apagar
          </button>
          {temLinhas && (
            <a
              href={`/api/folhas/${folha.id}/imagem`}
              target="_blank"
              rel="noreferrer"
              className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700"
            >
              folha
            </a>
          )}
          {temLinhas && (
            <a
              href={api.urlJson(folha.id)}
              className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700"
            >
              JSON
            </a>
          )}
          {temLinhas && (
            <Link
              to={`/folhas/${folha.id}`}
              className="rounded bg-marca-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-marca-600"
            >
              {revisada ? "ver" : "revisar"}
            </Link>
          )}
        </span>
      </div>

      {folha.estado === "processada" && (
        <Vinculo folha={folha} aoMudar={aoMudar} />
      )}

      {modelos.length > 0 && (
        <div className="mt-3 rounded border border-marca-200 bg-marca-50 p-3">
          <p className="text-sm text-marca-900">
            Reler com qual geometria? A atual é{" "}
            <code className="font-mono text-xs">{folha.modelo_folha}</code>.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {modelos.map((m) => (
              <button
                key={m}
                onClick={() => reavaliar(m)}
                className="rounded border border-marca-300 bg-white px-3 py-1.5 font-mono text-xs hover:border-marca-500 hover:bg-white"
              >
                {m}
              </button>
            ))}
            <button
              onClick={() => setModelos([])}
              className="px-2 py-1.5 text-xs text-slate-500 underline"
            >
              cancelar
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

/** A frase para quem usa, e o detalhe técnico só a pedido (A63: a tela mostrava a exceção
 *  Python crua). O servidor separa os dois com `DETALHE`, em `api/rotas/folhas.py`. */
function ErroDaFolha({ texto }: { texto: string }) {
  const [frase, detalhe] = texto.split("\n\ndetalhe técnico: ");
  return (
    <div className="mt-2 rounded bg-rose-50 px-2 py-1 text-sm text-rose-800">
      {frase}
      {detalhe && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-rose-700">detalhe técnico</summary>
          <p className="mt-1 break-all font-mono text-xs">{detalhe}</p>
        </details>
      )}
    </div>
  );
}

/** "cerca de 7 min", para a estimativa da fila. */
function duracao(segundos: number): string {
  if (segundos < 60) return "menos de 1 min";
  if (segundos < 3600) return `cerca de ${Math.round(segundos / 60)} min`;
  const h = Math.floor(segundos / 3600);
  return `cerca de ${h} h ${Math.round((segundos % 3600) / 60)} min`;
}

function Barra({ fracao }: { fracao: number }) {
  return (
    <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
      <div
        className="h-full rounded-full bg-marca-500 transition-all"
        style={{ width: `${Math.round(Math.min(1, Math.max(0, fracao)) * 100)}%` }}
      />
    </div>
  );
}

/** A leitura em andamento: quantas faltam, qual está sendo lida e quanto tempo falta. Aparece
 *  também para leituras que esta tela não começou (a fila retomada ao subir, o envio de outra
 *  pessoa). */
function ProgressoFila({ fila }: { fila: Fila }) {
  const faltam = fila.na_fila + fila.lendo;
  if (faltam === 0) return null;
  return (
    <div className="mt-4 rounded-lg border border-marca-200 bg-marca-50 px-4 py-3 text-sm text-marca-900">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span>
          <strong>Lendo as folhas:</strong>{" "}
          <span className="tabular-nums">
            {fila.lote_prontas} de {fila.lote_total}
          </span>{" "}
          prontas
        </span>
        <span className="text-xs tabular-nums text-marca-700">
          faltam {duracao(fila.estimativa_s)} · {fila.segundos_por_folha.toFixed(0)} s por folha
        </span>
      </div>
      <Barra fracao={fila.lote_total ? fila.lote_prontas / fila.lote_total : 0} />
      {fila.lendo_agora && (
        <p className="mt-1 truncate text-xs text-marca-700">agora: {fila.lendo_agora}</p>
      )}
    </div>
  );
}

export default function Folhas() {
  return <PainelFolhas />;
}

/** D35: o envio, a fila e as folhas — de todos os grupos, ou de um (a aba "Envio"). */
export function PainelFolhas({ grupo }: { grupo?: string }) {
  const [folhas, setFolhas] = useState<Folha[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [arrastando, setArrastando] = useState(false);
  const [saude, setSaude] = useState<Record<string, string> | null>(null);
  const [soBandeja, setSoBandeja] = useState(false);
  const [avisoEnvio, setAvisoEnvio] = useState<string | null>(null);
  const controle = useRef<AbortController | null>(null);
  // o progresso do envio: qual arquivo, que fração já subiu, e desde quando o servidor converte
  const [envio, setEnvio] = useState<{
    nome: string;
    indice: number;
    total: number;
    fracao: number;
    convertendoDesde: number | null;
  } | null>(null);
  const [agora, setAgora] = useState(Date.now());
  const [fila, setFila] = useState<Fila | null>(null);
  const [gatilhoFila, setGatilhoFila] = useState(0);
  const ultimoEstadoFila = useRef("");

  const recarregar = useCallback(async () => {
    try {
      setFolhas(await api.listarFolhas(grupo));
      setErro(null);
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    }
  }, [grupo]);

  useEffect(() => {
    api.saude().then(setSaude).catch(() => setSaude(null));
    recarregar();
    const c = new AbortController();
    controle.current = c;
    return () => c.abort();
  }, [recarregar]);

  // A fila: uma pergunta a cada 2 s enquanto há leitura, a cada 15 s quando não há (para ver
  // leituras que esta tela não começou). A lista só é recarregada quando a fila muda.
  useEffect(() => {
    let vivo = true;
    let espera: number | undefined;
    async function passo() {
      try {
        const f = await api.fila();
        if (!vivo) return;
        setFila(f);
        const chave = `${f.na_fila}/${f.lendo}/${f.lote_prontas}`;
        if (chave !== ultimoEstadoFila.current) {
          if (ultimoEstadoFila.current) recarregar();
          ultimoEstadoFila.current = chave;
        }
        espera = window.setTimeout(passo, f.na_fila + f.lendo > 0 ? 2000 : 15000);
      } catch {
        if (vivo) espera = window.setTimeout(passo, 5000);
      }
    }
    passo();
    return () => {
      vivo = false;
      window.clearTimeout(espera);
    };
  }, [recarregar, gatilhoFila]);

  // o relógio da conversão do PDF, que acontece no servidor sem dar notícia
  useEffect(() => {
    if (!envio?.convertendoDesde) return;
    const t = window.setInterval(() => setAgora(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [envio?.convertendoDesde]);

  const enviar = useCallback(
    async (arquivos: File[]) => {
      if (!arquivos.length) return;
      setEnviando(true);
      setErro(null);
      setAvisoEnvio(null);
      try {
        for (const [i, arquivo] of arquivos.entries()) {
          // Um envio pode virar VÁRIAS folhas: PDF de escâner traz uma folha
          // por página, que é o formato real das 500.
          setEnvio({ nome: arquivo.name, indice: i + 1, total: arquivos.length, fracao: 0,
                     convertendoDesde: null });
          const { folhas: devolvidas, jaEnviado } = await api.enviarFolhaComProgresso(arquivo, (fracao) =>
            setEnvio((e) =>
              e && {
                ...e,
                fracao,
                convertendoDesde: fracao >= 1 ? (e.convertendoDesde ?? Date.now()) : null,
              },
            ),
            grupo,
          );
          if (jaEnviado) {
            // nada foi duplicado: dizer isso, em vez de o envio parecer não ter acontecido
            const n = devolvidas.length;
            setAvisoEnvio(
              `“${arquivo.name}” já tinha sido enviado (${n} folha${n === 1 ? "" : "s"}); nada foi duplicado.`,
            );
          }
          await recarregar();
          setGatilhoFila((g) => g + 1);     // a fila começa a andar: pergunta já
        }
      } catch (e) {
        setErro(e instanceof ErroApi ? e.message : String(e));
      } finally {
        setEnviando(false);
        setEnvio(null);
      }
    },
    [recarregar, grupo],
  );

  const naFila = folhas.filter(
    (f) => f.estado === "recebida" || f.estado === "processando",
  ).length;
  const bancoFora = saude?.banco && saude.banco !== "ok";
  const emErro = folhas.filter((f) => f.estado === "erro").length;
  const bandeja = folhas.filter(
    (f) => f.estado === "processada" && f.evento_id === null,
  );
  const visiveis = soBandeja ? bandeja : folhas;

  return (
    <>
      {bancoFora && (
        <div className="mb-5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <strong>Banco indisponível</strong> ({saude?.banco}). Ver{" "}
          <code className="font-mono">api/README.md</code>.
        </div>
      )}

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setArrastando(true);
        }}
        onDragLeave={() => setArrastando(false)}
        onDrop={(e) => {
          e.preventDefault();
          setArrastando(false);
          enviar(Array.from(e.dataTransfer.files));
        }}
        className={`block cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition ${
          arrastando
            ? "border-marca-500 bg-marca-50"
            : "border-slate-300 bg-white hover:border-marca-500"
        }`}
      >
        <input
          type="file"
          // PDF primeiro: é o que o escâner entrega, e é o caminho principal.
          accept="application/pdf,image/*"
          multiple
          className="hidden"
          disabled={enviando}
          onChange={(e) => enviar(Array.from(e.target.files ?? []))}
        />
        <div className="text-base font-medium text-marca-700">
          {!enviando
            ? "Enviar folhas digitalizadas"
            : !envio
              ? "enviando…"
              : envio.convertendoDesde
                ? `convertendo o PDF em páginas… ${Math.max(0, Math.round((agora - envio.convertendoDesde) / 1000))} s`
                : `enviando ${envio.nome}${envio.total > 1 ? ` (${envio.indice} de ${envio.total})` : ""} — ${Math.round(envio.fracao * 100)}%`}
        </div>
        {envio && !envio.convertendoDesde && (
          <div className="mx-auto max-w-sm">
            <Barra fracao={envio.fracao} />
          </div>
        )}
        {envio?.convertendoDesde && (
          <p className="mx-auto mt-1 max-w-sm text-xs text-slate-500">
            O arquivo chegou. O servidor está separando as páginas (um PDF de 60 páginas leva
            um ou dois minutos); depois a leitura aparece abaixo, folha por folha.
          </p>
        )}
        <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500">
          PDF do escâner (uma folha por página) ou imagens. Toque para escolher,
          ou arraste os arquivos aqui.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          Cada folha leva cerca de 9 segundos, e são lidas uma por vez.
        </p>
      </label>

      {fila && <ProgressoFila fila={fila} />}

      {emErro > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <span>
            {emErro} folha{emErro === 1 ? "" : "s"} com erro de leitura.
          </span>
          <button
            onClick={async () => {
              setErro(null);
              try {
                await api.relerErros(grupo);
                await recarregar();
                setGatilhoFila((g) => g + 1);
              } catch (e) {
                setErro(e instanceof ErroApi ? e.message : String(e));
              }
            }}
            className="rounded bg-rose-600 px-3 py-1.5 font-medium text-white hover:bg-rose-700"
          >
            reler {emErro === 1 ? "a folha" : `as ${emErro}`}
          </button>
        </div>
      )}

      {avisoEnvio && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {avisoEnvio}
        </div>
      )}

      {erro && (
        <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {erro}
        </div>
      )}

      <section className="mt-8">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Folhas
            {naFila > 0 && (
              <span className="ml-2 font-normal normal-case text-amber-700">
                {naFila} na fila
              </span>
            )}
          </h2>
          <span className="flex items-baseline gap-3">
            {(bandeja.length > 0 || soBandeja) && (
              <button
                onClick={() => setSoBandeja((s) => !s)}
                className="text-xs text-amber-800 underline underline-offset-2"
              >
                {soBandeja ? "ver todas" : `só sem evento (${bandeja.length})`}
              </button>
            )}
            <button
              onClick={recarregar}
              className="text-xs text-slate-500 underline underline-offset-2 hover:text-marca-700"
            >
              atualizar
            </button>
          </span>
        </div>

        {folhas.length > 0 && (
          <div className="-mt-1 mb-3 flex justify-end">
            <ApagarEmMassa
              rotulo="apagar todas as folhas"
              previa={api.previaApagarTodas}
              apagar={api.apagarTodas}
              aoApagar={() => void recarregar()}
            />
          </div>
        )}

        {visiveis.length === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
            {soBandeja
              ? "Nenhuma folha sem evento."
              : "Nenhuma folha enviada ainda."}
          </p>
        ) : (
          <ul className="space-y-3">
            {visiveis.map((f) => (
              <Cartao key={f.id} folha={f} aoMudar={recarregar} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
