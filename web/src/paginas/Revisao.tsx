import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ErroApi } from "../api";
import { Leitura } from "../componentes/Leitura";
import type { Folha, Linha, Participante, ResumoRevisao } from "../tipos";

interface Decidida {
  matricula: string | null;
  rotulo: string;
  ms: number;
}

/** Relógio por linha.
 *
 *  O `quarentena.py` media a partir do `mouseenter` no bloco, e foi assim que
 *  saíram os 3,7 s por auditoria e 15,8 s por triagem de A39. No celular não
 *  existe hover, então marcamos o foco também por toque; e se uma linha for
 *  decidida sem nunca ter recebido foco, o relógio conta desde a decisão
 *  anterior. **Os dois relógios não medem a mesma coisa** — o primeiro é
 *  "tempo olhando esta linha", o segundo é "tempo desde a linha anterior" —,
 *  e comparar com A39 só vale para o primeiro. */
function useRelogio() {
  const foco = useRef<{ id: number; em: number } | null>(null);
  const ultimaDecisao = useRef<number>(performance.now());

  const marcarFoco = useCallback((id: number) => {
    if (foco.current?.id !== id) foco.current = { id, em: performance.now() };
  }, []);

  const parar = useCallback((id: number) => {
    const agora = performance.now();
    const inicio =
      foco.current?.id === id ? foco.current.em : ultimaDecisao.current;
    ultimaDecisao.current = agora;
    foco.current = null;
    return Math.round(agora - inicio);
  }, []);

  return { marcarFoco, parar };
}

