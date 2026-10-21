import { executionState, timeAgo } from "../lib/executions";
import type { Ejecucion } from "../types";

/**
 * Historial de ejecuciones del ejecutor.
 *
 * Vive en Aprobaciones porque es la consecuencia de lo que se aprueba: la
 * pestaña Equipos responde "¿están vivos los hosts?" y esta responde "¿qué se
 * ejecutó y cómo salió?".
 */
export function RecentActivity({ items }: { items: Ejecucion[] }) {
  return (
    <section aria-label="Actividad reciente del ejecutor">
      <div className="section-head">
        <h2>Actividad reciente</h2>
        <span className="muted">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="muted">Sin ejecuciones registradas todavía.</p>
      ) : (
        <ul className="exec-list">
          {items.map((item) => {
            const state = executionState(item);
            return (
              <li key={item.id} className="exec-item">
                <span className={`exec-dot ${state.cls}`} aria-hidden="true" />
                <div className="exec-body">
                  <div className="exec-title">
                    <span className="exec-op">{item.operation}</span>
                    <span className="exec-host">{item.host_id}</span>
                    <span className="exec-state">{state.label}</span>
                  </div>
                  {item.error && <div className="exec-error">{item.error}</div>}
                  {item.output_excerpt && !item.error && (
                    <pre className="exec-out">{item.output_excerpt.slice(0, 400)}</pre>
                  )}
                </div>
                <span className="exec-time">{timeAgo(item.created_at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
