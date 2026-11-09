import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ErroApi } from "../api";
import type { CabecalhoFolha, Lote, Sessao } from "../tipos";

/** Preparação da impressão: os textos da folha e o lote por planilha.
 *
 *  O editor só alcança textos que o sistema não lê. Marcadores, grade e células
 *  não têm campo aqui nem no servidor, que recusa qualquer outro. */

const VAZIO: CabecalhoFolha = {
  titulo: "",
  subtitulo: "",
  complemento: "",
  instrucao: "",
};

const ENTRADA =
  "mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm " +
  "focus:border-marca-500 focus:outline-none focus:ring-1 focus:ring-marca-500";

const BOTAO =
  "rounded bg-marca-600 px-3 py-1.5 text-sm font-medium text-white " +
  "hover:bg-marca-700 disabled:cursor-not-allowed disabled:opacity-50";

const BOTAO_SECUNDARIO =
  "rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 " +
  "hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50";

type DefinicaoCampo = { campo: keyof CabecalhoFolha; rotulo: string };

const CAMPOS_TOPO: DefinicaoCampo[] = [
  { campo: "titulo", rotulo: "Título" },
  { campo: "subtitulo", rotulo: "Subtítulo" },
  { campo: "complemento", rotulo: "Complemento" },
];

const CAMPO_INSTRUCAO: DefinicaoCampo = {
  campo: "instrucao",
  rotulo: "Instrução",
};

const CAMPOS = [...CAMPOS_TOPO, CAMPO_INSTRUCAO];

const ROTULO_CAMPO = "text-xs font-medium uppercase tracking-wide text-slate-500";

const mensagem = (e: unknown) =>
  e instanceof ErroApi ? e.message : "não consegui falar com o servidor";

const iguais = (a: CabecalhoFolha, b: CabecalhoFolha) =>
  CAMPOS.every(({ campo }) => a[campo] === b[campo]);

function baixar(blob: Blob, nome: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = nome;
  link.click();
  URL.revokeObjectURL(url);
}

function Editor({ aoMudar }: { aoMudar: (pronto: boolean) => void }) {
  const [salvo, setSalvo] = useState<CabecalhoFolha | null>(null);
  const [campos, setCampos] = useState<CabecalhoFolha>(VAZIO);
  const [chave, setChave] = useState("");
  const [folhas, setFolhas] = useState(1);
  const [limite, setLimite] = useState(0);
  const [travado, setTravado] = useState<string[]>([]);
  const [previa, setPrevia] = useState("");
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [ocupado, setOcupado] = useState("");

  useEffect(() => {
    api
      .cabecalho()
      .then((r) => {
        setSalvo(r.cabecalho);
        setCampos(r.cabecalho);
        setLimite(r.limite_caracteres);
        setTravado(r.travado);
      })
      .catch((e) => setErro(mensagem(e)));
  }, []);

  // A prévia sai do mesmo renderizador da impressão, então cada tecla custaria
  // uma ida ao servidor. A espera junta a digitação, e o cancelamento impede
  // que uma resposta velha cubra a mais nova.
  useEffect(() => {
    if (!salvo) return;
    const controle = new AbortController();
    const espera = setTimeout(async () => {
      try {
        const blob = await api.previaCabecalho(campos, chave, controle.signal);
        setErro("");
        setPrevia((antiga) => {
          if (antiga) URL.revokeObjectURL(antiga);
          return URL.createObjectURL(blob);
        });
      } catch (e) {
        if ((e as Error).name !== "AbortError") setErro(mensagem(e));
      }
    }, 300);
    return () => {
      clearTimeout(espera);
      controle.abort();
    };
  }, [campos, chave, salvo]);

  const mudou = salvo !== null && !iguais(campos, salvo);

  useEffect(() => {
    aoMudar(salvo !== null && !mudou);
  }, [salvo, mudou, aoMudar]);

  async function gravar() {
    const r = await api.salvarCabecalho(campos);
    setSalvo(r.cabecalho);
    setCampos(r.cabecalho);
  }

  async function executar(nome: string, acao: () => Promise<void>) {
    setOcupado(nome);
    setErro("");
    setAviso("");
    try {
      await acao();
    } catch (e) {
      setErro(mensagem(e));
    } finally {
      setOcupado("");
    }
  }

  const salvar = () => executar("salvando", gravar);

  // O PDF sai sempre do que está salvo, então gerar grava primeiro: o que a
  // prévia mostra é o que vai para o arquivo, sem um passo a mais para esquecer.
  const gerar = () =>
    executar("gerando", async () => {
      if (mudou) await gravar();
      const blob = await api.gerarFolhas(chave, folhas);
      baixar(blob, `folhas_${chave || "sem_chave"}_${folhas}paginas.pdf`);
      setAviso(
        `PDF gerado: ${folhas} folha(s), ${chave ? `chave ${chave}` : "sem chave"}.`,
      );
    });

  const campo = ({ campo: nome, rotulo }: DefinicaoCampo) => (
    <label key={nome} className="block">
      <span className={ROTULO_CAMPO}>{rotulo}</span>
      <input
        className={ENTRADA}
        value={campos[nome]}
        maxLength={limite || undefined}
        onChange={(e) => setCampos((c) => ({ ...c, [nome]: e.target.value }))}
      />
    </label>
  );

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-base font-semibold">Folha</h2>
      <p className="mt-1 text-sm text-slate-600">
        Edite os textos da folha e, se precisar, a chave. O que o sistema lê não
        muda.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        {CAMPOS_TOPO.map(campo)}
      </div>
      <div className="mt-3">{campo(CAMPO_INSTRUCAO)}</div>

      {previa && (
        <img
          src={previa}
          alt="Topo da folha, como será impresso"
          className="mt-3 w-full rounded border border-slate-200"
        />
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className={ROTULO_CAMPO}>Chave</span>
          <input
            className={`${ENTRADA} w-48 font-mono`}
            value={chave}
            maxLength={16}
            placeholder="em branco"
            onChange={(e) => setChave(e.target.value.toUpperCase())}
          />
        </label>
        <label className="block">
          <span className={ROTULO_CAMPO}>Folhas</span>
          <input
            type="number"
            min={1}
            className={`${ENTRADA} w-24`}
            value={folhas}
            onChange={(e) => setFolhas(Number(e.target.value))}
          />
        </label>
        <button
          type="button"
          className={BOTAO}
          disabled={!salvo || !!erro || !!ocupado || folhas < 1}
          onClick={gerar}
        >
          {ocupado === "gerando"
            ? "gerando…"
            : `Gerar PDF (${folhas} ${folhas === 1 ? "folha" : "folhas"})`}
        </button>
        <button
          type="button"
          className={BOTAO_SECUNDARIO}
          disabled={!mudou || !!ocupado || !!erro}
          onClick={salvar}
        >
          {ocupado === "salvando" ? "salvando…" : "Salvar textos"}
        </button>
        <span className="pb-2 text-xs text-slate-500">
          {mudou ? "alterações não salvas" : "salvo"}
        </span>
      </div>

      {chave.includes("V") && (
        <p className="mt-2 text-sm text-amber-800">
          Evite V na chave: impresso, ele é lido como W, e a folha cai na bandeja.
        </p>
      )}
      {erro && <p className="mt-2 text-sm text-red-700">{erro}</p>}
      {aviso && <p className="mt-2 text-sm text-green-800">{aviso}</p>}

      {travado.length > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          Travado, fora do editor: {travado.join(", ")}.
        </p>
      )}
    </section>
  );
}

