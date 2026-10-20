import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Aprobaciones } from "./components/Aprobaciones";
import { Chat } from "./components/Chat";
import { Equipos } from "./components/Equipos";
import { Files } from "./components/Files";
import { Metrics } from "./components/Metrics";
import { Settings } from "./components/Settings";
import { TabBar, type Tab } from "./components/TabBar";
import { Login } from "./components/Login";
import type { AuthMe, Health } from "./types";

type Phase = "loading" | "anon" | "auth";

const REFRESH_MS = 10_000;

function Splash() {
  return (
    <div className="splash">
      <div className="orb" aria-hidden="true" />
      <span className="brand-word">JARVIS</span>
    </div>
  );
}

export default function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [me, setMe] = useState<AuthMe | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [health, setHealth] = useState<Health | null>(null);
  // Token CSRF: SOLO en memoria. Nunca en localStorage ni en una cookie
  // legible por JavaScript.
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  // Contador de solicitudes pendientes (badge de la pestaña).
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((result) => {
        if (cancelled) return;
        setMe(result);
        setCsrfToken(result.csrf_token ?? null);
        setPhase(result.authenticated ? "auth" : "anon");
      })
      .catch(() => {
        if (!cancelled) setPhase("anon");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (phase !== "auth") return;
    let cancelled = false;

    const check = async () => {
      try {
        const result = await api.health();
        if (!cancelled) setHealth(result);
      } catch {
        if (!cancelled) setHealth(null);
      }
    };

    void check();
    const interval = setInterval(() => void check(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [phase]);

  // Contador de pendientes para el badge. Cuando la pestaña de aprobaciones
  // esta abierta, es ella quien informa (una sola fuente de verdad).
  useEffect(() => {
    if (phase !== "auth" || tab === "approvals") return;
    let cancelled = false;

    const check = async () => {
      try {
        const data = await api.approvals("pending", 100);
        if (!cancelled) setPendingCount(data.approvals?.length ?? 0);
      } catch {
        /* el badge no es critico: si falla, se conserva el ultimo valor */
      }
    };

    void check();
    const interval = setInterval(() => void check(), REFRESH_MS);
    const onFocus = () => void check();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [phase, tab]);

  const handleLogin = useCallback(async () => {
    const result = await api.me();
    setMe(result);
    setCsrfToken(result.csrf_token ?? null);
    setPhase("auth");
  }, []);

  const handleUnauthorized = useCallback(() => {
    setMe(null);
    setCsrfToken(null);
    setPendingCount(0);
    setPhase("anon");
    setTab("chat");
  }, []);

  const handleLogout = useCallback(() => {
    setMe(null);
    setCsrfToken(null);
    setPendingCount(0);
    setPhase("anon");
    setTab("chat");
  }, []);

  if (phase === "loading") return <Splash />;

  if (phase === "anon") {
    return (
      <Login
        passwordConfigured={me?.password_configured ?? true}
        onSuccess={() => void handleLogin()}
      />
    );
  }

  const online = health?.tools_reachable === true;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-dot" aria-hidden="true" />
          <span className="brand-word">JARVIS</span>
        </div>
        <div className="topmeta">
          <span className={`pill ${online ? "ok" : "warn"}`}>
            {health ? (online ? "en línea" : "degradado") : "conectando"}
          </span>
        </div>
      </header>

      <main className={`main ${tab === "chat" ? "chat-mode" : ""}`}>
        {tab === "chat" && <Chat onUnauthorized={handleUnauthorized} />}
        {tab === "metrics" && <Metrics onUnauthorized={handleUnauthorized} />}
        {tab === "equipos" && <Equipos />}
        {tab === "files" && <Files />}
        {tab === "approvals" && (
          <Aprobaciones
            csrfToken={csrfToken}
            onUnauthorized={handleUnauthorized}
            onPendingCount={setPendingCount}
          />
        )}
        {tab === "settings" && (
          <Settings me={me} onLogout={handleLogout} onUnauthorized={handleUnauthorized} />
        )}
      </main>

      <TabBar tab={tab} onChange={setTab} pendingCount={pendingCount} />
    </div>
  );
}
