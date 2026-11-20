import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ErroApi, type EventoEntrada } from "../api";
import { ApagarEmMassa } from "../componentes/ApagarEmMassa";
import { EncerrarGrupo } from "../componentes/EncerrarGrupo";
import {
  CATEGORIAS,
  type Categoria,
  type Evento,
  type Grupo,
  type TipoEvento,
} from "../tipos";

/** Cadastro manual de ocorrências (D15).
 *
 *  A folha traz só a CHAVE. Se o evento não estiver cadastrado antes, a chave
 *  lida não tem contra o que casar — e deixá-la criar grupo sozinha é o erro
 *  que D14 registra como o pior desta parte: as pessoas certas identificadas
 *  na pasta do evento errado, invisível para precisão e para cobertura.
 *
 *  Cadastro em LOTE (a programação dos Encontros, as N ocorrências de uma
 *  turma por recorrência) é outra tela e ainda não existe. */

const VAZIO: EventoEntrada = { chave: "", tipo: "evento", nome: "" };

function Campo({
  rotulo,
  dica,
  children,
}: {
  rotulo: string;
  dica?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {rotulo}
      </span>
      {children}
      {dica && <span className="mt-1 block text-xs text-slate-500">{dica}</span>}
    </label>
  );
}

const ENTRADA =
  "mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm " +
  "focus:border-marca-500 focus:outline-none focus:ring-1 focus:ring-marca-500";

/** Os nomes de grupo já em uso, para o campo sugerir em vez de a pessoa digitar
 *  "EU" num evento e "EUs" no outro e criar dois grupos sem querer. */
function ListaDeGrupos({ id, grupos }: { id: string; grupos: Grupo[] }) {
  return (
    <datalist id={id}>
      {grupos.map((g) => (
        <option key={g.nome} value={g.nome} />
      ))}
    </datalist>
  );
}