function Planilha({ cabecalhoPronto }: { cabecalhoPronto: boolean }) {
  const eu = useOutletContext<Sessao>();
  const [lote, setLote] = useState<Lote | null>(null);
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [aceito, setAceito] = useState(false);
  const [ocupado, setOcupado] = useState("");
  // o arquivo fica guardado para reler com outro corte sem escolher de novo
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [ateLinha, setAteLinha] = useState("");
  const [corteLido, setCorteLido] = useState("");

  async function executar(nome: string, acao: () => Promise<void>) {
    setOcupado(nome);
    setErro("");
    setAviso("");
    try {
      await acao();
    } catch (e) {
      setErro(mensagem(e));
    } finally {
      setOcupado("");
    }
  }

  const enviar = (a: File, corte: string) =>
    executar("lendo", async () => {
      setLote(null);
      setAceito(false);
      setArquivo(a);
      setCorteLido(corte);
      setLote(
        await api.enviarPlanilha(a, corte.trim() ? Number(corte) : undefined),
      );
    });

  const gerar = (l: Lote) =>
    executar("gerando", async () => {
      const blob = await api.gerarPdf(l.itens, aceito);
      baixar(
        blob,
        `folhas_${l.total_chaves}chaves_${l.total_folhas}paginas.pdf`,
      );
      setAviso(`PDF gerado: ${l.total_folhas} páginas.`);
    });

  const cadastrar = (l: Lote) =>
    executar("cadastrando", async () => {
      const r = await api.cadastrarChaves(l.itens);
      setAviso(
        `${r.criados} evento(s) cadastrado(s), ${r.ja_existiam} já existia(m).`,
      );
    });

  const podeGerar =
    !!lote &&
    lote.itens.length > 0 &&
    lote.erros.length === 0 &&
    (lote.alertas.length === 0 || aceito) &&
    cabecalhoPronto &&
    !ocupado;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-base font-semibold">Planilha de chaves</h2>
      <p className="mt-1 text-sm text-slate-600">
        Envie um <code>.xlsx</code> ou <code>.csv</code> com a coluna{" "}
        <code>chave</code> (ou <code>key</code>) e, se quiser, <code>folhas</code>
        . Sem <code>folhas</code>, sai uma folha por chave. As colunas{" "}
        <code>nome</code> (ou <code>tag</code>), <code>local</code>,{" "}
        <code>responsavel</code> e <code>dia</code> (ou <code>day</code>) são
        opcionais. Com dia, o carimbo da folha sai “v4 · dia/mês/ano”; sem
        ele, só “v4”.
      </p>
      <p className="mt-1 text-sm text-slate-600">
        A chave aceita só letras, até 16. As mais seguras impressas são B, D, E,
        F, G, H, K, L, M, P, Q, R, S, T, W e Z. Evite V: ele é lido como W.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          Até a linha
          <input
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            placeholder="todas"
            value={ateLinha}
            onChange={(e) => setAteLinha(e.target.value)}
            className="w-24 rounded border border-slate-300 px-2 py-1.5 text-sm focus:border-marca-500 focus:outline-none focus:ring-1 focus:ring-marca-500"
          />
        </label>
        <label className={`${BOTAO} cursor-pointer`}>
          {ocupado === "lendo" ? "lendo…" : "Enviar planilha"}
          <input
            type="file"
            accept=".xlsx,.csv"
            className="sr-only"
            onChange={(e) => {
              const escolhido = e.target.files?.[0];
              // limpar permite reenviar o mesmo arquivo depois de corrigi-lo
              e.target.value = "";
              if (escolhido) enviar(escolhido, ateLinha);
            }}
          />
        </label>
        {arquivo && ateLinha !== corteLido && (
          <button
            type="button"
            className={BOTAO_SECUNDARIO}
            disabled={!!ocupado}
            onClick={() => enviar(arquivo, ateLinha)}
          >
            Aplicar o corte
          </button>
        )}
        <a className={BOTAO_SECUNDARIO} href={api.urlModeloPlanilha()}>
          Baixar modelo
        </a>
      </div>

      {erro && <p className="mt-3 text-sm text-red-700">{erro}</p>}

      {lote && (
        <div className="mt-4">
          <p className="text-sm font-medium">
            {lote.total_chaves} chave(s), {lote.total_folhas} folha(s)
            {corteLido.trim() && ` · até a linha ${corteLido.trim()}`}
            {arquivo && ` · ${arquivo.name}`}
          </p>

          {lote.erros.length > 0 && (
            <div className="mt-2 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
              <p className="font-medium">
                Corrija a planilha e envie de novo:
              </p>
              <ul className="mt-1 list-disc pl-5">
                {lote.erros.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {lote.alertas.length > 0 && (
            <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-medium">Confira as chaves:</p>
              <ul className="mt-1 list-disc pl-5">
                {lote.alertas.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
              <label className="mt-2 flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={aceito}
                  onChange={(e) => setAceito(e.target.checked)}
                />
                Entendi, gerar mesmo assim
              </label>
            </div>
          )}

          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-1 pr-3 font-medium">Linha</th>
                  <th className="py-1 pr-3 font-medium">Chave</th>
                  <th className="py-1 pr-3 font-medium">Folhas</th>
                  <th className="py-1 pr-3 font-medium">Nome</th>
                  <th className="py-1 pr-3 font-medium">Dia</th>
                  <th className="py-1 font-medium">Local</th>
                </tr>
              </thead>
              <tbody>
                {lote.itens.map((i) => (
                  <tr key={i.linha} className="border-t border-slate-100">
                    <td className="py-1 pr-3 text-slate-500">{i.linha}</td>
                    <td className="py-1 pr-3 font-mono">{i.chave}</td>
                    <td className="py-1 pr-3">{i.folhas}</td>
                    <td className="py-1 pr-3">{i.nome ?? "—"}</td>
                    <td className="py-1 pr-3">
                      {i.dia ? i.dia.split("-").reverse().join("/") : "—"}
                    </td>
                    <td className="py-1">{i.local ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={BOTAO}
              disabled={!podeGerar}
              onClick={() => gerar(lote)}
            >
              {ocupado === "gerando"
                ? "gerando…"
                : `Gerar PDF (${lote.total_folhas} páginas)`}
            </button>
            {eu.papel === "admin" && (
              <button
                type="button"
                className={BOTAO_SECUNDARIO}
                disabled={lote.erros.length > 0 || !!ocupado}
                onClick={() => cadastrar(lote)}
              >
                {ocupado === "cadastrando"
                  ? "cadastrando…"
                  : "Cadastrar como eventos"}
              </button>
            )}
          </div>

          {!cabecalhoPronto && (
            <p className="mt-2 text-xs text-slate-500">
              Salve os textos antes de gerar: o PDF usa o que está salvo.
            </p>
          )}
          {aviso && <p className="mt-2 text-sm text-green-800">{aviso}</p>}

          <p className="mt-3 text-xs text-slate-500">
            Imprima em A4, a 100%, sem ajustar à página. Escala diferente muda o
            tamanho da célula em que a folha foi medida.
          </p>
        </div>
      )}
    </section>
  );
}

export default function Impressao() {
  const [cabecalhoPronto, setCabecalhoPronto] = useState(false);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Impressão</h1>
      <Editor aoMudar={setCabecalhoPronto} />
      <Planilha cabecalhoPronto={cabecalhoPronto} />
    </div>
  );
}