function Selo({ decisao }: { decisao: Linha["decisao"] }) {
  const estilo =
    decisao === "aceito"
      ? "bg-emerald-100 text-emerald-800"
      : decisao === "anulada"
        ? "bg-slate-200 text-slate-700"
        : "bg-amber-100 text-amber-900";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${estilo}`}>
      {decisao}
    </span>
  );
}

function Busca({
  aoEscolher,
}: {
  aoEscolher: (p: Participante) => void;
}) {
  const [termo, setTermo] = useState("");
  const [achados, setAchados] = useState<Participante[]>([]);

  useEffect(() => {
    if (termo.trim().length < 2) {
      setAchados([]);
      return;
    }
    // Espera curta antes de consultar: digitar "silva" dispararia cinco
    // consultas contra as 1.334 pessoas sem isso.
    const t = setTimeout(() => {
      api.buscarParticipantes(termo).then(setAchados).catch(() => setAchados([]));
    }, 200);
    return () => clearTimeout(t);
  }, [termo]);

  return (
    <div className="mt-3">
      <input
        value={termo}
        onChange={(e) => setTermo(e.target.value)}
        placeholder="buscar na base por nome ou matrícula…"
        // 16px evita o zoom automático do Safari no iPhone ao focar o campo
        className="w-full rounded border border-slate-300 px-3 py-2 text-base outline-none focus:border-marca-500 focus:ring-1 focus:ring-marca-500"
      />
      {achados.length > 0 && (
        <ul className="mt-2 max-h-56 divide-y divide-slate-100 overflow-y-auto rounded border border-slate-200">
          {achados.map((p) => (
            <li key={p.matricula}>
              <button
                onClick={() => {
                  aoEscolher(p);
                  setTermo("");
                  setAchados([]);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-marca-50"
              >
                <span className="font-mono text-xs text-slate-500">
                  {p.matricula}
                </span>
                <span className="min-w-0 flex-1 truncate">{p.nome}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Bloco({
  linha,
  decidida,
  travada,
  aoFocar,
  aoDecidir,
  aoDesfazer,
}: {
  linha: Linha;
  decidida?: Decidida;
  /** Folha concluída: a decisão vira leitura. Mudar presença depois do
   *  carimbo faria o carimbo não significar nada. */
  travada: boolean;
  aoFocar: (id: number) => void;
  aoDecidir: (linha: Linha, matricula: string | null, rotulo: string) => void;
  aoDesfazer: (linha: Linha) => void;
}) {
  const perigosa = linha.vizinhos.length > 0;

  return (
    <li
      onMouseEnter={() => aoFocar(linha.id)}
      onTouchStart={() => aoFocar(linha.id)}
      className={`rounded-lg border bg-white p-4 shadow-sm ${
        decidida
          ? "border-emerald-300"
          : perigosa
            ? "border-amber-300"
            : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium">linha {String(linha.numero).padStart(2, "0")}</span>
        <Selo decisao={linha.decisao} />
        {linha.duplicada_na_folha && (
          <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800">
            repetida nesta folha
          </span>
        )}
        {linha.motivo_rejeicao && (
          <span className="text-xs text-amber-800">{linha.motivo_rejeicao}</span>
        )}
        <span className="ml-auto font-mono text-xs tabular-nums text-slate-400">
          score {linha.score.toFixed(3)} · margem {linha.margem.toFixed(3)}
        </span>
      </div>

      {linha.duplicada_na_folha && (
        <p className="mt-2 rounded border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
          Esta matrícula aparece em <strong>outra linha desta mesma folha</strong>.
          Costuma ser linha começada e refeita abaixo. Se as duas valerem, a
          pessoa conta presença duas vezes — e nenhuma métrica do sistema
          percebe.
        </p>
      )}

      {perigosa && (
        <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          ⚠ O campeão tem {linha.vizinhos.length} matrícula(s) real(is) a{" "}
          <strong>um dígito</strong>:{" "}
          <code className="font-mono text-xs">
            {linha.vizinhos.slice(0, 5).join(", ")}
          </code>{" "}
          — um erro de leitura cai em pessoa existente sem o gate perceber.{" "}
          <strong>Confira o nome.</strong>
        </p>
      )}

      <div className="tira-linha mt-3 rounded border border-slate-200 bg-slate-50">
        <img
          src={`/api/linhas/${linha.id}/recorte`}
          alt={`recorte da linha ${linha.numero}`}
          loading="lazy"
        />
      </div>

      <dl className="mt-3 space-y-1">
        <div className="flex gap-2">
          <dt className="w-24 shrink-0 text-xs uppercase tracking-wide text-slate-500">
            matrícula
          </dt>
          <dd>
            <Leitura
              texto={linha.leitura_matricula}
              confiancas={linha.confiancas_matricula}
              motivos={linha.motivos_matricula}
            />
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-24 shrink-0 text-xs uppercase tracking-wide text-slate-500">
            nome
          </dt>
          <dd>
            <Leitura
              texto={linha.leitura_nome}
              confiancas={linha.confiancas_nome}
              motivos={linha.motivos_nome}
            />
          </dd>
        </div>
      </dl>

      {decidida || travada ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          <span>
            {decidida ? (
              <>
                ✓ {decidida.matricula ?? "não identificado"}
                {decidida.rotulo && ` · ${decidida.rotulo}`}
              </>
            ) : (
              // travada e sem decisão humana: vale o que o sistema aceitou
              <>✓ {linha.matricula_sugerida ?? "sem candidato"} (pelo sistema)</>
            )}
          </span>
          {decidida && decidida.ms > 0 && (
            <span className="ml-auto text-xs tabular-nums text-emerald-700">
              {(decidida.ms / 1000).toFixed(1)} s
            </span>
          )}
          {decidida && !travada && (
            // Mudar de ideia é normal, e o revisor de A21 mudou em 1 de 41
            // linhas. Escolher de novo grava uma revisão NOVA por cima: a
            // tabela é append-only, então o histórico guarda as duas.
            <button
              onClick={() => aoDesfazer(linha)}
              className="rounded px-2 py-0.5 text-xs text-emerald-800 underline underline-offset-2 hover:text-emerald-950"
            >
              mudar
            </button>
          )}
        </div>
      ) : (
        <>
          <ul className="mt-3 space-y-1">
            {linha.candidatos.map((c) => (
              <li key={c.matricula}>
                <button
                  onClick={() => aoDecidir(linha, c.matricula, c.nome)}
                  className="flex w-full items-center gap-2 rounded border border-slate-200 px-3 py-2 text-left text-sm hover:border-marca-500 hover:bg-marca-50"
                >
                  <span className="font-mono text-xs tabular-nums text-slate-500">
                    {c.score.toFixed(3)}
                  </span>
                  <span className="font-mono text-xs">{c.matricula}</span>
                  <span className="min-w-0 flex-1 truncate">{c.nome}</span>
                </button>
              </li>
            ))}
          </ul>

          <Busca aoEscolher={(p) => aoDecidir(linha, p.matricula, p.nome)} />

          <button
            onClick={() => aoDecidir(linha, null, "")}
            className="mt-2 text-sm text-slate-500 underline underline-offset-2 hover:text-rose-700"
          >
            não identificado
          </button>
        </>
      )}
    </li>
  );
}

