import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Chat } from "./components/Chat";
import { Files } from "./components/Files";
import { Metrics } from "./components/Metrics";
import { Settings } from "./components/Settings";
import { TabBar, type Tab } from "./components/TabBar";
import { Login } from "./components/Login";
import type { AuthMe, Health } from "./types";

type Phase = "loading" | "anon" | "auth";

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

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((result) => {
        if (cancelled) return;
        setMe(result);
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

  const handleLogin = useCallback(async () => {
    const result = await api.me();
    setMe(result);
    setPhase("auth");
  }, []);

  const handleUnauthorized = useCallback(() => {
    setMe(null);
    setPhase("anon");
    setTab("chat");
  }, []);

  const handleLogout = useCallback(() => {
    setMe(null);
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
        {tab === "files" && <Files />}
        {tab === "settings" && (
          <Settings me={me} onLogout={handleLogout} onUnauthorized={handleUnauthorized} />
        )}
      </main>

      <TabBar tab={tab} onChange={setTab} />
    </div>
  );
}