export function Formulario({
  aoCriar,
  grupos,
  grupoFixo,
  chavePreenchida,
}: {
  aoCriar: () => void;
  grupos: Grupo[];
  /** D35: dentro da página do grupo, o evento nasce nele e o campo some */
  grupoFixo?: string;
  /** D35, fase 4: uma chave sugerida, escolhida fora do formulário */
  chavePreenchida?: string;
}) {
  const [dados, setDados] = useState<EventoEntrada>({ ...VAZIO, grupo: grupoFixo ?? VAZIO.grupo });
  useEffect(() => {
    if (chavePreenchida) setDados((d) => ({ ...d, chave: chavePreenchida }));
  }, [chavePreenchida]);
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);

  const mudar = (campo: keyof EventoEntrada, valor: unknown) =>
    setDados((d) => ({ ...d, [campo]: valor === "" ? null : valor }));

  // Trocar de tipo LIMPA os campos do outro: o servidor recusa 'aula' com
  // categoria, e deixar o valor escondido no estado faria o formulário ser
  // rejeitado por um campo que ninguém está mais vendo.
  const trocarTipo = (tipo: TipoEvento) =>
    setDados((d) => ({
      chave: d.chave,
      nome: d.nome,
      responsavel: d.responsavel,
      local: d.local,
      inicio: d.inicio,
      fim: d.fim,
      grupo: d.grupo,
      tipo,
    }));

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setErro("");
    setOcupado(true);
    try {
      await api.criarEvento({ ...dados, chave: dados.chave.toUpperCase() });
      setDados({ ...VAZIO, grupo: grupoFixo ?? VAZIO.grupo });
      aoCriar();
    } catch (erro) {
      setErro(
        erro instanceof ErroApi ? erro.message : "não deu para criar o evento",
      );
    } finally {
      setOcupado(false);
    }
  }

  const aula = dados.tipo === "aula";

  return (
    <form
      onSubmit={enviar}
      className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Novo evento
      </h2>

      <div className="mt-3 flex gap-2">
        {(["evento", "aula"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => trocarTipo(t)}
            className={`rounded px-3 py-1.5 text-sm font-medium ${
              dados.tipo === t
                ? "bg-marca-500 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {t === "aula" ? "aula" : "evento"}
          </button>
        ))}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Campo
          rotulo="chave"
          dica="A–Z apenas, até 16 letras. É o que vai escrito na folha."
        >
          <input
            required
            value={dados.chave}
            onChange={(e) => mudar("chave", e.target.value.toUpperCase())}
            className={`${ENTRADA} font-mono uppercase`}
            placeholder="BANANA"
          />
        </Campo>

        <Campo rotulo="nome">
          <input
            required
            value={dados.nome}
            onChange={(e) => mudar("nome", e.target.value)}
            className={ENTRADA}
            placeholder={aula ? "Cálculo I — T01" : "Apresentação de trabalhos"}
          />
        </Campo>

        <Campo rotulo="responsável">
          <input
            value={dados.responsavel ?? ""}
            onChange={(e) => mudar("responsavel", e.target.value)}
            className={ENTRADA}
          />
        </Campo>

        <Campo rotulo="local" dica="A unidade é a sala numa janela de tempo.">
          <input
            value={dados.local ?? ""}
            onChange={(e) => mudar("local", e.target.value)}
            className={ENTRADA}
            placeholder="Bloco 2, sala 2"
          />
        </Campo>

        {!grupoFixo && (
          <Campo
            rotulo="grupo"
            dica="Obrigatório: todo evento pertence a um grupo (D35). Um nome novo cria o grupo."
          >
            <input
              value={dados.grupo ?? ""}
              onChange={(e) => mudar("grupo", e.target.value)}
              list="grupos-do-formulario"
              className={ENTRADA}
              placeholder="EU"
            />
            <ListaDeGrupos id="grupos-do-formulario" grupos={grupos} />
          </Campo>
        )}

        <Campo rotulo="início">
          <input
            type="datetime-local"
            value={dados.inicio ?? ""}
            onChange={(e) => mudar("inicio", e.target.value)}
            className={ENTRADA}
          />
        </Campo>

        <Campo rotulo="fim">
          <input
            type="datetime-local"
            value={dados.fim ?? ""}
            onChange={(e) => mudar("fim", e.target.value)}
            className={ENTRADA}
          />
        </Campo>

        {aula ? (
          <>
            <Campo rotulo="disciplina">
              <input
                value={dados.disciplina ?? ""}
                onChange={(e) => mudar("disciplina", e.target.value)}
                className={ENTRADA}
              />
            </Campo>
            <Campo rotulo="turma">
              <input
                value={dados.turma ?? ""}
                onChange={(e) => mudar("turma", e.target.value)}
                className={ENTRADA}
                placeholder="T01"
              />
            </Campo>
            <Campo rotulo="semestre">
              <input
                value={dados.semestre ?? ""}
                onChange={(e) => mudar("semestre", e.target.value)}
                className={ENTRADA}
                placeholder="2026.2"
              />
            </Campo>
          </>
        ) : (
          <>
            <Campo rotulo="categoria">
              <select
                value={dados.categoria ?? ""}
                onChange={(e) =>
                  mudar("categoria", e.target.value as Categoria)
                }
                className={ENTRADA}
              >
                <option value="">—</option>
                {CATEGORIAS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Campo>
            <Campo rotulo="horas complementares">
              <input
                type="number"
                min={0}
                step={0.5}
                value={dados.horas_complementares ?? ""}
                onChange={(e) =>
                  mudar(
                    "horas_complementares",
                    e.target.value === "" ? "" : Number(e.target.value),
                  )
                }
                className={ENTRADA}
              />
            </Campo>
            <Campo rotulo="órgão promotor">
              <input
                value={dados.orgao_promotor ?? ""}
                onChange={(e) => mudar("orgao_promotor", e.target.value)}
                className={ENTRADA}
              />
            </Campo>
          </>
        )}
      </div>

      {erro && (
        <p className="mt-3 rounded border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {erro}
        </p>
      )}

      <button
        type="submit"
        disabled={ocupado}
        className="mt-4 rounded bg-marca-500 px-4 py-2 text-sm font-medium text-white hover:bg-marca-600 disabled:opacity-50"
      >
        {ocupado ? "criando…" : "criar evento"}
      </button>
    </form>
  );
}

export function Cartao({
  evento,
  aoMudar,
  selecionado,
  aoSelecionar,
}: {
  evento: Evento;
  aoMudar: () => void;
  selecionado: boolean;
  aoSelecionar: (id: number) => void;
}) {
  const [erro, setErro] = useState("");
  const encerrado = evento.situacao === "encerrado";

  async function agir(fn: () => Promise<unknown>) {
    setErro("");
    try {
      await fn();
      aoMudar();
    } catch (erro) {
      setErro(erro instanceof ErroApi ? erro.message : "não deu certo");
    }
  }

  const quando = evento.inicio
    ? new Date(evento.inicio).toLocaleString("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : null;

  return (
    <div
      className={`rounded-lg border bg-white p-4 shadow-sm ${
        selecionado ? "border-marca-500 ring-1 ring-marca-500" : "border-slate-200"
      } ${encerrado ? "opacity-60" : ""}`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="checkbox"
          checked={selecionado}
          onChange={() => aoSelecionar(evento.id)}
          aria-label={`selecionar ${evento.chave}`}
          className="h-4 w-4"
        />
        <code className="rounded bg-slate-100 px-2 py-1 font-mono text-sm font-semibold tracking-widest text-slate-800">
          {evento.chave}
        </code>
        <span className="font-medium">{evento.nome}</span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
          {evento.tipo}
        </span>
        {encerrado && (
          <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-700">
            encerrado
          </span>
        )}

        <span className="ml-auto flex items-center gap-2">
          <button
            onClick={() => {
              // D34: encerrar pede confirmação; reabrir (o desfazer) não
              if (
                !encerrado &&
                !confirm(
                  `Encerrar "${evento.nome}" (${evento.chave})? A chave fica livre para outro ` +
                    "evento, e folhas novas com ela deixam de vir para cá. Dá para reabrir.",
                )
              )
                return;
              agir(() =>
                api.mudarSituacaoEvento(
                  evento.id,
                  encerrado ? "em andamento" : "encerrado",
                ),
              );
            }}
            className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700"
          >
            {encerrado ? "reabrir" : "encerrar"}
          </button>
          <button
            onClick={() => agir(() => api.apagarEvento(evento.id))}
            className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-rose-50 hover:text-rose-700"
          >
            apagar
          </button>
        </span>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
        {quando && <span>{quando}</span>}
        {evento.local && <span>{evento.local}</span>}
        {evento.responsavel && <span>{evento.responsavel}</span>}
        {evento.tipo === "aula" && evento.turma && (
          <span>
            {evento.disciplina} · {evento.turma} · {evento.semestre}
          </span>
        )}
        {evento.tipo === "evento" && evento.categoria && (
          <span>
            {evento.categoria}
            {evento.horas_complementares
              ? ` · ${evento.horas_complementares} h`
              : ""}
          </span>
        )}
        <span className="text-slate-400">
          {evento.folhas_enviadas} folha
          {evento.folhas_enviadas === 1 ? "" : "s"}
        </span>
      </div>

      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
    </div>
  );
}

/** Uma caixa "selecionar todos" que também mostra o meio-termo. */
function SelecionarTodos({
  ids,
  selecionados,
  aoTrocar,
}: {
  ids: number[];
  selecionados: Set<number>;
  aoTrocar: (ids: number[], marcar: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const marcados = ids.filter((i) => selecionados.has(i)).length;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = marcados > 0 && marcados < ids.length;
  }, [marcados, ids.length]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={ids.length > 0 && marcados === ids.length}
      onChange={() => aoTrocar(ids, marcados < ids.length)}
      aria-label="selecionar todos desta seção"
      className="h-4 w-4"
    />
  );
}

/** Um grupo de eventos (ou os sem grupo), com o que se faz com o grupo inteiro. */
function SecaoGrupo({
  nome,
  info,
  eventos,
  selecionados,
  aoSelecionar,
  aoTrocarVarios,
  aoMudar,
}: {
  nome: string | null;
  info?: Grupo;
  eventos: Evento[];
  selecionados: Set<number>;
  aoSelecionar: (id: number) => void;
  aoTrocarVarios: (ids: number[], marcar: boolean) => void;
  aoMudar: () => void;
}) {
  // aberta por padrão só quando é curta: os 150 eventos dos Encontros abertos
  // empurrariam os outros grupos para fora da tela
  const [aberta, setAberta] = useState(eventos.length <= 12);
  const [aviso, setAviso] = useState("");
  // D33: o grupo está encerrado quando nenhum evento dele está aberto
  const encerrado = !!info && info.eventos > 0 && (info.eventos_abertos ?? info.eventos) === 0;

  return (
    <section className="rounded-lg border border-slate-200 bg-slate-100/60 p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <SelecionarTodos
          ids={eventos.map((e) => e.id)}
          selecionados={selecionados}
          aoTrocar={aoTrocarVarios}
        />
        <button
          type="button"
          onClick={() => setAberta((a) => !a)}
          className="flex items-baseline gap-2 text-left"
        >
          <span className="text-slate-400">{aberta ? "▾" : "▸"}</span>
          <span className={`font-semibold ${nome ? "text-slate-800" : "text-slate-500"}`}>
            {nome ?? "sem grupo"}
          </span>
          {encerrado && (
            <span className="rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600">
              encerrado
            </span>
          )}
          <span className="text-sm tabular-nums text-slate-500">
            {eventos.length} evento{eventos.length === 1 ? "" : "s"}
            {info &&
              ` · ${info.folhas} folha${info.folhas === 1 ? "" : "s"}` +
                (info.folhas > 0 ? ` (${info.folhas_revisadas} revisada${info.folhas_revisadas === 1 ? "" : "s"})` : "")}
            {info && info.folhas > 0 && (
              // D30: o mesmo papel escaneado mais de uma vez conta uma vez só
              <span className="block text-xs">
                {(info.leitura_unica ?? 0) + (info.com_copia ?? 0)} papéis contam:{" "}
                {info.leitura_unica ?? 0} de leitura única, {info.com_copia ?? 0} com outro
                escaneamento guardado · {info.copias ?? 0} cópia{info.copias === 1 ? "" : "s"}{" "}
                que não conta{info.copias === 1 ? "" : "m"}
              </span>
            )}
          </span>
        </button>

        {nome && (
          <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <Link
              to={`/grupos/${encodeURIComponent(nome)}/quarentena`}
              className="text-marca-700 underline underline-offset-2"
            >
              ver a quarentena
            </Link>
            <a
              href={api.urlFrequencia(nome, "xlsx")}
              className="text-marca-700 underline underline-offset-2"
            >
              baixar planilha
            </a>
            <a
              href={api.urlFrequencia(nome, "json")}
              className="text-marca-700 underline underline-offset-2"
            >
              baixar JSON
            </a>
          </span>
        )}
      </div>

      {nome && info && (
        <div className="mt-1 flex flex-wrap items-start justify-end gap-x-4">
          {/* D33/D34: encerrar com o nome digitado, e "desfazer" */}
          <EncerrarGrupo nome={nome} info={info} aoMudar={aoMudar} />
          {info.folhas > 0 && (
          <ApagarEmMassa
            rotulo="apagar as folhas do grupo"
            previa={(incluir) => api.previaApagarDoGrupo(nome, incluir)}
            apagar={(n, incluir) => api.apagarDoGrupo(nome, n, incluir)}
            aoApagar={(feito) => {
              setAviso(`${feito.apagar} folha(s) apagada(s).`);
              aoMudar();
            }}
          />
          )}
        </div>
      )}
      {aviso && <p className="mt-1 text-right text-sm text-emerald-800">{aviso}</p>}

      {aberta && (
        <div className="mt-3 space-y-3">
          {eventos.map((e) => (
            <Cartao
              key={e.id}
              evento={e}
              aoMudar={aoMudar}
              selecionado={selecionados.has(e.id)}
              aoSelecionar={aoSelecionar}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/** A barra que aparece com eventos selecionados: pôr no grupo ou tirar dele. */
function BarraSelecao({
  quantos,
  grupos,
  aoAplicar,
  aoLimpar,
}: {
  quantos: number;
  grupos: Grupo[];
  aoAplicar: (grupo: string | null) => Promise<void>;
  aoLimpar: () => void;
}) {
  const [grupo, setGrupo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState("");

  async function aplicar(destino: string | null) {
    setOcupado(true);
    setErro("");
    try {
      await aoAplicar(destino);
      setGrupo("");
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-marca-200 bg-white/95 px-4 py-3 shadow-lg backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 text-sm">
        <span className="font-medium tabular-nums">
          {quantos} selecionado{quantos === 1 ? "" : "s"}
        </span>
        <input
          value={grupo}
          onChange={(e) => setGrupo(e.target.value)}
          list="grupos-da-barra"
          placeholder="nome do grupo, ex.: EU"
          className="w-48 rounded border border-slate-300 px-2 py-1.5 text-base sm:text-sm"
        />
        <ListaDeGrupos id="grupos-da-barra" grupos={grupos} />
        <button
          type="button"
          disabled={ocupado || !grupo.trim()}
          onClick={() => aplicar(grupo.trim())}
          className="rounded bg-marca-500 px-3 py-1.5 font-medium text-white hover:bg-marca-600 disabled:opacity-50"
        >
          pôr no grupo
        </button>
        <button
          type="button"
          disabled={ocupado}
          onClick={() => aplicar(null)}
          className="rounded px-3 py-1.5 text-slate-600 hover:bg-slate-100"
        >
          tirar do grupo
        </button>
        <button
          type="button"
          onClick={aoLimpar}
          className="ml-auto rounded px-3 py-1.5 text-slate-500 hover:bg-slate-100"
        >
          limpar seleção
        </button>
        {erro && <p className="w-full text-rose-700">{erro}</p>}
      </div>
    </div>
  );
}

export default function Eventos() {
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [grupos, setGrupos] = useState<Grupo[]>([]);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");

  const carregar = useCallback(async () => {
    try {
      const [ev, gr] = await Promise.all([api.listarEventos(), api.listarGrupos()]);
      setEventos(ev);
      setGrupos(gr);
      // um evento apagado não fica selecionado sem aparecer
      const existentes = new Set(ev.map((e) => e.id));
      setSelecionados((s) => new Set([...s].filter((i) => existentes.has(i))));
    } catch {
      setErro("não deu para carregar os eventos");
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  // uma seção por grupo, em ordem alfabética, e os sem grupo no fim
  const secoes = useMemo(() => {
    const porGrupo = new Map<string | null, Evento[]>();
    for (const e of eventos) {
      const lista = porGrupo.get(e.grupo) ?? [];
      lista.push(e);
      porGrupo.set(e.grupo, lista);
    }
    const nomes = [...porGrupo.keys()]
      .filter((g): g is string => g !== null)
      .sort((a, b) => a.localeCompare(b, "pt-BR"));
    return [...nomes, ...(porGrupo.has(null) ? [null] : [])].map((nome) => ({
      nome,
      eventos: porGrupo.get(nome)!,
    }));
  }, [eventos]);

  const alternar = useCallback((id: number) => {
    setSelecionados((s) => {
      const novo = new Set(s);
      if (novo.has(id)) novo.delete(id);
      else novo.add(id);
      return novo;
    });
  }, []);

  const trocarVarios = useCallback((ids: number[], marcar: boolean) => {
    setSelecionados((s) => {
      const novo = new Set(s);
      for (const i of ids) {
        if (marcar) novo.add(i);
        else novo.delete(i);
      }
      return novo;
    });
  }, []);

  async function aplicarGrupo(grupo: string | null) {
    const r = await api.porNoGrupo([...selecionados], grupo);
    setAviso(
      r.grupo
        ? `${r.alterados} evento(s) no grupo ${r.grupo}.`
        : `${r.alterados} evento(s) sem grupo.`,
    );
    setSelecionados(new Set());
    await carregar();
  }

  const info = new Map(grupos.map((g) => [g.nome, g]));

  return (
    <div className="space-y-6">
      <Formulario aoCriar={carregar} grupos={grupos} />

      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Eventos
        </h2>
        {erro && <p className="text-sm text-rose-700">{erro}</p>}
        {aviso && <p className="mb-2 text-sm text-emerald-800">{aviso}</p>}
        {eventos.length === 0 && !erro ? (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
            Nenhum evento ainda. A folha traz só a chave — sem evento
            cadastrado, ela não tem contra o que casar.
          </p>
        ) : (
          <div className="space-y-4">
            {secoes.map(({ nome, eventos: doGrupo }) => (
              <SecaoGrupo
                key={nome ?? ""}
                nome={nome}
                info={nome ? info.get(nome) : undefined}
                eventos={doGrupo}
                selecionados={selecionados}
                aoSelecionar={alternar}
                aoTrocarVarios={trocarVarios}
                aoMudar={carregar}
              />
            ))}
          </div>
        )}
      </div>

      {selecionados.size > 0 && (
        <BarraSelecao
          quantos={selecionados.size}
          grupos={grupos}
          aoAplicar={aplicarGrupo}
          aoLimpar={() => setSelecionados(new Set())}
        />
      )}
      {/* espaço para a barra fixa não cobrir o último evento */}
      {selecionados.size > 0 && <div className="h-20" />}
    </div>
  );
}
