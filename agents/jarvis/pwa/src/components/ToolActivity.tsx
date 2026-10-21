import { useState } from "react";
import { ChevronIcon } from "./icons";

/** Una herramienta ejecutada por el agente, en el historial o en vivo. */
export type ToolStep = {
  id: string;
  tool: string;
  ok: boolean;
  duration_ms?: number;
  summary?: string;
  /** Argumentos ya formateados (JSON legible), si los hubo. */
  args?: string;
};

function totalMs(steps: ToolStep[]): number | undefined {
  const tiempos = steps
    .map((step) => step.duration_ms)
    .filter((ms): ms is number => typeof ms === "number");
  if (tiempos.length === 0) return undefined;
  return tiempos.reduce((total, ms) => total + ms, 0);
}

function resumen(steps: ToolStep[]): string {
  const fallos = steps.filter((step) => !step.ok).length;
  const ms = totalMs(steps);
  const partes = [`${steps.length} ${steps.length === 1 ? "acción" : "acciones"}`];
  if (fallos > 0) partes.push(`${fallos} con error`);
  if (ms !== undefined) partes.push(`${ms} ms`);
  return partes.join(" · ");
}

/**
 * Actividad de herramientas agrupada.
 *
 * Antes era una fila de chips suelta: con seis llamadas seguidas el chat se
 * volvía ilegible y no se veía ni qué se consultó ni qué falló. Ahora es un
 * bloque plegable con el resultado de cada paso; se abre solo cuando hay
 * pocas acciones (las de una respuesta normal) y se pliega cuando hay ruido.
 */
export function ToolActivity({ steps }: { steps: ToolStep[] }) {
  const [abierto, setAbierto] = useState(steps.length <= 2);

  if (steps.length === 0) return null;
  const hayFallo = steps.some((step) => !step.ok);

  return (
    <div className={`tool-activity${hayFallo ? " has-fail" : ""}`}>
      <button
        type="button"
        className="tool-activity-head"
        aria-expanded={abierto}
        onClick={() => setAbierto((valor) => !valor)}
      >
        <ChevronIcon open={abierto} />
        <span className="tool-activity-title">
          {abierto ? "Ocultar actividad" : "Ver actividad"}
        </span>
        <span className="tool-activity-meta">{resumen(steps)}</span>
      </button>

      {abierto && (
        <ol className="tool-steps">
          {steps.map((step) => (
            <li key={step.id} className={`tool-step ${step.ok ? "ok" : "fail"}`}>
              <div className="tool-step-line">
                <span className="tool-dot" aria-hidden="true" />
                <code className="tool-name">{step.tool}</code>
                {step.duration_ms !== undefined && (
                  <span className="tool-ms">{step.duration_ms} ms</span>
                )}
                <span className="tool-result">{step.ok ? "ok" : "falló"}</span>
              </div>
              {step.summary && <p className="tool-summary">{step.summary}</p>}
              {step.args && step.args !== "{}" && (
                <details className="tool-args">
                  <summary>Parámetros</summary>
                  <pre>{step.args}</pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
