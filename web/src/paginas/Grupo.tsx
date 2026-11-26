import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ErroApi } from "../api";
import BasePessoas from "../componentes/BasePessoas";
import { EncerrarGrupo } from "../componentes/EncerrarGrupo";
import type {
  CabecalhoAjuste,
  CabecalhoFolha,
  ChaveSugerida,
  EtapaGrupo,
  Evento,
  Grupo as GrupoT,
  ModeloGrupo,
} from "../tipos";
import { Cartao, Formulario } from "./Eventos";
import { PainelFolhas } from "./Folhas";
import { PASSOS, SeloEtapa } from "./Grupos";
import { Editor, type FonteCabecalho, Planilha } from "./Impressao";
import { QuarentenaDoGrupo } from "./QuarentenaGrupo";

type Aba = (typeof PASSOS)[number]["aba"];

/** A aba em que o grupo abre: a da etapa em que ele está. */
const ABA_DA_ETAPA: Record<EtapaGrupo, Aba> = {
  preparando: "eventos",
  recebendo: "envio",
  revisando: "revisao",
  pronto: "frequencia",
  encerrado: "frequencia",
};

function Cabecalho({ g, aoMudar }: { g: GrupoT; aoMudar: (novoNome?: string) => void }) {
  const [editando, setEditando] = useState(false);
  const [nome, setNome] = useState(g.nome);
  const [descricao, setDescricao] = useState(g.descricao ?? "");
  const [erro, setErro] = useState("");
  const navegar = useNavigate();

  if (editando) {
    return (
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setErro("");
          try {
            const novo = await api.renomearGrupo(g.nome, nome, descricao);
            setEditando(false);
            aoMudar(novo.nome);
          } catch (err) {
            setErro(err instanceof ErroApi ? err.message : String(err));
          }
        }}
        className="grid gap-2 sm:grid-cols-[1fr_2fr_auto_auto]"
      >
        <input
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          aria-label="nome do grupo"
          className="rounded border border-slate-300 px-3 py-2 text-base"
        />
        <input
          value={descricao}
          onChange={(e) => setDescricao(e.target.value)}
          placeholder="descrição"
          aria-label="descrição do grupo"
          className="rounded border border-slate-300 px-3 py-2 text-base"
        />
        <button type="submit" className="rounded bg-marca-600 px-3 py-2 text-sm text-white">
          salvar
        </button>
        <button type="button" onClick={() => setEditando(false)} className="text-sm underline">
          cancelar
        </button>
        {erro && <p className="text-sm text-rose-700 sm:col-span-4">{erro}</p>}
      </form>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Link to="/" className="text-sm text-marca-700 underline underline-offset-2">
        ← grupos
      </Link>
      <h1 className="text-xl font-semibold tracking-tight">{g.nome}</h1>
      <SeloEtapa etapa={g.etapa} />
      {g.descricao && <span className="text-sm text-slate-500">{g.descricao}</span>}
      <span className="ml-auto flex gap-3 text-sm">
        <button onClick={() => setEditando(true)} className="text-slate-600 underline">
          renomear
        </button>
        {g.eventos === 0 && (
          <button
            onClick={async () => {
              if (!confirm(`Apagar o grupo "${g.nome}"? Ele não tem eventos.`)) return;
              try {
                await api.apagarGrupo(g.nome);
                navegar("/");
              } catch (err) {
                setErro(err instanceof ErroApi ? err.message : String(err));
              }
            }}
            className="text-slate-600 underline hover:text-rose-700"
          >
            apagar
          </button>
        )}
      </span>
      {erro && <p className="w-full text-sm text-rose-700">{erro}</p>}
    </div>
  );
}

