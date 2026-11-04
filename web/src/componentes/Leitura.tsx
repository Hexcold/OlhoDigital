import type { Motivo } from "../tipos";

/** Faixas de confiança. Portadas do `quarentena.py`, que é a especificação
 *  desta tela — não são estética, são a única pista que o revisor tem de ONDE
 *  a leitura é frágil. `CORTE_CNN` vale 0,95, então tudo abaixo disso já teria
 *  virado `?` no modo de abstenção. */
function corDoCaractere(confianca: number, motivo: Motivo): string {
  if (motivo !== "lido") return "text-slate-300";
  if (confianca >= 0.95) return "text-slate-900";
  if (confianca >= 0.7) return "text-amber-600";
  return "text-rose-600 font-bold";
}

/** A leitura de um campo, com cada caractere pintado pela confiança da célula
 *  que o produziu. */
export function Leitura({
  texto,
  confiancas,
  motivos,
}: {
  texto: string;
  confiancas: number[];
  motivos: Motivo[];
}) {
  if (!texto) return <span className="text-slate-400">—</span>;
  return (
    <span className="font-mono text-base tracking-wide break-all">
      {Array.from(texto).map((ch, i) => (
        <span
          key={i}
          className={corDoCaractere(confiancas[i] ?? 0, motivos[i] ?? "lido")}
          title={
            motivos[i] && motivos[i] !== "lido"
              ? motivos[i]
              : `confiança ${(confiancas[i] ?? 0).toFixed(3)}`
          }
        >
          {ch === " " ? "·" : ch}
        </span>
      ))}
    </span>
  );
}
