import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { RefreshIcon } from "./icons";
import type { Ejecucion, Equipo } from "../types";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `hace ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  return `hace ${Math.round(hours / 24)} d`;
}

function hostState(host: Equipo): { label: string; cls: string } {
  if (!host.enabled) return { label: "deshabilitado", cls: "off" };
  if (host.online === true) return { label: "en línea", cls: "ok" };
  if (host.online === false) return { label: "sin conexión", cls: "warn" };
  return { label: "consultando", cls: "off" };
}

function executionState(item: Ejecucion): { label: string; cls: string } {
  if (!item.allowed) return { label: "denegado", cls: "warn" };
  if (item.ok) return { label: "ok", cls: "ok" };
  return { label: `error ${item.exit_code ?? "?"}`, cls: "warn" };
}

function HostCard({ host }: { host: Equipo }) {
  const state = hostState(host);
  return (
    <div className="card host-card">
      <div className="host-head">
        <span className="host-name">{host.label}</span>
        <span className={`pill ${state.cls === "ok" ? "ok" : state.cls === "warn" ? "warn" : ""}`}>
          {state.label}
        </span>
      </div>
      <div className="host-meta">
        <span className="host-id">{host.id}</span>
        <span className="host-os">{host.os}</span>
      </div>
      {host.address && <div className="host-addr">{host.address}</div>}
      {host.kernel && <div className="host-kernel">{host.kernel}</div>}
      {!host.online && host.reason && <div className="host-reason">{host.reason}</div>}
      {host.roots.length > 0 && (
        <div className="host-roots">
          {host.roots.map((root) => (
            <span key={root} className="host-root">
              {root}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function Equipos() {
  const [hosts, setHosts] = useState<Equipo[]>([]);
  const [executions, setExecutions] = useState<Ejecucion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.equipos();
      setHosts(result.hosts ?? []);
      setExecutions(result.executions ?? []);
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo contactar con el servicio de equipos",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), 20_000);
    return () => clearInterval(interval);
  }, [load]);

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Equipos</h1>
        <button
          type="button"
          className="icon-btn"
          onClick={() => void load()}
          aria-label="Actualizar"
        >
          <RefreshIcon />
        </button>
      </div>

      {error && <div className="notice warn">{error}</div>}
      {loading && hosts.length === 0 && <div className="notice">Consultando equipos…</div>}

      {hosts.map((host) => (
        <HostCard key={host.id} host={host} />
      ))}

      <div className="card">
        <div className="card-head">
          <h2>Actividad reciente</h2>
          <span className="muted">{executions.length}</span>
        </div>
        {executions.length === 0 ? (
          <p className="muted">Sin ejecuciones registradas todavía.</p>
        ) : (
          <ul className="exec-list">
            {executions.map((item) => {
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
                  <span className="exec-time">{relativeTime(item.created_at)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