/** D35, fase 4: chaves livres e seguras, para usar no formulário. */
function SugerirChaves({ nome, aoUsar }: { nome: string; aoUsar: (chave: string) => void }) {
  const [sugestoes, setSugestoes] = useState<ChaveSugerida[] | null>(null);
  const [erro, setErro] = useState("");
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-base font-semibold">Chaves sugeridas</h2>
        <button
          onClick={() =>
            api
              .chavesSugeridas(nome, 10)
              .then(setSugestoes)
              .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)))
          }
          className="rounded border border-marca-300 px-3 py-1.5 text-sm text-marca-700 hover:bg-marca-50"
        >
          {sugestoes ? "sugerir de novo" : "sugerir chaves"}
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        Palavras nunca usadas, sem V (lido como W), com o máximo de letras que o leitor nunca erra
        e a pelo menos 4 letras de diferença de toda chave aberta e umas das outras — para que uma
        letra ilegível na folha não leve ao evento errado.
      </p>
      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
      {sugestoes && sugestoes.length === 0 && (
        <p className="mt-2 text-sm text-amber-800">
          Nenhuma palavra da lista passa nos critérios agora. Encerrar grupos antigos libera mais.
        </p>
      )}
      {sugestoes && sugestoes.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {sugestoes.map((s) => (
            <li key={s.chave}>
              <button
                onClick={() => aoUsar(s.chave)}
                title={
                  `${Math.round(s.letras_seguras * 100)}% letras seguras` +
                  (s.mais_proxima ? ` · a ${s.distancia} de ${s.mais_proxima}` : "")
                }
                className="rounded border border-slate-300 bg-slate-50 px-3 py-1.5 font-mono text-sm tracking-wider hover:border-marca-500 hover:bg-marca-50"
              >
                {s.chave}
              </button>
            </li>
          ))}
        </ul>
      )}
      {sugestoes && sugestoes.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">Clique numa chave para usá-la no evento abaixo.</p>
      )}
    </section>
  );
}

function AbaEventos({ g, aoMudar }: { g: GrupoT; aoMudar: () => void }) {
  const [eventos, setEventos] = useState<Evento[] | null>(null);
  const [chave, setChave] = useState<string>();
  const recarregar = useCallback(() => {
    api.listarEventos(g.nome).then(setEventos).catch(() => setEventos([]));
  }, [g.nome]);
  useEffect(recarregar, [recarregar]);
  const mudou = () => {
    recarregar();
    aoMudar();
  };

  return (
    <div className="space-y-4">
      <p className="rounded-lg border border-marca-200 bg-marca-50 px-4 py-3 text-sm text-marca-900">
        Cada evento é uma sala numa janela de tempo, com a <strong>chave</strong> que vai impressa
        na folha. Cadastre um a um abaixo, ou importe a <strong>planilha de chaves</strong> na aba{" "}
        <Link to="?aba=folha" className="underline">
          Folha
        </Link>
        , que cria os eventos e gera o PDF de uma vez.
      </p>
      <BasePessoas grupo={g.nome} />
      <SugerirChaves nome={g.nome} aoUsar={setChave} />
      <Formulario aoCriar={mudou} grupos={[]} grupoFixo={g.nome} chavePreenchida={chave} />
      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Eventos do grupo {eventos && `(${eventos.length})`}
        </h2>
        {!eventos && <p className="text-sm text-slate-500">carregando…</p>}
        {eventos && eventos.length === 0 && (
          <p className="text-sm text-slate-500">Nenhum evento ainda.</p>
        )}
        <div className="space-y-3">
          {eventos?.map((e) => (
            <Cartao key={e.id} evento={e} aoMudar={mudou} selecionado={false} aoSelecionar={() => {}} />
          ))}
        </div>
      </section>
    </div>
  );
}

const CAMPOS_TEXTO: { campo: keyof CabecalhoFolha; rotulo: string }[] = [
  { campo: "titulo", rotulo: "Título" },
  { campo: "subtitulo", rotulo: "Subtítulo" },
  { campo: "complemento", rotulo: "Complemento" },
  { campo: "instrucao", rotulo: "Instrução" },
];

