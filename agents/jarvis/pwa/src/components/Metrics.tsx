import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { bytes, percent, relativeTime, uptime } from "../lib/format";
import type { ContainerInfo, Containers, Disks, SystemStats } from "../types";
import { RefreshIcon } from "./icons";

type Props = { onUnauthorized: () => void };

type History = { cpu: number[]; mem: number[]; temp: number[] };

const REFRESH_MS = 10_000;
const POINTS = 30;

function Spark({ data, max = 100 }: { data: number[]; max?: number }) {
  if (data.length < 2) return <div className="spark" />;
  const width = 100;
  const height = 26;
  const top = Math.max(max, ...data, 1);
  const points = data
    .map((value, index) => {
      const x = (index / (data.length - 1)) * width;
      const y = height - (value / top) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={points} />
    </svg>
  );
}

function tone(container: ContainerInfo): string {
  if (container.state !== "running") return "off";
  if (container.health === "unhealthy") return "warn";
  return "ok";
}

export function Metrics({ onUnauthorized }: Props) {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [disks, setDisks] = useState<Disks | null>(null);
  const [containers, setContainers] = useState<Containers | null>(null);
  const [history, setHistory] = useState<History>({ cpu: [], mem: [], temp: [] });
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const busy = useRef(false);
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    try {
      const [statsResult, disksResult, containersResult] = await Promise.all([
        api.systemStats(),
        api.disks(),
        api.containers(),
      ]);
      setStats(statsResult);
      setDisks(disksResult);
      setContainers(containersResult);
      setLastUpdated(new Date());
      setError(null);
      setHistory((prev) => ({
        cpu: [...prev.cpu, statsResult.cpu.percent_total].slice(-POINTS),
        mem: [...prev.mem, statsResult.memory.percent].slice(-POINTS),
        temp:
          statsResult.cpu.temperature_c == null
            ? prev.temp
            : [...prev.temp, statsResult.cpu.temperature_c].slice(-POINTS),
      }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Error al consultar métricas");
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }, [onUnauthorized]);

  useEffect(() => {
    void load();
    const refresh = setInterval(() => void load(), REFRESH_MS);
    const ticker = setInterval(() => setTick((value) => value + 1), 5_000);
    return () => {
      clearInterval(refresh);
      clearInterval(ticker);
    };
  }, [load]);

  const temperature = stats?.cpu.temperature_c;

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Métricas</h1>
        <div className="head-meta">
          <span className="updated">{relativeTime(lastUpdated)}</span>
          <button
            type="button"
            className={`icon-btn ${loading ? "spin" : ""}`}
            onClick={() => void load()}
            aria-label="Actualizar métricas"
          >
            <RefreshIcon />
          </button>
        </div>
      </div>

      {error && (
        <div className="card error-card">
          <strong>No pude leer las métricas</strong>
          <p>{error}</p>
          <button className="btn small" type="button" onClick={() => void load()}>
            Reintentar
          </button>
        </div>
      )}

      {!stats && !error && <div className="card skeleton tall" />}

      {stats && (
        <>
          <section className="stats-grid">
            <article className="card stat">
              <span className="stat-label">CPU</span>
              <span className="stat-value">{percent(stats.cpu.percent_total)}</span>
              <Spark data={history.cpu} />
            </article>
            <article className="card stat">
              <span className="stat-label">RAM</span>
              <span className="stat-value">{percent(stats.memory.percent)}</span>
              <Spark data={history.mem} />
            </article>
            <article className="card stat">
              <span className="stat-label">Temp.</span>
              <span className="stat-value">
                {temperature == null ? "—" : `${temperature.toFixed(1)}°`}
              </span>
              <Spark data={history.temp} max={Math.max(80, ...history.temp)} />
            </article>
          </section>

          <section className="card">
            <header className="card-head">
              <h2>Memoria</h2>
              <span className="mono">{bytes(stats.memory.total_bytes)}</span>
            </header>
            <div className="bar-row">
              <span className="bar-label">RAM</span>
              <div className="bar">
                <i style={{ width: `${Math.min(100, stats.memory.percent)}%` }} />
              </div>
              <span className="mono">
                {bytes(stats.memory.used_bytes)} / {bytes(stats.memory.total_bytes)}
              </span>
            </div>
            {stats.swap.total_bytes > 0 && (
              <div className="bar-row">
                <span className="bar-label">Swap</span>
                <div className="bar">
                  <i style={{ width: `${Math.min(100, stats.swap.percent)}%` }} />
                </div>
                <span className="mono">{percent(stats.swap.percent)}</span>
              </div>
            )}
          </section>

          <section className="card">
            <header className="card-head">
              <h2>Discos</h2>
            </header>
            {(disks?.disks ?? []).map((disk) => (
              <div className="bar-row" key={disk.mount}>
                <span className="bar-label mono">{disk.mount}</span>
                <div className="bar">
                  <i
                    className={disk.percent > 85 ? "danger" : undefined}
                    style={{ width: `${Math.min(100, disk.percent)}%` }}
                  />
                </div>
                <span className="mono">
                  {bytes(disk.free_bytes)} libres
                </span>
              </div>
            ))}
            {(disks?.disks ?? []).length === 0 && (
              <p className="muted">
                Sin discos monitorizados. Conecta el disco USB para montar
                <code>/mnt/data</code>.
              </p>
            )}
          </section>

          <section className="card">
            <header className="card-head">
              <h2>Sistema</h2>
              <span className="mono">up {uptime(stats.uptime_seconds)}</span>
            </header>
            <dl className="kv">
              <div>
                <dt>Placa</dt>
                <dd>{stats.cpu.model ?? "—"}</dd>
              </div>
              <div>
                <dt>Núcleos</dt>
                <dd>
                  {stats.cpu.physical_cores} físicos · {stats.cpu.cores} lógicos
                </dd>
              </div>
              <div>
                <dt>Carga</dt>
                <dd className="mono">{stats.cpu.load_avg.join(" · ")}</dd>
              </div>
              <div>
                <dt>Por núcleo</dt>
                <dd className="mono">
                  {stats.cpu.percent_per_core.map((value) => `${value.toFixed(0)}%`).join(" · ")}
                </dd>
              </div>
            </dl>
          </section>

          <section className="card">
            <header className="card-head">
              <h2>Contenedores</h2>
              <span className="mono">{containers?.count ?? 0}</span>
            </header>
            <ul className="containers">
              {(containers?.containers ?? []).map((container) => (
                <li key={container.name}>
                  <span className={`dot ${tone(container)}`} aria-hidden="true" />
                  <div className="container-main">
                    <div className="container-name">{container.name}</div>
                    <div className="container-image">{container.image}</div>
                    {container.ports.length > 0 && (
                      <div className="container-ports">
                        {container.ports.map((port) => (
                          <span className="chip tiny" key={port}>
                            {port}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className="container-state">
                    {container.health === "unhealthy"
                      ? "unhealthy"
                      : container.state}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
