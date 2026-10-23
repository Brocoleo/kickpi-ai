import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Aprobaciones } from "./components/Aprobaciones";
import { Chat } from "./components/Chat";
import { Equipos } from "./components/Equipos";
import { Files } from "./components/Files";
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
  // FileBrowser se monta una sola vez y se queda vivo (oculto) al cambiar de
  // pestaña: abrirlo deja de ser una recarga completa y no pierde la carpeta.
  const [filesReady, setFilesReady] = useState(false);

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

  // Precarga silenciosa: con la sesión ya resuelta, se espera un momento
  // para no competir con el primer turno del chat ni con las métricas.
  useEffect(() => {
    if (phase !== "auth" || filesReady) return;
    const timer = setTimeout(() => setFilesReady(true), 1500);
    return () => clearTimeout(timer);
  }, [phase, filesReady]);


  // Se monta desde el evento (no desde un efecto): abrir Archivos antes de
  // que termine la precarga lo carga al instante, y no hay un render extra.
  const handleTabChange = useCallback((next: Tab) => {
    setTab(next);
    if (next === "files") setFilesReady(true);
  }, []);

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

      <main
        className={`main ${tab === "chat" ? "chat-mode" : ""} ${
          tab === "files" ? "files-mode" : ""
        }`}
      >
        {tab === "chat" && (
          <Chat
            onUnauthorized={handleUnauthorized}
            csrfToken={csrfToken}
          />
        )}
        {tab === "equipos" && <Equipos onUnauthorized={handleUnauthorized} />}
        {filesReady && (
          <div className="tab-pane" hidden={tab !== "files"}>
            <Files csrfToken={csrfToken} onUnauthorized={handleUnauthorized} />
          </div>
        )}
        {tab === "approvals" && (
          <Aprobaciones
            csrfToken={csrfToken}
            onUnauthorized={handleUnauthorized}
            onPendingCount={setPendingCount}
          />
        )}
        {tab === "settings" && (
          <Settings
            me={me}
            csrfToken={csrfToken}
            onLogout={handleLogout}
            onUnauthorized={handleUnauthorized}
          />
        )}
      </main>

      <TabBar tab={tab} onChange={handleTabChange} pendingCount={pendingCount} />
    </div>
  );
}