/** D35, fase 3: o que cada evento troca no modelo do grupo. Campo vazio fica como no grupo. */
function AjusteDoEvento({
  evento,
  base,
  aoSalvar,
}: {
  evento: Evento;
  base: CabecalhoFolha;
  aoSalvar: () => void;
}) {
  const atual = evento.cabecalho_ajuste ?? {};
  const [aberto, setAberto] = useState(false);
  const [campos, setCampos] = useState<CabecalhoAjuste>(atual);
  const [previa, setPrevia] = useState("");
  const [erro, setErro] = useState("");
  const trocas = CAMPOS_TEXTO.filter(({ campo }) => atual[campo] != null);

  async function salvar(ajuste: CabecalhoAjuste) {
    setErro("");
    try {
      // vazio = fica como no grupo: só vai o que foi digitado
      const limpo = Object.fromEntries(
        Object.entries(ajuste).filter(([, v]) => v != null && v !== ""),
      );
      await api.ajustarEvento(evento.id, limpo);
      setAberto(false);
      aoSalvar();
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    }
  }

  async function verPrevia() {
    setErro("");
    try {
      const combinado = { ...base };
      for (const { campo } of CAMPOS_TEXTO)
        if (campos[campo]) combinado[campo] = campos[campo] as string;
      const blob = await api.previaCabecalho(combinado, evento.chave);
      setPrevia((antiga) => {
        if (antiga) URL.revokeObjectURL(antiga);
        return URL.createObjectURL(blob);
      });
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    }
  }

  return (
    <li className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <code className="font-mono text-sm font-semibold">{evento.chave}</code>
        <span className="text-sm">{evento.nome}</span>
        <span className="text-xs text-slate-500">
          {trocas.length
            ? trocas.map(({ campo, rotulo }) => `${rotulo}: “${atual[campo]}”`).join(" · ")
            : "usa o modelo do grupo"}
        </span>
        <button
          onClick={() => setAberto((a) => !a)}
          className="ml-auto text-sm text-marca-700 underline"
        >
          {aberto ? "fechar" : "ajustar texto"}
        </button>
      </div>
      {aberto && (
        <div className="mt-2 rounded border border-slate-200 bg-slate-50 p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            {CAMPOS_TEXTO.map(({ campo, rotulo }) => (
              <label key={campo} className="block text-xs text-slate-600">
                {rotulo}
                <input
                  value={campos[campo] ?? ""}
                  placeholder={base[campo] || "(vazio no grupo)"}
                  onChange={(e) => setCampos((c) => ({ ...c, [campo]: e.target.value }))}
                  className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                />
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Em branco, o campo fica como no modelo do grupo.
          </p>
          <div className="mt-2 flex flex-wrap gap-3 text-sm">
            <button onClick={() => salvar(campos)} className="rounded bg-marca-600 px-3 py-1.5 text-white">
              salvar ajuste
            </button>
            <button onClick={verPrevia} className="underline">
              ver prévia
            </button>
            {trocas.length > 0 && (
              <button onClick={() => salvar({})} className="text-slate-600 underline">
                voltar ao modelo do grupo
              </button>
            )}
          </div>
          {previa && <img src={previa} alt="Topo da folha deste evento" className="mt-2 w-full rounded border" />}
        </div>
      )}
      {erro && <p className="mt-1 text-sm text-rose-700">{erro}</p>}
    </li>
  );
}

