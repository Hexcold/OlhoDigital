import { useCallback, useEffect, useState } from "react";
import { api, ErroApi } from "../api";
import type { BaseResumo, PreviaBase, SituacaoBase } from "../tipos";

/** A base de pessoas geral (tela inicial) ou a lista de um grupo (22/11).
 *
 *  A lista do grupo NÃO substitui a geral (A66, Experimento 04 parte 2): a identificação é
 *  contra a geral, e só sai aceito quem está na lista; o resto vai à revisão. Toda
 *  base nova passa por prévia; trocar com folhas já lidas avisa e oferece reler, e reler com
 *  decisão humana pede confirmação; apagar tira de uso, mas a base fica guardada enquanto
 *  alguma folha a usa. */

function data(iso: string | null): string {
  if (!iso) return "";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function Descricao({ b }: { b: BaseResumo }) {
  return (
    <span>
      <strong>{b.pessoas.toLocaleString("pt-BR")} pessoas</strong> · {b.arquivo}
      {b.data_referencia && <> · extração de {data(b.data_referencia)}</>}
      {b.origem === "semente" ? (
        <> · planilha do repositório</>
      ) : (
        b.enviada_em && (
          <>
            {" "}
            · enviada em {data(b.enviada_em)}
            {b.enviada_por && <> por {b.enviada_por}</>}
          </>
        )
      )}
    </span>
  );
}

function Previa({ p }: { p: PreviaBase }) {
  const fora = Object.entries(p.fora_por_status);
  return (
    <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-3 text-sm">
      <p>
        <strong>{p.arquivo}</strong>
        {p.aba && <> · aba {p.aba}</>}: {p.pessoas.toLocaleString("pt-BR")} pessoas.
        {p.comparada_com ? (
          <>
            {" "}
            Contra a {p.grupo ? "lista" : "base"} que vale hoje ({p.comparada_com.pessoas.toLocaleString("pt-BR")}):{" "}
            <strong>{p.entram}</strong> entram, <strong>{p.saem}</strong> saem,{" "}
            <strong>{p.mudam}</strong> mudam de nome ou curso.
          </>
        ) : (
          p.grupo ? "" : " Não há base valendo para comparar."
        )}
        {p.na_geral !== null && (
          <>
            {" "}
            {p.na_geral.toLocaleString("pt-BR")} estão na base geral
            {p.pessoas - p.na_geral > 0 &&
              `; ${(p.pessoas - p.na_geral).toLocaleString("pt-BR")} só nesta lista (identificadas por ela)`}
            .
          </>
        )}
      </p>
      {(fora.length > 0 || p.sem_matricula_ou_nome + p.matricula_invalida + p.repetidas > 0) && (
        <p className="mt-1 text-slate-600">
          Ficaram de fora:{" "}
          {[
            ...fora.map(([s, n]) => `${n} com status ${s}`),
            p.sem_matricula_ou_nome && `${p.sem_matricula_ou_nome} sem matrícula ou nome`,
            p.matricula_invalida && `${p.matricula_invalida} com matrícula que não é número`,
            p.repetidas && `${p.repetidas} repetidas`,
          ]
            .filter(Boolean)
            .join(", ")}
          .
        </p>
      )}
      {p.alertas.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-amber-900">
          {p.alertas.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function BasePessoas({ grupo }: { grupo?: string }) {
  const [sit, setSit] = useState<SituacaoBase | null>(null);
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [dataExtracao, setDataExtracao] = useState("");
  const [previa, setPrevia] = useState<PreviaBase | null>(null);
  const [conferiu, setConferiu] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [pedirForca, setPedirForca] = useState(false);

  const falhou = (e: unknown) => setErro(e instanceof ErroApi ? e.message : String(e));
  const recarregar = useCallback(() => {
    api.situacaoBase(grupo).then(setSit).catch(falhou);
  }, [grupo]);
  useEffect(recarregar, [recarregar]);

  const limpar = () => {
    setArquivo(null);
    setPrevia(null);
    setConferiu(false);
    setDataExtracao("");
  };

  // a prévia é refeita quando muda o arquivo ou a data: a data entra no alerta de lista velha
  useEffect(() => {
    if (!arquivo) return;
    setErro("");
    setOcupado(true);
    api
      .previaBase(arquivo, grupo, dataExtracao || undefined)
      .then((p) => {
        setPrevia(p);
        setConferiu(false);
        if (p.data_referencia && !dataExtracao) setDataExtracao(p.data_referencia);
      })
      .catch((e) => {
        setPrevia(null);
        falhou(e);
      })
      .finally(() => setOcupado(false));
  }, [arquivo, dataExtracao, grupo]);

  async function aplicar() {
    if (!arquivo) return;
    setOcupado(true);
    setErro("");
    try {
      const nova = await api.enviarBase(arquivo, grupo, dataExtracao || undefined);
      setSit(nova);
      setAviso(`${grupo ? "Lista nova" : "Base nova"} valendo: ${nova.propria?.pessoas.toLocaleString("pt-BR")} pessoas.`);
      limpar();
    } catch (e) {
      falhou(e);
    } finally {
      setOcupado(false);
    }
  }

  async function apagar(b: BaseResumo) {
    const volta = grupo
      ? "sem lista, qualquer pessoa da base geral volta a poder ser aceita"
      : "as folhas passam a ser identificadas só pelas listas dos grupos, e as sem lista falham até você enviar outra base";
    if (!confirm(`Apagar esta ${grupo ? "lista" : "base"} (${b.pessoas} pessoas)? ${volta[0].toUpperCase()}${volta.slice(1)}.`)) return;
    setOcupado(true);
    try {
      const r = await api.apagarBase(b.id);
      setSit(r);
      setAviso(
        r.resultado === "guardada"
          ? `${grupo ? "Lista" : "Base"} tirada de uso. Ela fica guardada porque há folhas lidas com ela, e a frequência dessas folhas não perde os nomes.`
          : `${grupo ? "Lista" : "Base"} apagada.`,
      );
    } catch (e) {
      falhou(e);
    } finally {
      setOcupado(false);
    }
  }

  async function reler(forcar: boolean) {
    setOcupado(true);
    setErro("");
    try {
      const r = await api.relerComABase(grupo, forcar);
      setSit(r);
      setPedirForca(false);
      setAviso(`${r.relidas} folha(s) voltaram para a fila e serão lidas com a ${grupo ? "lista" : "base"} atual.`);
    } catch (e) {
      if (e instanceof ErroApi && e.status === 409) setPedirForca(true);
      else falhou(e);
    } finally {
      setOcupado(false);
    }
  }

  const propria = sit?.propria ?? null;
  const geral = sit?.geral ?? null;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-base font-semibold">
        {grupo ? "Lista de pessoas do grupo" : "Base de pessoas geral"}
      </h2>
      <p className="mt-1 text-sm text-slate-600">
        {grupo
          ? "Quem pode ser aceito automaticamente nas folhas deste grupo (uma turma, os inscritos de um evento). A identificação continua usando a base geral, para que quem assinou e não está na lista seja reconhecido e vá para a revisão, em vez de virar a pessoa mais parecida da lista. Sem lista, qualquer pessoa da base geral é aceita."
          : "Quem as folhas podem identificar, em todos os grupos. Uma planilha .xlsx ou o .csv do SIGAA, com as colunas Matrícula e Nome (Curso e Status, se houver)."}
      </p>

      <div className="mt-3 text-sm">
        {!sit ? (
          <span className="text-slate-500">carregando…</span>
        ) : propria ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
              {grupo ? "lista valendo" : "valendo"}
            </span>
            <Descricao b={propria} />
            <button
              onClick={() => apagar(propria)}
              disabled={ocupado}
              className="text-slate-500 underline hover:text-rose-700"
            >
              apagar
            </button>
          </div>
        ) : grupo && geral ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
              sem lista
            </span>
            <span>qualquer pessoa da base geral ({geral.pessoas.toLocaleString("pt-BR")}) pode ser aceita</span>
          </div>
        ) : (
          <p className="rounded border border-rose-300 bg-rose-50 px-3 py-2 text-rose-900">
            Nenhuma base valendo: as folhas não têm contra quem ser identificadas. Envie a lista
            de matriculados abaixo.
          </p>
        )}
      </div>

      {sit && sit.desatualizadas > 0 && (
        <div className="mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {sit.desatualizadas} folha(s){grupo ? " deste grupo" : ""} foram lidas com outra base
          ou outra lista. Elas continuam valendo como estão; reler compara de novo com a base
          atual.{" "}
          {pedirForca ? (
            <>
              Reler descarta {sit.decisoes_humanas} decisão(ões) da revisão.{" "}
              <button onClick={() => reler(true)} disabled={ocupado} className="font-medium underline">
                reler mesmo assim
              </button>{" "}
              <button onClick={() => setPedirForca(false)} className="underline">
                cancelar
              </button>
            </>
          ) : (
            <button onClick={() => reler(false)} disabled={ocupado} className="font-medium underline">
              reler {sit.desatualizadas} folha(s)
            </button>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
        {/* Com uma lista escolhida, o botão forte da tela é o "usar esta": dois iguais
            lado a lado deixariam dúvida de qual dos dois grava. */}
        <label
          className={`rounded border px-4 py-2 text-sm font-medium focus-within:ring-2 focus-within:ring-marca-500 focus-within:ring-offset-1 ${
            ocupado ? "cursor-not-allowed opacity-50" : "cursor-pointer"
          } ${
            arquivo
              ? "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
              : "border-marca-600 bg-marca-600 text-white hover:bg-marca-700"
          }`}
        >
          {arquivo
            ? "Escolher outro arquivo"
            : propria
              ? "Enviar outra planilha"
              : "Enviar planilha"}
          <input
            type="file"
            accept=".xlsx,.csv"
            disabled={ocupado}
            className="sr-only"
            onChange={(e) => {
              const escolhido = e.target.files?.[0];
              // limpar permite reenviar o mesmo arquivo depois de corrigi-lo
              e.target.value = "";
              if (!escolhido) return;
              setAviso("");
              setPrevia(null);
              setDataExtracao("");
              setArquivo(escolhido);
            }}
          />
        </label>
        {propria && !arquivo && (
          <a
            href={api.urlPlanilhaBase(propria.id)}
            className="rounded border border-marca-300 px-4 py-2 text-sm text-marca-700 hover:bg-marca-50 focus-visible:ring-2 focus-visible:ring-marca-500"
          >
            {grupo ? "Baixar a lista atual" : "Baixar a base atual"}
          </a>
        )}
        {arquivo && (
          <label className="text-sm">
            <span className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              data da extração
            </span>
            <input
              type="date"
              value={dataExtracao}
              onChange={(e) => setDataExtracao(e.target.value)}
              className="mt-1 rounded border border-slate-300 px-2 py-1"
            />
          </label>
        )}
      </div>

      {ocupado && arquivo && !previa && <p className="mt-2 text-sm text-slate-500">lendo a lista…</p>}
      {previa && (
        <>
          <Previa p={previa} />
          {previa.alertas.length > 0 && (
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={conferiu} onChange={(e) => setConferiu(e.target.checked)} />
              conferi os alertas, aplicar mesmo assim
            </label>
          )}
          <div className="mt-3 flex gap-3">
            <button
              onClick={aplicar}
              disabled={ocupado || (previa.alertas.length > 0 && !conferiu)}
              className="rounded bg-marca-600 px-4 py-2 text-sm font-medium text-white hover:bg-marca-700 disabled:opacity-50"
            >
              {grupo ? "usar esta lista" : "usar esta base"}
            </button>
            <button onClick={limpar} className="text-sm underline">
              cancelar
            </button>
          </div>
        </>
      )}

      {aviso && <p className="mt-3 text-sm text-emerald-800">{aviso}</p>}
      {erro && <p className="mt-3 text-sm text-rose-700">{erro}</p>}
    </section>
  );
}
