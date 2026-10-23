import { useEffect, useState } from "react";
import { ApiError, api, type NasConfig } from "../api";
import { shortModel } from "../lib/format";
import type { AuthMe, Health, LlmStatus } from "../types";
import { LogoutIcon, RefreshIcon, ShieldIcon } from "./icons";

type Props = {
  me: AuthMe | null;
  csrfToken: string | null;
  onLogout: () => void;
  onUnauthorized: () => void;
};

export function Settings({ me, csrfToken, onLogout, onUnauthorized }: Props) {
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [updating, setUpdating] = useState(false);

  // Configuración del NAS editable en caliente (la guarda el backend).
  const [nas, setNas] = useState<NasConfig | null>(null);
  const [maxMb, setMaxMb] = useState("");
  const [rate, setRate] = useState("");
  const [reglas, setReglas] = useState("");
  const [nasError, setNasError] = useState<string | null>(null);
  const [nasOk, setNasOk] = useState(false);
  const [guardandoNas, setGuardandoNas] = useState(false);

  function aplicarNas(config: NasConfig): void {
    setNas(config);
    setMaxMb(String(config.upload_max_mb));
    setRate(String(config.upload_rate_per_min));
    setReglas(
      config.classifier_rules
        ? JSON.stringify(config.classifier_rules, null, 2)
        : "",
    );
  }

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
    api
      .nasConfig()
      .then((config) => {
        if (!cancelled) aplicarNas(config);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
        else setNasError(err instanceof Error ? err.message : "Error");
      });
    return () => {
      cancelled = true;
    };
  }, [onUnauthorized]);

  /** Valida y guarda los ajustes del NAS; el backend vuelve a validar. */
  async function guardarNas(): Promise<void> {
    if (guardandoNas) return;
    setNasError(null);
    setNasOk(false);
    const maximo = Number(maxMb);
    const porMinuto = Number(rate);
    if (!Number.isInteger(maximo) || maximo < 1 || maximo > 500) {
      setNasError("El límite por archivo debe ser un entero entre 1 y 500 MB.");
      return;
    }
    if (!Number.isInteger(porMinuto) || porMinuto < 0 || porMinuto > 600) {
      setNasError(
        "Las subidas por minuto deben ser un entero entre 0 y 600 (0 = sin límite).",
      );
      return;
    }
    const textoReglas = reglas.trim();
    if (textoReglas) {
      try {
        JSON.parse(textoReglas);
      } catch (err) {
        setNasError(
          `Las reglas no son JSON válido: ${
            err instanceof Error ? err.message : "revisa la sintaxis"
          }`,
        );
        return;
      }
    }
    setGuardandoNas(true);
    try {
      const config = await api.updateNasConfig(
        {
          upload_max_mb: maximo,
          upload_rate_per_min: porMinuto,
          classifier_rules: textoReglas,
        },
        csrfToken,
      );
      aplicarNas(config);
      setNasOk(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      else setNasError(err instanceof Error ? err.message : "No se pudo guardar");
    } finally {
      setGuardandoNas(false);
    }
  }

  // Boton de rescate: borra Service Worker + caches y recarga desde la red.
  // Sirve cuando el iPhone conserva un bundle viejo atascado.
  async function refreshApp() {
    if (updating) return;
    setUpdating(true);
    try {
      if ("serviceWorker" in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map((registration) => registration.unregister()));
      }
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch {
      /* aunque falle la limpieza, recargamos */
    }
    window.location.reload();
  }

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
        </dl>
      </section>

      <section className="card">
        <header className="card-head">
          <h2>NAS</h2>
          <span className={`pill ${nasOk ? "ok" : ""}`}>
            {guardandoNas ? "guardando" : nasOk ? "guardado" : "editable"}
          </span>
        </header>
        {nasError && <p className="form-error">{nasError}</p>}
        {!nas && !nasError && <div className="skeleton line" />}
        {nas && (
          <form
            className="nas-config"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void guardarNas();
            }}
          >
            <label>
              <span>Ruta de entrada</span>
              <input value={nas.inbox} readOnly />
            </label>
            <p className="muted">
              Fija por el volumen del contenedor: no se cambia desde aquí.
            </p>
            <label>
              <span>Límite por archivo (MB)</span>
              <input
                type="number"
                min={1}
                max={500}
                inputMode="numeric"
                value={maxMb}
                onChange={(event) => {
                  setMaxMb(event.target.value);
                  setNasOk(false);
                }}
              />
            </label>
            <label>
              <span>Subidas por minuto (0 = sin límite)</span>
              <input
                type="number"
                min={0}
                max={600}
                inputMode="numeric"
                value={rate}
                onChange={(event) => {
                  setRate(event.target.value);
                  setNasOk(false);
                }}
              />
            </label>
            <p className="muted">
              Cuenta archivos, no peticiones: 100 fotos consumen 100. El chat
              manda lotes de 10 (o del tamaño del límite si es menor) y, si se
              pasa, espera y reintenta solo.
            </p>
            <details>
              <summary>Reglas de clasificación (JSON)</summary>
              <textarea
                value={reglas}
                spellCheck={false}
                onChange={(event) => {
                  setReglas(event.target.value);
                  setNasOk(false);
                }}
                placeholder='{"rules": [...]}'
              />
              <p className="muted">
                Vacío = reglas de fábrica (Fotos, Videos, Audio, Documentos).
                La extensión manda; el tipo MIME es el respaldo.
              </p>
            </details>
            <button className="btn primary" type="submit" disabled={guardandoNas}>
              {guardandoNas ? "Guardando…" : "Guardar ajustes del NAS"}
            </button>
            <p className="muted">
              Se aplica a la próxima subida, sin reiniciar el agente.
            </p>
          </form>
        )}
      </section>

      <section className="card">
        <header className="card-head">
          <h2>Aplicación</h2>
          <span className="pill">{updating ? "actualizando" : "al día"}</span>
        </header>
        <dl className="kv">
          <div>
            <dt>Build PWA</dt>
            <dd className="mono">{health?.static_asset ?? "—"}</dd>
          </div>
        </dl>
        <button
          className="btn"
          type="button"
          onClick={() => void refreshApp()}
          disabled={updating}
        >
          <RefreshIcon />
          {updating ? "Limpiando caché…" : "Buscar actualización"}
        </button>
        <p className="muted">
          Si la app se ve vieja o una pantalla no cambia, esto borra la caché
          local y vuelve a descargarla.
        </p>
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
