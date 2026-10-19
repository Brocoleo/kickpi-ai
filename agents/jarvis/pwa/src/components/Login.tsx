import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api";
import { EyeIcon, ShieldIcon } from "./icons";

type Props = {
  passwordConfigured: boolean;
  onSuccess: () => void;
};

export function Login({ passwordConfigured, onSuccess }: Props) {
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onSuccess();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 429 ? "Demasiados intentos. Espera unos minutos." : err.message);
      } else {
        setError("No se pudo conectar con Jarvis.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-glow" aria-hidden="true" />
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark" aria-hidden="true">
          <span />
        </div>
        <h1 className="login-title">Jarvis</h1>
        <p className="login-sub">Acceso privado a tu KICKPI</p>

        <label className="field">
          <span>Contraseña</span>
          <div className="field-input">
            <input
              type={show ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="••••••••••••"
              autoFocus
            />
            <button
              type="button"
              className="icon-btn"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? "Ocultar contraseña" : "Mostrar contraseña"}
            >
              <EyeIcon open={show} />
            </button>
          </div>
        </label>

        {error && <p className="form-error">{error}</p>}

        <button className="btn primary" type="submit" disabled={!password || busy}>
          {busy ? "Entrando…" : "Entrar"}
        </button>

        <p className="login-hint">
          <ShieldIcon />
          {passwordConfigured
            ? "Sesión segura por cookie HttpOnly. Solo accesible desde tu tailnet."
            : "Aún no hay contraseña propia: usa tu JARVIS_API_TOKEN como contraseña."}
        </p>
      </form>
    </div>
  );
}
