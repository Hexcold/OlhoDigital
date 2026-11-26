import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ErroApi } from "../api";
import BasePessoas from "../componentes/BasePessoas";
import type { EtapaGrupo, Grupo } from "../tipos";

/** D35: o início do sistema é a lista de grupos — cada grupo é uma coleta, do preparo ao
 *  fechamento. */

export const ETAPAS: Record<EtapaGrupo, { texto: string; classe: string; dica: string }> = {
  preparando: {
    texto: "preparando",
    classe: "bg-slate-100 text-slate-700",
    dica: "cadastre os eventos e as chaves, e imprima as folhas",
  },
  recebendo: {
    texto: "lendo folhas",
    classe: "bg-amber-100 text-amber-800",
    dica: "há folhas na fila de leitura",
  },
  revisando: {
    texto: "revisando",
    classe: "bg-amber-100 text-amber-900",
    dica: "há linhas em quarentena esperando decisão",
  },
  pronto: {
    texto: "pronto",
    classe: "bg-emerald-100 text-emerald-800",
    dica: "sem pendências: a frequência pode ser baixada",
  },
  encerrado: {
    texto: "encerrado",
    classe: "bg-slate-200 text-slate-600",
    dica: "as chaves foram liberadas",
  },
};

/** O caminho de uma coleta, na ordem das abas da página do grupo. */
export const PASSOS = [
  { aba: "eventos", texto: "Eventos e chaves" },
  { aba: "folha", texto: "Folha" },
  { aba: "envio", texto: "Envio" },
  { aba: "revisao", texto: "Revisão" },
  { aba: "frequencia", texto: "Frequência" },
] as const;

export function SeloEtapa({ etapa }: { etapa?: EtapaGrupo }) {
  const e = ETAPAS[etapa ?? "preparando"];
  return (
    <span title={e.dica} className={`rounded-full px-2 py-0.5 text-xs font-medium ${e.classe}`}>
      {e.texto}
    </span>
  );
}

function NovoGrupo({ aoCriar }: { aoCriar: (nome: string) => void }) {
  const [nome, setNome] = useState("");
  const [descricao, setDescricao] = useState("");
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setOcupado(true);
        setErro("");
        try {
          const g = await api.criarGrupo(nome, descricao);
          aoCriar(g.nome);
        } catch (err) {
          setErro(err instanceof ErroApi ? err.message : String(err));
        } finally {
          setOcupado(false);
        }
      }}
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Novo grupo</h2>
      <p className="mt-1 text-sm text-slate-600">
        Um grupo é uma coleta: os Encontros Universitários de 2027, as aulas de uma turma num
        semestre. Os eventos, as chaves e as folhas vivem dentro dele.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_2fr_auto]">
        <input
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          placeholder="nome, ex.: EU 2027"
          aria-label="nome do grupo"
          className="rounded border border-slate-300 px-3 py-2 text-base outline-none focus:border-marca-500"
        />
        <input
          value={descricao}
          onChange={(e) => setDescricao(e.target.value)}
          placeholder="descrição (opcional)"
          aria-label="descrição do grupo"
          className="rounded border border-slate-300 px-3 py-2 text-base outline-none focus:border-marca-500"
        />
        <button
          type="submit"
          disabled={ocupado || !nome.trim()}
          className="rounded bg-marca-600 px-4 py-2 text-sm font-medium text-white hover:bg-marca-700 disabled:opacity-50"
        >
          {ocupado ? "criando…" : "criar grupo"}
        </button>
      </div>
      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
    </form>
  );
}

function CartaoGrupo({ g }: { g: Grupo }) {
  const contam = (g.leitura_unica ?? 0) + (g.com_copia ?? 0);
  return (
    <Link
      to={`/grupos/${encodeURIComponent(g.nome)}`}
      className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-marca-500 hover:shadow"
    >
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold text-slate-900">{g.nome}</span>
          {g.descricao && (
            <span className="block truncate text-sm text-slate-500">{g.descricao}</span>
          )}
        </span>
        <SeloEtapa etapa={g.etapa} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div>
          <dt className="text-xs text-slate-500">eventos</dt>
          <dd className="text-lg font-semibold tabular-nums">{g.eventos}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">folhas</dt>
          <dd className="text-lg font-semibold tabular-nums">{contam || g.folhas}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">pendentes</dt>
          <dd
            className={`text-lg font-semibold tabular-nums ${g.pendentes ? "text-amber-700" : ""}`}
          >
            {g.pendentes ?? 0}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-xs text-slate-500">{ETAPAS[g.etapa ?? "preparando"].dica}</p>
    </Link>
  );
}

export default function Grupos() {
  const [grupos, setGrupos] = useState<Grupo[] | null>(null);
  const [erro, setErro] = useState("");
  const navegar = useNavigate();

  const recarregar = useCallback(() => {
    api
      .listarGrupos()
      .then(setGrupos)
      .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)));
  }, []);
  useEffect(recarregar, [recarregar]);

  const abertos = (grupos ?? []).filter((g) => g.etapa !== "encerrado");
  const encerrados = (grupos ?? []).filter((g) => g.etapa === "encerrado");

  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-xl font-semibold tracking-tight">Grupos</h1>
        <ol className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-500">
          {PASSOS.map((p, i) => (
            <li key={p.aba} className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-marca-100 text-xs font-semibold text-marca-700">
                {i + 1}
              </span>
              {p.texto}
              {i < PASSOS.length - 1 && <span className="text-slate-300">→</span>}
            </li>
          ))}
        </ol>
      </section>

      <NovoGrupo aoCriar={(nome) => navegar(`/grupos/${encodeURIComponent(nome)}?aba=eventos`)} />

      {erro && (
        <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {erro}
        </div>
      )}
      {!grupos && !erro && <p className="text-sm text-slate-500">carregando…</p>}

      {abertos.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {abertos.map((g) => (
            <CartaoGrupo key={g.nome} g={g} />
          ))}
        </div>
      )}
      {grupos && abertos.length === 0 && (
        <p className="text-sm text-slate-500">Nenhum grupo em andamento. Crie o primeiro acima.</p>
      )}

      <BasePessoas />

      {encerrados.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Encerrados
          </h2>
          <div className="grid gap-3 opacity-80 sm:grid-cols-2">
            {encerrados.map((g) => (
              <CartaoGrupo key={g.nome} g={g} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
