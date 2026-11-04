import { useCallback, useEffect, useState } from "react";
import { api, ErroApi, type EventoEntrada } from "../api";
import { CATEGORIAS, type Categoria, type Evento, type TipoEvento } from "../tipos";

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

function Formulario({ aoCriar }: { aoCriar: () => void }) {
  const [dados, setDados] = useState<EventoEntrada>(VAZIO);
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
      tipo,
    }));

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setErro("");
    setOcupado(true);
    try {
      await api.criarEvento({ ...dados, chave: dados.chave.toUpperCase() });
      setDados(VAZIO);
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

function Cartao({ evento, aoMudar }: { evento: Evento; aoMudar: () => void }) {
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
        encerrado ? "border-slate-200 opacity-60" : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-3">
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
            onClick={() =>
              agir(() =>
                api.mudarSituacaoEvento(
                  evento.id,
                  encerrado ? "em andamento" : "encerrado",
                ),
              )
            }
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

export default function Eventos() {
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [erro, setErro] = useState("");

  const carregar = useCallback(async () => {
    try {
      setEventos(await api.listarEventos());
    } catch {
      setErro("não deu para carregar os eventos");
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  return (
    <div className="space-y-6">
      <Formulario aoCriar={carregar} />

      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Eventos
        </h2>
        {erro && <p className="text-sm text-rose-700">{erro}</p>}
        {eventos.length === 0 && !erro ? (
          <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
            Nenhum evento ainda. A folha traz só a chave — sem evento
            cadastrado, ela não tem contra o que casar.
          </p>
        ) : (
          <div className="space-y-3">
            {eventos.map((e) => (
              <Cartao key={e.id} evento={e} aoMudar={carregar} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
