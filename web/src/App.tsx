import { useEffect, useState } from "react";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";
import { api } from "./api";
import type { Sessao } from "./tipos";

/** Casca da aplicação: cabeçalho azul, conteúdo branco.
 *
 *  Mobile-first — o revisor pode estar com o celular ao lado da pilha de
 *  folhas, e a tela de revisão é a que mais precisa disso. */
export default function App() {
  const { pathname } = useLocation();
  const naRevisao = pathname.startsWith("/folhas/");
  const [eu, setEu] = useState<Sessao | null>(null);

  // Sem sessão a resposta é 401, e `api.ts` manda para o login.
  useEffect(() => {
    api
      .eu()
      .then(setEu)
      .catch(() => {});
  }, []);

  if (!eu) return null;

  // O bloqueio de verdade é do servidor. Aqui é só para a pessoa não ver telas
  // que responderiam 403.
  const soImpressao = eu.papel === "impressao";
  if (soImpressao && pathname !== "/impressao") {
    return <Navigate to="/impressao" replace />;
  }
  const abas = soImpressao
    ? []
    : [
        { para: "/", texto: "folhas" },
        { para: "/eventos", texto: "eventos" },
        { para: "/impressao", texto: "impressão" },
      ];

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 bg-marca-700 text-white shadow-sm">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <Link to="/" className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight">
              OlhoDigital
            </span>
            <span className="hidden text-xs text-marca-200 sm:inline">
              frequência a partir de listas manuscritas
            </span>
          </Link>
          {naRevisao ? (
            <Link
              to="/"
              className="ml-auto rounded px-2 py-1 text-sm text-marca-100 underline-offset-2 hover:bg-marca-600 hover:text-white"
            >
              ← folhas
            </Link>
          ) : (
            <nav className="ml-auto flex items-center gap-1 text-sm">
              {abas.map(({ para, texto }) => (
                <Link
                  key={para}
                  to={para}
                  className={`rounded px-2 py-1 ${
                    pathname === para
                      ? "bg-marca-600 text-white"
                      : "text-marca-100 hover:bg-marca-600 hover:text-white"
                  }`}
                >
                  {texto}
                </Link>
              ))}
              {eu.login && (
                <button
                  type="button"
                  onClick={() =>
                    api.sair().finally(() => window.location.assign("/login"))
                  }
                  className="rounded px-2 py-1 text-marca-100 hover:bg-marca-600 hover:text-white"
                >
                  sair ({eu.usuario})
                </button>
              )}
            </nav>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet context={eu} />
      </main>

      <footer className="mx-auto max-w-5xl px-4 pb-10 pt-4 text-xs text-slate-500">
        Ferramenta de apoio. <strong>Não é sistema institucional</strong> — a
        frequência apurada aqui não é registrada no SI3/SIGAA.
      </footer>
    </div>
  );
}
