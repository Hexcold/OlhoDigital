import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Linha, Participante } from "../tipos";
import { Leitura } from "./Leitura";

/** Igual a `REVISOR_AUTOMATICO` em `api/esquemas.py` (D26). */
export const REVISOR_AUTOMATICO = "automático: sem matrícula";

// O bloco de uma linha na revisão, com o relógio e a busca na base. Saiu de
// `Revisao.tsx` para a quarentena do grupo (D24) usar o MESMO bloco: duas
// versões da tela de decisão divergiriam, e o tempo medido por linha (A39)
// deixaria de ser comparável entre elas.

export interface Decidida {
  matricula: string | null;
  rotulo: string;
  ms: number;
  /** D32: a pessoa estava lá e não está na base; `matricula` é a que foi informada. */
  foraDaBase?: boolean;
}

/** D32: "não está na base", com a matrícula e o nome conferidos por quem revisa. */
export interface Fora {
  matricula: string;
  nome: string;
}

/** O formulário do "não está na base", preenchido com a leitura. Os `?` ficam à vista, para
 *  quem revisa corrigir o dígito que o OCR não leu. Sem matrícula, vale "não identificado"
 *  (decisão do autor, 20/11): presença sem matrícula não identifica ninguém. */
function ForaDaBase({
  linha,
  aoConfirmar,
  aoCancelar,
}: {
  linha: Linha;
  aoConfirmar: (fora: Fora | null) => void;
  aoCancelar: () => void;
}) {
  const [matricula, setMatricula] = useState(linha.leitura_matricula);
  const [nome, setNome] = useState(linha.leitura_nome.replace(/\?/g, "").trim());
  const limpa = matricula.trim();
  const invalida = limpa !== "" && !/^\d{5,6}$/.test(limpa);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (invalida) return;
        aoConfirmar(limpa ? { matricula: limpa, nome: nome.trim() } : null);
      }}
      className="mt-3 rounded border border-violet-200 bg-violet-50 p-3 text-sm"
    >
      <p className="text-violet-900">
        A pessoa estava lá, mas <strong>não está na base</strong>. Confira a matrícula e o nome;
        a presença entra na frequência como “fora da base”.
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[10rem_1fr]">
        <input
          value={matricula}
          onChange={(e) => setMatricula(e.target.value)}
          inputMode="numeric"
          placeholder="matrícula"
          aria-label="matrícula"
          className="rounded border border-slate-300 px-2 py-1.5 font-mono text-base outline-none focus:border-violet-500"
        />
        <input
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          placeholder="nome"
          aria-label="nome"
          className="rounded border border-slate-300 px-2 py-1.5 text-base outline-none focus:border-violet-500"
        />
      </div>
      {invalida && (
        <p className="mt-1 text-xs text-rose-700">A matrícula tem 5 ou 6 dígitos, sem “?”.</p>
      )}
      {!limpa && (
        <p className="mt-1 text-xs text-slate-600">Sem matrícula, fica como “não identificado”.</p>
      )}
      <div className="mt-2 flex gap-3">
        <button
          type="submit"
          disabled={invalida}
          className="rounded bg-violet-600 px-3 py-1.5 text-white hover:bg-violet-700 disabled:opacity-50"
        >
          {limpa ? "confirmar fora da base" : "gravar como não identificado"}
        </button>
        <button type="button" onClick={aoCancelar} className="text-slate-600 underline">
          cancelar
        </button>
      </div>
    </form>
  );
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
export function useRelogio() {
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

export function Bloco({
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
  aoDecidir: (linha: Linha, matricula: string | null, rotulo: string, fora?: Fora) => void;
  aoDesfazer: (linha: Linha) => void;
}) {
  const perigosa = linha.vizinhos.length > 0;
  const [foraAberto, setForaAberto] = useState(false);

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
                ✓ {decidida.foraDaBase && "fora da base · "}
                {decidida.matricula ?? "não identificado"}
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

          {foraAberto ? (
            <ForaDaBase
              linha={linha}
              aoCancelar={() => setForaAberto(false)}
              aoConfirmar={(fora) => {
                setForaAberto(false);
                if (fora) aoDecidir(linha, null, fora.nome, fora);
                else aoDecidir(linha, null, "");
              }}
            />
          ) : (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              <button
                onClick={() => aoDecidir(linha, null, "")}
                className="text-sm text-slate-500 underline underline-offset-2 hover:text-rose-700"
              >
                não identificado
              </button>
              <button
                onClick={() => setForaAberto(true)}
                className="text-sm text-slate-500 underline underline-offset-2 hover:text-violet-700"
              >
                não está na base
              </button>
            </div>
          )}
        </>
      )}
    </li>
  );
}

/** As decisões que já estão no servidor, no formato da tela. Recarregar a
 *  página, trocar de aparelho ou passar a revisão para outra pessoa não pode
 *  fazer a revisão parecer não feita. */
export function decisoesGravadas(linhas: Linha[]): Record<number, Decidida> {
  const feitas: Record<number, Decidida> = {};
  for (const linha of linhas) {
    if (!linha.revisao) continue;
    const escolhida = linha.revisao.matricula_escolhida;
    const informada = linha.revisao.matricula_informada;
    if (informada) {
      // D32: fora da base, com o que quem revisou conferiu
      feitas[linha.id] = {
        matricula: informada,
        rotulo: linha.revisao.nome_informado ?? "",
        ms: linha.revisao.ms ?? 0,
        foraDaBase: true,
      };
      continue;
    }
    feitas[linha.id] = {
      matricula: escolhida,
      // D26: o "não identificado" que a leitura grava sozinha na linha sem matrícula.
      // O rótulo diz que ninguém olhou, para o revisor saber que pode trocar.
      rotulo:
        linha.revisao.revisor === REVISOR_AUTOMATICO
          ? "padrão: linha sem matrícula"
          : (linha.candidatos.find((c) => c.matricula === escolhida)?.nome ?? ""),
      ms: linha.revisao.ms ?? 0,
    };
  }
  return feitas;
}