function Secao({
  titulo,
  aviso,
  linhas,
  ...resto
}: {
  titulo: string;
  aviso?: string;
  linhas: Linha[];
  travada: boolean;
  decididas: Record<number, Decidida>;
  aoFocar: (id: number) => void;
  aoDecidir: (linha: Linha, matricula: string | null, rotulo: string) => void;
  aoDesfazer: (linha: Linha) => void;
}) {
  if (linhas.length === 0) return null;
  return (
    <section className="mt-8">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        {titulo} — {linhas.length}
      </h2>
      {aviso && <p className="mt-1 text-sm text-slate-600">{aviso}</p>}
      <ul className="mt-3 space-y-3">
        {linhas.map((l) => (
          <Bloco
            key={l.id}
            linha={l}
            decidida={resto.decididas[l.id]}
            travada={resto.travada}
            aoFocar={resto.aoFocar}
            aoDesfazer={resto.aoDesfazer}
            aoDecidir={resto.aoDecidir}
          />
        ))}
      </ul>
    </section>
  );
}

export default function Revisao() {
  const { id } = useParams();
  const folhaId = Number(id);
  const [folha, setFolha] = useState<Folha | null>(null);
  const [linhas, setLinhas] = useState<Linha[]>([]);
  const [decididas, setDecididas] = useState<Record<number, Decidida>>({});
  const [resumo, setResumo] = useState<ResumoRevisao | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const { marcarFoco, parar } = useRelogio();

  useEffect(() => {
    Promise.all([api.obterFolha(folhaId), api.linhasDaFolha(folhaId)])
      .then(([f, l]) => {
        setFolha(f);
        setLinhas(l);
        // As decisões vêm do SERVIDOR, não de estado local: recarregar a
        // página, trocar de aparelho ou passar a folha para outra pessoa não
        // pode fazer a revisão parecer não feita. É a diferença de sair do
        // localStorage.
        const jaFeitas: Record<number, Decidida> = {};
        for (const linha of l) {
          if (!linha.revisao) continue;
          const escolhida = linha.revisao.matricula_escolhida;
          jaFeitas[linha.id] = {
            matricula: escolhida,
            rotulo:
              linha.candidatos.find((c) => c.matricula === escolhida)?.nome ?? "",
            ms: linha.revisao.ms ?? 0,
          };
        }
        setDecididas(jaFeitas);
      })
      .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)));
  }, [folhaId]);

  const decidir = useCallback(
    async (linha: Linha, matricula: string | null, rotulo: string) => {
      const ms = parar(linha.id);
      // Otimista: a decisão aparece na hora e é desfeita se o servidor recusar.
      // Numa tela em que a mediana é de segundos, esperar a ida e volta a cada
      // clique somaria mais tempo que a decisão em si.
      setDecididas((d) => ({ ...d, [linha.id]: { matricula, rotulo, ms } }));
      try {
        await api.revisar(linha.id, matricula, ms);
        setErro(null);
      } catch (e) {
        setDecididas((d) => {
          const copia = { ...d };
          delete copia[linha.id];
          return copia;
        });
        setErro(e instanceof ErroApi ? e.message : String(e));
      }
    },
    [parar],
  );

  // Só tira a linha do estado "decidida" na TELA. A revisão anterior continua
  // gravada — a tabela é append-only —, e escolher de novo empilha por cima.
  // Nada é apagado, e é isso que mantém o histórico auditável.
  const desfazer = useCallback((linha: Linha) => {
    setDecididas((d) => {
      const copia = { ...d };
      delete copia[linha.id];
      return copia;
    });
  }, []);

  const grupos = useMemo(() => {
    const quarentena = linhas.filter((l) => l.decisao === "quarentena");
    const anuladas = linhas.filter((l) => l.decisao === "anulada");
    const aceitos = linhas.filter((l) => l.decisao === "aceito");
    return {
      quarentena,
      anuladas,
      // Aceites com sósia sobem: são o único modo de falha que nem θ nem δ
      // alcançam, e na primeira revisão com humano eles passaram batidos em
      // ~1 s cada. Auditoria dirigida em vez de amostragem cega.
      perigosos: aceitos.filter((l) => l.vizinhos.length > 0),
      tranquilos: aceitos.filter((l) => l.vizinhos.length === 0),
    };
  }, [linhas]);

  // O que impede concluir é quarentena sem decisão. Aceite sem auditar não
  // impede: a auditoria é por amostragem por decisão de método (A21).
  const pendentes = grupos.quarentena.filter((l) => !decididas[l.id]).length;
  // Folha concluída não aceita decisão nova: o carimbo é o que torna a
  // frequência final. Reabrir é ato explícito, e não apaga nada — a tabela de
  // revisão é append-only.
  const travada = folha?.revisada_em != null;
  const feitas = Object.keys(decididas).length;
  const tempos = Object.values(decididas)
    .map((d) => d.ms)
    .sort((a, b) => a - b);
  const mediana = tempos.length
    ? (tempos[Math.floor(tempos.length / 2)] / 1000).toFixed(1)
    : "—";

  if (erro && !folha) {
    return (
      <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
        {erro}
      </div>
    );
  }

  return (
    <>
      <div className="sticky top-[3.25rem] z-10 -mx-4 mb-2 border-b border-slate-200 bg-slate-50/95 px-4 py-2 backdrop-blur">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
          <span className="font-medium">
            {folha?.nome_original}
            {folha && folha.pagina > 1 && ` · página ${folha.pagina}`}
          </span>
          <span className="tabular-nums text-slate-600">
            {feitas}/{linhas.length} revisadas · mediana {mediana} s
          </span>
        </div>
      </div>

      {erro && (
        <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {erro}
        </div>
      )}

      <FolhaInteira folhaId={folhaId} />

      <Secao
        titulo="Quarentena"
        aviso="O gate não teve confiança suficiente. É aqui que a revisão humana recupera identificação."
        linhas={grupos.quarentena}
        decididas={decididas}
        travada={travada}
        aoFocar={marcarFoco}
        aoDecidir={decidir}
        aoDesfazer={desfazer}
      />
      <Secao
        titulo="Aceites com sósia na base"
        aviso="Aceitos com score alto, mas a matrícula escolhida tem vizinho a um dígito que também é pessoa real. O nome é a única evidência que denuncia a troca."
        linhas={grupos.perigosos}
        decididas={decididas}
        travada={travada}
        aoFocar={marcarFoco}
        aoDecidir={decidir}
        aoDesfazer={desfazer}
      />
      <Secao
        titulo="Anuladas pelo protocolo"
        aviso="Uma célula preenchida por inteiro anula a linha — a pessoa declarou que ela não vale."
        linhas={grupos.anuladas}
        decididas={decididas}
        travada={travada}
        aoFocar={marcarFoco}
        aoDecidir={decidir}
        aoDesfazer={desfazer}
      />
      <Secao
        titulo="Demais aceites"
        aviso="Auditoria por amostragem. Não precisam de decisão, mas conferir uma parte é o que pega o aceite errado."
        linhas={grupos.tranquilos}
        decididas={decididas}
        travada={travada}
        aoFocar={marcarFoco}
        aoDecidir={decidir}
        aoDesfazer={desfazer}
      />

      <BarraConclusao
        folhaId={folhaId}
        pendentes={pendentes}
        revisadaEm={folha?.revisada_em ?? null}
        aoReabrir={() => {
          setResumo(null);
          setFolha((f) => (f ? { ...f, revisada_em: null } : f));
        }}
        aoConcluir={(r) => {
          setResumo(r);
          setFolha((f) => (f ? { ...f, revisada_em: new Date().toISOString() } : f));
        }}
      />

      {resumo && (
        <div className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          <strong>Revisão concluída.</strong> {resumo.identificados}{" "}
          identificados · {resumo.nao_identificados} não identificados ·{" "}
          {resumo.anuladas} anuladas
          {resumo.duplicadas > 0 && ` · ${resumo.duplicadas} repetidas na folha`}
          .
        </div>
      )}

      {/* espaço para a barra fixa não cobrir a última linha */}
      <div className="h-24" />
    </>
  );
}

