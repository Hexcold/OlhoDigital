import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ErroApi, esperarProcessamento } from "../api";
import type { Folha, SugestaoEventos } from "../tipos";

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
  const temLinhas =
    folha.estado === "processada" || folha.estado === "suspeita";
  const revisada = folha.situacao === "revisada";

  async function reavaliar(modelo?: string) {
    const temTrabalho = folha.situacao === "revisada" || folha.pendentes === 0;
    if (
      temTrabalho &&
      !confirm(
        `Reler "${folha.nome_original}"${modelo ? ` como ${modelo}` : ""}? ` +
          "As leituras e as decisões humanas desta folha são descartadas.",
      )
    )
      return;
    setOcupado(true);
    setErro(null);
    try {
      await api.reprocessar(folha.id, modelo, true);
      aoMudar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
      setModelos([]);
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

      {folha.erro && (
        <p className="mt-2 rounded bg-rose-50 px-2 py-1 font-mono text-xs text-rose-800">
          {folha.erro}
        </p>
      )}
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
            onClick={async () => {
              // Reler com OUTRA geometria é o conserto do lixo silencioso: a
              // folha lida com o modelo errado não dá erro, dá 33 linhas de
              // nada. Escolher o modelo certo é o que a recupera.
              if (modelos.length) return setModelos([]);
              try {
                setModelos(await api.modelosFolha());
              } catch {
                reavaliar();
              }
            }}
            disabled={ocupado}
            className="rounded px-2 py-1.5 text-sm text-slate-500 hover:bg-marca-50 hover:text-marca-700 disabled:opacity-50"
          >
            {ocupado ? "…" : "reavaliar"}
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

export default function Folhas() {
  const [folhas, setFolhas] = useState<Folha[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [arrastando, setArrastando] = useState(false);
  const [saude, setSaude] = useState<Record<string, string> | null>(null);
  const [soBandeja, setSoBandeja] = useState(false);
  const controle = useRef<AbortController | null>(null);

  const recarregar = useCallback(async () => {
    try {
      setFolhas(await api.listarFolhas());
      setErro(null);
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    api.saude().then(setSaude).catch(() => setSaude(null));
    recarregar();
    const c = new AbortController();
    controle.current = c;
    return () => c.abort();
  }, [recarregar]);

  const enviar = useCallback(
    async (arquivos: File[]) => {
      if (!arquivos.length) return;
      setEnviando(true);
      setErro(null);
      try {
        for (const arquivo of arquivos) {
          // Um envio pode virar VÁRIAS folhas: PDF de escâner traz uma folha
          // por página, que é o formato real das 500.
          const criadas = await api.enviarFolha(arquivo);
          await recarregar();
          for (const folha of criadas) {
            esperarProcessamento(folha.id, undefined, controle.current?.signal)
              .then(recarregar)
              .catch(() => {});
          }
        }
      } catch (e) {
        setErro(e instanceof ErroApi ? e.message : String(e));
      } finally {
        setEnviando(false);
      }
    },
    [recarregar],
  );

  const naFila = folhas.filter(
    (f) => f.estado === "recebida" || f.estado === "processando",
  ).length;
  const bancoFora = saude?.banco && saude.banco !== "ok";
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
          {enviando ? "enviando…" : "Enviar folhas digitalizadas"}
        </div>
        <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500">
          PDF do escâner (uma folha por página) ou imagens. Toque para escolher,
          ou arraste os arquivos aqui.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          Cada folha leva cerca de 9 segundos, e são lidas uma por vez.
        </p>
      </label>

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
