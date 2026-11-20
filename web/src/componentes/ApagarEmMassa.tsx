import { useEffect, useState } from "react";
import { ErroApi } from "../api";
import type { PreviaApagar } from "../tipos";

/** Apagar várias folhas de uma vez: todas, ou as de um grupo de eventos (D24).
 *
 *  Três travas, cada uma contra um erro diferente:
 *  - a PRÉVIA diz quantas saem antes de qualquer coisa sair;
 *  - as folhas revisadas carregam a frequência apurada e só entram marcando a
 *    opção, como no apagar de uma folha só (`forcar`);
 *  - a pessoa DIGITA o número de folhas. Clique sem querer não digita "62", e
 *    o servidor confere o número: se chegou folha entre a prévia e o clique, a
 *    conta muda e nada é apagado.
 *
 *  Folha ainda na fila nunca sai — a leitura terminaria gravando numa folha que
 *  não existe mais. */
export function ApagarEmMassa({
  rotulo,
  previa,
  apagar,
  aoApagar,
}: {
  rotulo: string;
  previa: (incluirRevisadas: boolean) => Promise<PreviaApagar>;
  apagar: (confirmacao: number, incluirRevisadas: boolean) => Promise<PreviaApagar>;
  aoApagar: (feito: PreviaApagar) => void;
}) {
  const [aberto, setAberto] = useState(false);
  const [incluir, setIncluir] = useState(false);
  const [conta, setConta] = useState<PreviaApagar | null>(null);
  const [digitado, setDigitado] = useState("");
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);
  // muda a cada pedido de prévia nova: a resposta de uma prévia antiga não pode
  // sobrescrever a da opção marcada agora
  const [versao, setVersao] = useState(0);

  useEffect(() => {
    if (!aberto) return;
    let valendo = true;
    setConta(null);
    setDigitado("");
    previa(incluir)
      .then((c) => valendo && setConta(c))
      .catch((e) => valendo && setErro(e instanceof ErroApi ? e.message : String(e)));
    return () => {
      valendo = false;
    };
    // `previa` fica fora de propósito: muda de identidade a cada render de quem
    // chama, e recontar a cada render apagaria o número digitado
  }, [aberto, incluir, versao]);

  function fechar() {
    setAberto(false);
    setIncluir(false);
    setErro("");
  }

  async function confirmar() {
    if (!conta) return;
    setOcupado(true);
    setErro("");
    try {
      const feito = await apagar(Number(digitado), incluir);
      fechar();
      aoApagar(feito);
    } catch (e) {
      // 409: a conta mudou entre a prévia e o clique. Reconta para a pessoa ver
      // o número novo, em vez de repetir o pedido com o número velho.
      setErro(e instanceof ErroApi ? e.message : String(e));
      setVersao((v) => v + 1);
    } finally {
      setOcupado(false);
    }
  }

  if (!aberto) {
    return (
      <button
        type="button"
        onClick={() => setAberto(true)}
        className="text-xs text-slate-500 underline underline-offset-2 hover:text-rose-700"
      >
        {rotulo}
      </button>
    );
  }

  const n = conta?.apagar ?? 0;
  const pronto = conta !== null && n > 0 && digitado.trim() === String(n);

  return (
    <div className="mt-2 w-full rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-950">
      {conta === null ? (
        <p>contando as folhas…</p>
      ) : (
        <>
          <p>
            {n === 0 ? (
              "Nenhuma folha para apagar."
            ) : (
              <>
                <strong>{n}</strong> folha{n === 1 ? "" : "s"} ser
                {n === 1 ? "á apagada" : "ão apagadas"}, com as linhas, as revisões
                e as imagens. Os eventos ficam.
              </>
            )}
          </p>
          {(conta.revisadas_mantidas > 0 || conta.na_fila_mantidas > 0) && (
            <p className="mt-1 text-rose-900">
              Ficam:{" "}
              {[
                conta.revisadas_mantidas > 0 &&
                  `${conta.revisadas_mantidas} já revisada(s), com a frequência apurada`,
                conta.na_fila_mantidas > 0 &&
                  `${conta.na_fila_mantidas} ainda sendo lida(s)`,
              ]
                .filter(Boolean)
                .join(" · ")}
              .
            </p>
          )}
        </>
      )}

      <label className="mt-2 flex items-center gap-2">
        <input
          type="checkbox"
          checked={incluir}
          onChange={(e) => setIncluir(e.target.checked)}
        />
        incluir as já revisadas
      </label>

      {n > 0 && (
        <label className="mt-2 block">
          Para confirmar, digite <strong>{n}</strong>:
          <input
            value={digitado}
            onChange={(e) => setDigitado(e.target.value)}
            inputMode="numeric"
            className="ml-2 w-20 rounded border border-rose-300 bg-white px-2 py-1 text-base tabular-nums"
          />
        </label>
      )}

      {erro && <p className="mt-2 text-rose-800">{erro}</p>}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={!pronto || ocupado}
          onClick={confirmar}
          className="rounded bg-rose-700 px-3 py-1.5 font-medium text-white hover:bg-rose-800 disabled:cursor-not-allowed disabled:bg-rose-300"
        >
          {ocupado ? "apagando…" : `apagar ${n} folha${n === 1 ? "" : "s"}`}
        </button>
        <button
          type="button"
          onClick={fechar}
          className="rounded px-3 py-1.5 text-rose-900 hover:bg-rose-100"
        >
          cancelar
        </button>
      </div>
    </div>
  );
}