/** A folha digitalizada inteira, sob demanda.
 *
 *  A tira da linha resolve a maioria dos casos, mas nem todos: linha
 *  abandonada e refeita abaixo (A40), risco de anulação que atravessa duas
 *  linhas, ou tinta que invadiu a célula vizinha só fazem sentido vendo a
 *  folha. Fica fechada por padrão porque é uma imagem grande e o fluxo normal
 *  não precisa dela — abrir é decisão de quem está com uma dúvida. */
function FolhaInteira({ folhaId }: { folhaId: number }) {
  const [aberta, setAberta] = useState(false);
  return (
    <section className="mb-2">
      <button
        onClick={() => setAberta((a) => !a)}
        className="inline-flex items-center gap-2 rounded-lg border border-marca-200 bg-white px-3 py-2 text-sm font-medium text-marca-700 shadow-sm hover:border-marca-500 hover:bg-marca-50"
      >
        <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none"
             stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <rect x="2.5" y="1.5" width="11" height="13" rx="1.5" />
          <path d="M5 5h6M5 8h6M5 11h4" />
        </svg>
        {aberta ? "esconder a folha inteira" : "ver a folha inteira"}
      </button>
      {aberta && (
        <div className="tira-linha mt-2 max-h-[70vh] overflow-y-auto rounded border border-slate-200 bg-white">
          <img
            src={`/api/folhas/${folhaId}/imagem`}
            alt="folha digitalizada"
            className="w-full"
            style={{ maxWidth: "100%", height: "auto" }}
          />
        </div>
      )}
    </section>
  );
}

