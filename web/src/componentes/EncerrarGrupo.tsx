import { useState } from "react";
import { api, ErroApi } from "../api";
import type { Grupo } from "../tipos";

/** Encerrar ou reabrir o grupo inteiro (D33), com as travas do D34: o nome digitado para
 *  encerrar, e o "desfazer" logo depois. Usado na tela de eventos e na aba de frequência. */
export function EncerrarGrupo({
  nome,
  info,
  aoMudar,
}: {
  nome: string;
  info: Grupo;
  aoMudar: () => void;
}) {
  const [aviso, setAviso] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const encerrado = info.eventos > 0 && (info.eventos_abertos ?? info.eventos) === 0;
  // D34: encerrar o grupo pede o nome digitado, e o sucesso traz um "desfazer"
  const [confirmando, setConfirmando] = useState(false);
  const [digitado, setDigitado] = useState("");
  const [desfazer, setDesfazer] = useState(false);

  async function mudar(situacao: "encerrado" | "em andamento", confirmacao?: string) {
    setOcupado(true);
    setAviso("");
    try {
      const r = await api.mudarSituacaoGrupo(nome, situacao, confirmacao);
      if (r.situacao === "encerrado") {
        setAviso(`${r.eventos} evento(s) encerrado(s); as chaves estão livres.`);
        setDesfazer(true);
      } else {
        setAviso(
          `${r.eventos} evento(s) reabertos.` +
            (r.folhas_religadas
              ? ` ${r.folhas_religadas} folha(s) que tinham caído em “sem evento” voltaram ao grupo.`
              : ""),
        );
        setDesfazer(false);
      }
      setConfirmando(false);
      setDigitado("");
      aoMudar();
    } catch (e) {
      setAviso(e instanceof ErroApi ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  }

  function trocarSituacao() {
    if (encerrado) {
      if (confirm(`Reabrir os ${info.eventos} eventos de "${nome}"? As chaves voltam a valer.`))
        mudar("em andamento");
      return;
    }
    setConfirmando((c) => !c);
  }


  return (
    <div className="text-sm">
      <div className="text-right">
        <button
          type="button"
          onClick={trocarSituacao}
          disabled={ocupado || info.eventos === 0}
          className="text-slate-600 underline underline-offset-2 hover:text-rose-700 disabled:opacity-50"
        >
          {encerrado ? "reabrir o grupo" : "encerrar o grupo"}
        </button>
      </div>
      {confirmando && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (digitado.trim() === nome) mudar("encerrado", digitado.trim());
          }}
          className="mt-2 rounded border border-rose-200 bg-rose-50 p-3 text-rose-900"
        >
          <p>
            Encerrar os <strong>{info.eventos}</strong> eventos de <strong>{nome}</strong> libera
            as chaves para outro grupo ou evento. Folhas novas com essas chaves deixam de vir para
            cá. Nada é apagado, e dá para reabrir.
            {info.folhas - info.folhas_revisadas > 0 &&
              ` ${info.folhas - info.folhas_revisadas} folha(s) ainda não foram concluídas na revisão.`}
          </p>
          <label className="mt-2 block">
            Para confirmar, digite o nome do grupo: <strong>{nome}</strong>
            <input
              value={digitado}
              onChange={(e) => setDigitado(e.target.value)}
              autoFocus
              className="mt-1 block w-full rounded border border-rose-300 bg-white px-2 py-1.5 text-base text-slate-900 outline-none focus:border-rose-500"
            />
          </label>
          <div className="mt-2 flex gap-3">
            <button
              type="submit"
              disabled={ocupado || digitado.trim() !== nome}
              className="rounded bg-rose-600 px-3 py-1.5 text-white hover:bg-rose-700 disabled:opacity-40"
            >
              encerrar o grupo
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmando(false);
                setDigitado("");
              }}
              className="text-slate-600 underline"
            >
              cancelar
            </button>
          </div>
        </form>
      )}
      {aviso && (
        <p className="mt-1 text-right text-emerald-800">
          {aviso}
          {desfazer && (
            <button
              type="button"
              onClick={() => mudar("em andamento")}
              disabled={ocupado}
              className="ml-2 font-medium underline underline-offset-2 hover:text-emerald-950"
            >
              desfazer
            </button>
          )}
        </p>
      )}
    </div>
  );
}
