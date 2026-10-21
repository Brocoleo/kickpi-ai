import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { RefreshIcon } from "./icons";
import { Metrics } from "./Metrics";
import type { Equipo } from "../types";

function hostState(host: Equipo): { label: string; cls: string } {
  if (!host.enabled) return { label: "deshabilitado", cls: "off" };
  if (host.online === true) return { label: "en línea", cls: "ok" };
  if (host.online === false) return { label: "sin conexión", cls: "warn" };
  return { label: "consultando", cls: "off" };
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

export function Equipos({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [hosts, setHosts] = useState<Equipo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.equipos();
      setHosts(result.hosts ?? []);
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

      <section className="equipos-metrics" aria-label="Recursos del servidor">
        <h2 className="section-title">Recursos del servidor</h2>
        <Metrics onUnauthorized={onUnauthorized} embedded />
      </section>
    </div>
  );
}
