import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { shortModel } from "../lib/format";
import type { AuthMe, Health, LlmStatus } from "../types";
import { LogoutIcon, ShieldIcon } from "./icons";

type Props = {
  me: AuthMe | null;
  onLogout: () => void;
  onUnauthorized: () => void;
};

export function Settings({ me, onLogout, onUnauthorized }: Props) {
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .llmStatus()
      .then((status) => {
        if (!cancelled) setLlm(status);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
        else setError(err instanceof Error ? err.message : "Error");
      });
    api
      .health()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch(() => {
        /* el build se muestra como "—" si /health no responde */
      });
    return () => {
      cancelled = true;
    };
  }, [onUnauthorized]);

  async function logout() {
    if (busy) return;
    setBusy(true);
    try {
      await api.logout();
    } catch {
      /* la sesión local se limpia igualmente */
    } finally {
      setBusy(false);
      onLogout();
    }
  }

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Ajustes</h1>
        <span className="mono muted">v{me?.version ?? "—"}</span>
      </div>

      <section className="card">
        <header className="card-head">
          <h2>Sesión</h2>
          <span className="pill ok">activa</span>
        </header>
        <dl className="kv">
          <div>
            <dt>Método</dt>
            <dd>{me?.method === "session" ? "Cookie HttpOnly" : "Token de API"}</dd>
          </div>
          <div>
            <dt>Duración</dt>
            <dd>{me?.session_ttl_days ?? "—"} días</dd>
          </div>
          <div>
            <dt>Contraseña propia</dt>
            <dd>{me?.password_configured ? "configurada" : "no (usa el token)"}</dd>
          </div>
          <div>
            <dt>Build PWA</dt>
            <dd className="mono">{health?.static_asset ?? "—"}</dd>
          </div>
        </dl>
      </section>

      <section className="card">
        <header className="card-head">
          <h2>Modelos</h2>
        </header>
        {error && <p className="form-error">{error}</p>}
        {!llm && !error && <div className="skeleton line" />}
        {llm && (
          <ol className="model-chain">
            {llm.models.map((model, index) => {
              const circuit = llm.circuit_breaker[model];
              return (
                <li key={model}>
                  <span className="model-index">{index + 1}</span>
                  <span className="model-name" title={model}>
                    {shortModel(model)}
                  </span>
                  {circuit?.open ? (
                    <span className="pill warn">enfriando {Math.round(circuit.reopens_in_s)}s</span>
                  ) : (
                    <span className={`pill ${index === 0 ? "ok" : ""}`}>
                      {index === 0 ? "principal" : "reserva"}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {llm && !llm.configured && (
          <p className="muted">Sin clave de LLM: el chat responderá con un aviso.</p>
        )}
      </section>

      <section className="card">
        <header className="card-head">
          <h2>Instalar en iPhone</h2>
        </header>
        <ol className="steps">
          <li>Abre esta URL en Safari.</li>
          <li>Toca el botón Compartir.</li>
          <li>Elige «Añadir a pantalla de inicio».</li>
        </ol>
        <p className="muted">
          Solo funciona dentro de tu tailnet. Nunca se expone a Internet.
        </p>
      </section>

      <button className="btn danger" type="button" onClick={() => void logout()} disabled={busy}>
        <LogoutIcon />
        {busy ? "Cerrando…" : "Cerrar sesión"}
      </button>

      <p className="footnote">
        <ShieldIcon />
        Sesión en cookie HttpOnly · SameSite=Strict · el token nunca toca
        localStorage.
      </p>
    </div>
  );
}
