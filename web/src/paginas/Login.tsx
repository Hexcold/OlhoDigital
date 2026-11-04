import { useState } from "react";
import { api, ErroApi } from "../api";

const ENTRADA =
  "mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm " +
  "focus:border-marca-500 focus:outline-none focus:ring-1 focus:ring-marca-500";

/** Fora da casca do `App`: sem sessão não há menu nem usuário para mostrar. */
export default function Login() {
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState("");
  const [ocupado, setOcupado] = useState(false);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setOcupado(true);
    setErro("");
    try {
      await api.entrar(usuario, senha);
      window.location.assign("/");
    } catch (falha) {
      setErro(
        falha instanceof ErroApi
          ? falha.message
          : "não consegui falar com o servidor",
      );
      setOcupado(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 text-slate-900">
      <form
        onSubmit={entrar}
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-lg font-semibold tracking-tight text-marca-700">
          OlhoDigital
        </h1>
        <label className="mt-4 block text-sm">
          Usuário
          <input
            className={ENTRADA}
            value={usuario}
            autoComplete="username"
            autoFocus
            onChange={(e) => setUsuario(e.target.value)}
          />
        </label>
        <label className="mt-3 block text-sm">
          Senha
          <input
            type="password"
            className={ENTRADA}
            value={senha}
            autoComplete="current-password"
            onChange={(e) => setSenha(e.target.value)}
          />
        </label>
        {erro && <p className="mt-3 text-sm text-red-700">{erro}</p>}
        <button
          type="submit"
          disabled={ocupado || !usuario || !senha}
          className="mt-4 w-full rounded bg-marca-600 px-3 py-2 text-sm font-medium text-white hover:bg-marca-700 disabled:opacity-50"
        >
          {ocupado ? "entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
