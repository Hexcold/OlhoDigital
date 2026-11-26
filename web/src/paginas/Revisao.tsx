import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ErroApi } from "../api";
import { Bloco, decisoesGravadas, useRelogio, type Decidida, type Fora } from "../componentes/BlocoLinha";
import type { Folha, Linha, ResumoRevisao } from "../tipos";


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
  errosLinha: Record<number, string>;
  aoFocar: (id: number) => void;
  aoDecidir: (linha: Linha, matricula: string | null, rotulo: string, fora?: Fora) => void;
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
            erro={resto.errosLinha[l.id]}
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
  const [errosLinha, setErrosLinha] = useState<Record<number, string>>({});
  const { marcarFoco, parar } = useRelogio();

  useEffect(() => {
    Promise.all([api.obterFolha(folhaId), api.linhasDaFolha(folhaId)])
      .then(([f, l]) => {
        setFolha(f);
        setLinhas(l);
        // As decisões vêm do SERVIDOR, não de estado local: é a diferença de
        // sair do localStorage.
        setDecididas(decisoesGravadas(l));
      })
      .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)));
  }, [folhaId]);

  const decidir = useCallback(
    async (linha: Linha, matricula: string | null, rotulo: string, fora?: Fora) => {
      const ms = parar(linha.id);
      // Otimista: a decisão aparece na hora e é desfeita se o servidor recusar.
      // Numa tela em que a mediana é de segundos, esperar a ida e volta a cada
      // clique somaria mais tempo que a decisão em si.
      setDecididas((d) => ({
        ...d,
        [linha.id]: { matricula: fora?.matricula ?? matricula, rotulo, ms, foraDaBase: !!fora },
      }));
      try {
        await api.revisar(linha.id, matricula, ms, fora);
        setErrosLinha(({ [linha.id]: _, ...outros }) => outros);
      } catch (e) {
        setDecididas((d) => {
          const copia = { ...d };
          delete copia[linha.id];
          return copia;
        });
        // no cartão da linha, e não no topo: ver `erro` em `Bloco`
        const texto = e instanceof ErroApi ? e.message : String(e);
        setErrosLinha((x) => ({ ...x, [linha.id]: texto }));
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
        errosLinha={errosLinha}
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
        errosLinha={errosLinha}
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
        errosLinha={errosLinha}
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
        errosLinha={errosLinha}
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