/** Todas as folhas do grupo num PDF só, só para imprimir (pedido do autor, 20/11). */
function ImprimirGrupo({ g, eventos }: { g: GrupoT; eventos: Evento[] }) {
  const [desde, setDesde] = useState<number | null>(null);
  const [agora, setAgora] = useState(Date.now());
  const [aviso, setAviso] = useState("");
  const [erro, setErro] = useState("");
  const folhas = eventos.reduce((n, e) => n + (e.folhas_previstas ?? 1), 0);

  useEffect(() => {
    if (desde === null) return;
    const t = window.setInterval(() => setAgora(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [desde]);

  async function gerar() {
    setErro("");
    setAviso("");
    setDesde(Date.now());
    try {
      const blob = await api.pdfDoGrupo(g.nome);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `folhas_${g.nome}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
      setAviso("PDF pronto: confira a pasta de downloads.");
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setDesde(null);
    }
  }

  return (
    <section className="rounded-lg border border-marca-200 bg-white p-4">
      <h2 className="text-base font-semibold">Imprimir o grupo inteiro</h2>
      <p className="mt-1 text-sm text-slate-600">
        Um PDF só com as folhas de todos os eventos abertos ({eventos.length} evento
        {eventos.length === 1 ? "" : "s"}, {folhas} folha{folhas === 1 ? "" : "s"}), com o modelo
        do grupo e o texto de cada evento. É só abrir e mandar para a impressora.
      </p>
      <button
        onClick={gerar}
        disabled={desde !== null || eventos.length === 0}
        className="mt-3 rounded bg-marca-600 px-4 py-2 text-sm font-medium text-white hover:bg-marca-700 disabled:opacity-50"
      >
        {desde !== null
          ? `gerando… ${Math.round((agora - desde) / 1000)} s`
          : "baixar todas as folhas em PDF"}
      </button>
      {desde !== null && (
        <p className="mt-2 text-xs text-slate-500">
          Cada folha é desenhada e conferida (os marcadores precisam ser detectáveis antes de ir
          para a impressora). Com muitos eventos, leva alguns minutos; pode continuar usando o
          sistema.
        </p>
      )}
      {aviso && <p className="mt-2 text-sm text-green-800">{aviso}</p>}
      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
    </section>
  );
}

function AbaFolha({ g }: { g: GrupoT }) {
  const [modelo, setModelo] = useState<ModeloGrupo | null>(null);
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [pronto, setPronto] = useState(false);
  const [versao, setVersao] = useState(0);         // remonta o editor ao voltar ao padrão
  // com 150 eventos a lista de textos tomava a aba inteira: recolhida por padrão
  const [listaAberta, setListaAberta] = useState(false);
  const [erro, setErro] = useState("");

  const recarregar = useCallback(() => {
    api.modeloDoGrupo(g.nome).then(setModelo).catch((e) => setErro(String(e)));
    api
      .listarEventos(g.nome)
      .then((es) => setEventos(es.filter((e) => e.situacao === "em andamento")))
      .catch(() => setEventos([]));
  }, [g.nome]);
  useEffect(recarregar, [recarregar]);

  const fonte = useMemo<FonteCabecalho>(
    () => ({
      carregar: () => api.modeloDoGrupo(g.nome),
      salvar: (c) => api.salvarModeloDoGrupo(g.nome, c),
    }),
    [g.nome],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-marca-200 bg-marca-50 px-4 py-3 text-sm text-marca-900">
        <span>
          O modelo da folha de <strong>{g.nome}</strong>:{" "}
          {modelo?.proprio ? (
            <strong>próprio do grupo</strong>
          ) : (
            "usando o padrão do sistema (edite abaixo para o grupo ter o seu)"
          )}
          . Cada evento pode trocar um texto, mais abaixo.
        </span>
        {modelo?.proprio && (
          <button
            onClick={async () => {
              if (!confirm("Voltar ao modelo padrão do sistema? Os ajustes dos eventos ficam.")) return;
              await api.salvarModeloDoGrupo(g.nome, null);
              setVersao((v) => v + 1);
              recarregar();
            }}
            className="ml-auto underline"
          >
            voltar ao padrão
          </button>
        )}
      </div>
      {erro && <p className="text-sm text-rose-700">{erro}</p>}

      <ImprimirGrupo g={g} eventos={eventos} />

      <Editor key={versao} fonte={fonte} aoMudar={setPronto} aoSalvar={recarregar} />

      {modelo && eventos.length > 0 && (
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="text-base font-semibold">Texto por evento</h2>
          <p className="mt-1 text-sm text-slate-600">
            Um evento pode trocar um texto do modelo (o subtítulo com o nome da sessão, por
            exemplo). O que o sistema lê continua igual.{" "}
            {Object.keys(modelo.ajustes).length > 0 &&
              `${Object.keys(modelo.ajustes).length} evento(s) com texto próprio.`}
          </p>
          {eventos.length > 8 && (
            <button
              onClick={() => setListaAberta((a) => !a)}
              className="mt-2 text-sm text-marca-700 underline"
            >
              {listaAberta ? "recolher a lista" : `mostrar os ${eventos.length} eventos`}
            </button>
          )}
          {(eventos.length <= 8 || listaAberta) && (
          <ul className="mt-2 divide-y divide-slate-100">
            {eventos.map((e) => (
              <AjusteDoEvento key={`${e.id}-${JSON.stringify(e.cabecalho_ajuste)}`} evento={e} base={modelo.cabecalho} aoSalvar={recarregar} />
            ))}
          </ul>
          )}
        </section>
      )}

      <Planilha
        cabecalhoPronto={pronto}
        grupoFixo={g.nome}
        modelo={modelo ? { cabecalho: modelo.cabecalho, ajustes: modelo.ajustes } : undefined}
      />

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-base font-semibold">Imprimir pela conta de impressão</h2>
        <p className="mt-1 text-sm text-slate-600">
          Quem só imprime entra com a conta de impressão, que não vê os grupos. Baixe o pacote do
          grupo (o modelo, os textos por evento e as chaves dos eventos abertos, com as folhas
          previstas) e abra-o na tela de impressão, em “Pacote de um grupo”.
        </p>
        <a
          href={api.urlPacote(g.nome)}
          className="mt-2 inline-block rounded border border-marca-300 px-4 py-2 text-sm text-marca-700 hover:bg-marca-50"
        >
          baixar pacote de impressão
        </a>
      </section>
    </div>
  );
}

function AbaFrequencia({ g, aoMudar }: { g: GrupoT; aoMudar: () => void }) {
  const contam = (g.leitura_unica ?? 0) + (g.com_copia ?? 0);
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["papéis que contam", contam],
          ["cópias (não contam)", g.copias ?? 0],
          ["folhas concluídas", g.folhas_revisadas],
          ["quarentena pendente", g.pendentes ?? 0],
        ].map(([rotulo, n]) => (
          <div key={rotulo} className="rounded-lg border border-slate-200 bg-white p-3">
            <dt className="text-xs text-slate-500">{rotulo}</dt>
            <dd className="text-2xl font-semibold tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
      {(g.pendentes ?? 0) > 0 && (
        <p className="text-sm text-amber-800">
          Há linhas em quarentena sem decisão: na planilha elas saem como “pendente de revisão”.{" "}
          <Link to="?aba=revisao" className="underline">
            Revisar agora
          </Link>
          .
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <a
          href={api.urlFrequencia(g.nome, "xlsx")}
          className="rounded bg-marca-600 px-4 py-2 text-sm font-medium text-white hover:bg-marca-700"
        >
          baixar planilha
        </a>
        <a
          href={api.urlFrequencia(g.nome, "json")}
          className="rounded border border-marca-300 px-4 py-2 text-sm text-marca-700 hover:bg-marca-50"
        >
          baixar JSON
        </a>
      </div>
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Fechar a coleta
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Encerrar libera as chaves para outro grupo usar. Nada é apagado, e dá para reabrir.
        </p>
        <div className="mt-2">
          <EncerrarGrupo nome={g.nome} info={g} aoMudar={aoMudar} />
        </div>
      </section>
    </div>
  );
}

export default function Grupo() {
  const { nome = "" } = useParams();
  const [busca, setBusca] = useSearchParams();
  const [g, setG] = useState<GrupoT | null>(null);
  const [erro, setErro] = useState("");
  const navegar = useNavigate();

  const recarregar = useCallback(() => {
    api
      .obterGrupo(nome)
      .then((x) => {
        setG(x);
        setErro("");
      })
      .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)));
  }, [nome]);
  useEffect(recarregar, [recarregar]);

  // As contagens das abas e a etapa vêm deste resumo, e as abas mudam o grupo por conta
  // própria (a planilha cadastra eventos, a revisão decide linhas, a fila lê folhas). Na
  // bateria de 22/11, "Eventos 1" com 62 cadastrados e "Revisão 37" com 36 pendentes. É uma
  // consulta só: recarregar ao trocar de aba e a cada 10 s custa pouco e não esquece caminho.
  const abaNaUrl = busca.get("aba");
  useEffect(() => {
    recarregar();
    const t = window.setInterval(recarregar, 10_000);
    return () => window.clearInterval(t);
  }, [recarregar, abaNaUrl]);

  // No celular a barra de abas rola para o lado, e a aba aberta podia ficar fora da vista.
  const abaAtiva = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    abaAtiva.current?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [abaNaUrl, g?.etapa]);

  if (erro) {
    return (
      <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
        {erro} · <Link to="/" className="underline">voltar aos grupos</Link>
      </div>
    );
  }
  if (!g) return <p className="text-sm text-slate-500">carregando…</p>;

  const aba = (busca.get("aba") as Aba | null) ?? ABA_DA_ETAPA[g.etapa ?? "preparando"];

  return (
    <div className="space-y-4">
      <Cabecalho
        g={g}
        aoMudar={(novo) => {
          if (novo && novo !== g.nome) navegar(`/grupos/${encodeURIComponent(novo)}`);
          else recarregar();
        }}
      />

      <nav className="-mx-4 flex gap-1 overflow-x-auto border-b border-slate-200 px-4">
        {PASSOS.map((p, i) => (
          <button
            key={p.aba}
            ref={aba === p.aba ? abaAtiva : undefined}
            onClick={() => setBusca({ aba: p.aba })}
            className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${
              aba === p.aba
                ? "border-marca-600 font-medium text-marca-700"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-xs">
              {i + 1}
            </span>
            {p.texto}
            {p.aba === "eventos" && <span className="text-xs text-slate-400">{g.eventos}</span>}
            {p.aba === "revisao" && (g.pendentes ?? 0) > 0 && (
              <span className="rounded-full bg-amber-100 px-1.5 text-xs text-amber-800">
                {g.pendentes}
              </span>
            )}
          </button>
        ))}
      </nav>

      {aba === "eventos" && <AbaEventos g={g} aoMudar={recarregar} />}
      {aba === "folha" && <AbaFolha g={g} />}
      {aba === "envio" && <PainelFolhas grupo={g.nome} />}
      {aba === "revisao" && <QuarentenaDoGrupo nome={g.nome} embutida />}
      {aba === "frequencia" && <AbaFrequencia g={g} aoMudar={recarregar} />}
    </div>
  );
}