/** A barra que fecha a folha.
 *
 *  Fica fixa no rodapé porque a decisão de concluir é tomada depois de rolar
 *  até o fim, e subir para achar um botão é atrito num fluxo cuja mediana é de
 *  segundos por linha. */
function BarraConclusao({
  folhaId,
  pendentes,
  revisadaEm,
  aoConcluir,
  aoReabrir,
}: {
  folhaId: number;
  pendentes: number;
  revisadaEm: string | null;
  aoConcluir: (r: ResumoRevisao) => void;
  aoReabrir: () => void;
}) {
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  if (revisadaEm) {
    return (
      <div className="fixed inset-x-0 bottom-0 border-t border-emerald-200 bg-emerald-50/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 text-sm text-emerald-900">
          <span>
            ✓ Folha revisada em{" "}
            {new Date(revisadaEm).toLocaleString("pt-BR", {
              dateStyle: "short",
              timeStyle: "short",
            })}
          </span>
          {erro && <span className="text-rose-700">{erro}</span>}
          <a
            href={api.urlJson(folhaId)}
            className="ml-auto rounded px-3 py-2 text-emerald-800 underline underline-offset-2"
          >
            baixar JSON
          </a>
          <button
            disabled={enviando}
            onClick={async () => {
              setEnviando(true);
              setErro(null);
              try {
                await api.reabrir(folhaId);
                aoReabrir();
              } catch (e) {
                setErro(e instanceof ErroApi ? e.message : String(e));
              } finally {
                setEnviando(false);
              }
            }}
            className="rounded-lg border border-emerald-300 bg-white px-4 py-2 font-medium text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
          >
            {enviando ? "reabrindo…" : "reabrir revisão"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-x-0 bottom-0 border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
        <span className="text-sm text-slate-600">
          {pendentes > 0 ? (
            <>
              <strong className="text-amber-700">{pendentes}</strong> linha
              {pendentes > 1 ? "s" : ""} de quarentena sem decisão
            </>
          ) : (
            "Quarentena resolvida."
          )}
        </span>
        {erro && <span className="text-sm text-rose-700">{erro}</span>}
        <button
          disabled={pendentes > 0 || enviando}
          onClick={async () => {
            setEnviando(true);
            setErro(null);
            try {
              aoConcluir(await api.concluir(folhaId));
            } catch (e) {
              setErro(e instanceof ErroApi ? e.message : String(e));
            } finally {
              setEnviando(false);
            }
          }}
          className="ml-auto rounded-lg bg-marca-500 px-4 py-2 text-sm font-medium text-white hover:bg-marca-600 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {enviando ? "concluindo…" : "concluir revisão"}
        </button>
      </div>
    </div>
  );
}
