import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ErroApi } from "../api";
import { Bloco, decisoesGravadas, useRelogio, type Decidida, type Fora } from "../componentes/BlocoLinha";
import type { Linha, LinhaDoGrupo } from "../tipos";

/** Todas as quarentenas de um grupo de eventos numa tela só (D24).
 *
 *  Revisar folha a folha obriga a abrir 62 páginas para decidir 55 linhas. Aqui
 *  as linhas em quarentena de todas as folhas do grupo vêm juntas, na ordem
 *  evento → folha → linha, com o MESMO bloco de decisão da revisão de uma folha.
 *
 *  Concluir continua sendo por folha: o carimbo diz que AQUELA folha foi
 *  conferida inteira, e a quarentena é só uma parte dela. Cada folha tem o link
 *  para a página dela. */
export default function QuarentenaGrupo() {
  const { nome = "" } = useParams();
  return <QuarentenaDoGrupo nome={nome} />;
}

/** D35: a mesma tela, para a aba "Revisão" da página do grupo. */
export function QuarentenaDoGrupo({ nome, embutida = false }: { nome: string; embutida?: boolean }) {
  const [linhas, setLinhas] = useState<LinhaDoGrupo[] | null>(null);
  const [decididas, setDecididas] = useState<Record<number, Decidida>>({});
  // as decididas ANTES de abrir a tela: escondê-las por padrão deixa à vista o
  // que falta. As decididas agora continuam à vista, para dar para mudar.
  const [jaVinham, setJaVinham] = useState<Set<number>>(new Set());
  const [mostrarDecididas, setMostrarDecididas] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [errosLinha, setErrosLinha] = useState<Record<number, string>>({});
  const { marcarFoco, parar } = useRelogio();

  useEffect(() => {
    api
      .quarentenaDoGrupo(nome)
      .then((l) => {
        setLinhas(l);
        const gravadas = decisoesGravadas(l);
        setDecididas(gravadas);
        setJaVinham(new Set(Object.keys(gravadas).map(Number)));
      })
      .catch((e) => setErro(e instanceof ErroApi ? e.message : String(e)));
  }, [nome]);

  // o mesmo decidir da revisão de uma folha: otimista, desfeito se o servidor recusar
  const decidir = useCallback(
    async (linha: Linha, matricula: string | null, rotulo: string, fora?: Fora) => {
      const ms = parar(linha.id);
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

  const desfazer = useCallback((linha: Linha) => {
    setDecididas((d) => {
      const copia = { ...d };
      delete copia[linha.id];
      return copia;
    });
  }, []);

  // uma seção por folha, na ordem em que o servidor mandou
  const porFolha = useMemo(() => {
    const visiveis = (linhas ?? []).filter(
      (l) => mostrarDecididas || !jaVinham.has(l.id),
    );
    const secoes: { folhaId: number; topo: LinhaDoGrupo; linhas: LinhaDoGrupo[] }[] = [];
    for (const l of visiveis) {
      const ultima = secoes[secoes.length - 1];
      if (ultima?.folhaId === l.folha_id) ultima.linhas.push(l);
      else secoes.push({ folhaId: l.folha_id, topo: l, linhas: [l] });
    }
    return secoes;
  }, [linhas, mostrarDecididas, jaVinham]);

  if (erro && !linhas) {
    return (
      <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
        {erro}
      </div>
    );
  }
  if (!linhas) return <p className="text-sm text-slate-500">carregando…</p>;

  const total = linhas.length;
  // pendente é o que trava a conclusão da folha: quarentena sem decisão, em folha aberta
  const pendentes = linhas.filter((l) => !decididas[l.id] && !l.folha_concluida).length;
  const folhasComPendencia = new Set(
    linhas.filter((l) => !decididas[l.id] && !l.folha_concluida).map((l) => l.folha_id),
  ).size;

  return (
    <>
      <div
        className={
          embutida
            ? "mb-2 border-b border-slate-200 py-2"
            : "sticky top-[3.25rem] z-10 -mx-4 mb-2 border-b border-slate-200 bg-slate-50/95 px-4 py-2 backdrop-blur"
        }
      >
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
          {!embutida && (
            <Link
              to={`/grupos/${encodeURIComponent(nome)}`}
              className="text-marca-700 underline underline-offset-2"
            >
              ← {nome}
            </Link>
          )}
          <span className="font-medium">{embutida ? "quarentena" : `quarentena do grupo ${nome}`}</span>
          <span className="tabular-nums text-slate-600">
            {pendentes} pendente{pendentes === 1 ? "" : "s"} de {total}
            {pendentes > 0 &&
              ` · em ${folhasComPendencia} folha${folhasComPendencia === 1 ? "" : "s"}`}
          </span>
          {jaVinham.size > 0 && (
            <label className="ml-auto flex items-center gap-2 text-slate-600">
              <input
                type="checkbox"
                checked={mostrarDecididas}
                onChange={(e) => setMostrarDecididas(e.target.checked)}
              />
              mostrar as já decididas ({jaVinham.size})
            </label>
          )}
        </div>
      </div>

      {erro && (
        <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {erro}
        </div>
      )}

      {porFolha.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
          {total === 0
            ? "Nenhuma linha em quarentena neste grupo."
            : "Toda a quarentena deste grupo já foi decidida."}
        </p>
      ) : (
        porFolha.map(({ folhaId, topo, linhas: daFolha }) => (
          <section key={folhaId} className="mt-6">
            <h2 className="flex flex-wrap items-baseline gap-x-3 text-sm">
              <code className="rounded bg-slate-100 px-2 py-0.5 font-mono font-semibold tracking-widest text-slate-800">
                {topo.evento_chave}
              </code>
              <span className="text-slate-600">{topo.evento_nome}</span>
              <Link
                to={`/folhas/${folhaId}`}
                className="text-marca-700 underline underline-offset-2"
              >
                {topo.folha_nome}
                {topo.folha_pagina > 1 && ` · página ${topo.folha_pagina}`}
              </Link>
              {topo.folha_concluida && (
                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
                  folha concluída
                </span>
              )}
            </h2>
            <ul className="mt-3 space-y-3">
              {daFolha.map((l) => (
                <Bloco
                  key={l.id}
                  linha={l}
                  decidida={decididas[l.id]}
                  travada={l.folha_concluida}
                  aoFocar={marcarFoco}
                  aoDecidir={decidir}
                  aoDesfazer={desfazer}
                  erro={errosLinha[l.id]}
                />
              ))}
            </ul>
          </section>
        ))
      )}

      <p className="mt-8 text-xs text-slate-500">
        Decidir aqui grava a mesma revisão que a página da folha. Concluir continua
        sendo folha a folha, pelo link de cada uma.
      </p>
      <div className="h-12" />
    </>
  );
}
