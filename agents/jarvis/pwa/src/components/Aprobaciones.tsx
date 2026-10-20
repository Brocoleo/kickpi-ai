/**
 * Pestaña "Aprobaciones" (Fase C).
 *
 * Permite consultar, aprobar, rechazar y ver el resultado de las
 * solicitudes que Jarvis necesita. Todo ocurre con la sesión humana
 * autenticada: el token CSRF viaja en una cabecera y vive solo en memoria.
 *
 * Decisiones de seguridad que esta pantalla respeta:
 * - no se puede modificar host, operacion, rutas ni argumentos: son datos
 *   que LLEGAN del backend y se muestran tal cual;
 * - aprobar exige una confirmacion en dos pasos que muestra los datos
 *   exactos de la solicitud;
 * - una solicitud `failed` (resultado incierto) NO se puede reintentar;
 * - una solicitud `consumed` no se vuelve a ejecutar: se informa.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import {
  canApprove,
  commandLine,
  formatCountdown,
  formatDuration,
  isConsumed,
  isExpiring,
  isOpen,
  isUncertain,
  paramsText,
  secondsLeft,
  statusMeta,
  truncateOutput,
} from "../lib/approvals";
import type { Approval } from "../types";
import { ApprovalDialog, type ConfirmKind } from "./ApprovalDialog";
import { CheckIcon, ChevronIcon, RefreshIcon, ShieldCheckIcon, XIcon } from "./icons";

type Props = {
  /** Token CSRF ligado a la sesion (solo en memoria). */
  csrfToken: string | null;
  onUnauthorized: () => void;
  /** Informa del numero de pendientes (badge de la pestaña). */
  onPendingCount?: (count: number) => void;
};

type ActionResult = {
  id: string;
  ok: boolean;
  message: string;
  stdout?: string;
  exitCode?: number | null;
  durationMs?: number | null;
  error?: string | null;
};

const REFRESH_MS = 10_000;

function ApprovalCard({
  approval,
  now,
  busy,
  result,
  expanded,
  onApprove,
  onReject,
  onToggleExpand,
}: {
  approval: Approval;
  now: number;
  busy: boolean;
  result: ActionResult | null;
  expanded: boolean;
  onApprove: () => void;
  onReject: () => void;
  onToggleExpand: () => void;
}) {
  const meta = statusMeta(approval.status);
  const left = secondsLeft(approval.expires_at, now);
  const actionable = canApprove(approval, now);
  const expiring = isExpiring(approval, now);
  const pending = approval.status === "pending";
  const output = truncateOutput(result?.stdout ?? "", 8);

  return (
    <article className="card approval-card" aria-label={`Solicitud ${approval.operation}`}>
      <div className="approval-head">
        <div className="approval-title">
          <span className="approval-op">{approval.operation}</span>
          <span className="approval-host">{approval.host}</span>
        </div>
        <div className="approval-head-right">
          <span className={`pill ${meta.cls}`}>{meta.label}</span>
          {pending && (
            <span
              className={`countdown ${expiring ? "warn" : ""}`}
              role="timer"
              aria-label={`Caduca en ${formatCountdown(left)}`}
            >
              {formatCountdown(left)}
            </span>
          )}
        </div>
      </div>

      <p className="approval-desc">
        {approval.risk_description ?? "Sin descripción."}
      </p>

      <dl className="approval-facts">
        <div>
          <dt>Riesgo</dt>
          <dd>{approval.risk ?? "—"}</dd>
        </div>
        <div>
          <dt>Proyecto</dt>
          <dd>{approval.project_id ?? "—"}</dd>
        </div>
        <div>
          <dt>Solicitada por</dt>
          <dd>{approval.requested_by}</dd>
        </div>
        {approval.approved_by && (
          <div>
            <dt>Aprobada por</dt>
            <dd>{approval.approved_by}</dd>
          </div>
        )}
      </dl>

      <div className="approval-cmd">
        <span className="approval-cmd-label">Comando exacto</span>
        <code className="approval-cmd-value">{commandLine(approval)}</code>
        <span className="approval-params">{paramsText(approval)}</span>
      </div>

      {result && result.id === approval.id && (
        <div className={`approval-result ${result.ok ? "ok" : "warn"}`}>
          <div className="approval-result-head">{result.message}</div>
          {result.exitCode !== undefined && (
            <div className="approval-result-meta">
              exit {result.exitCode ?? "—"} · {formatDuration(result.durationMs)}
            </div>
          )}
          {result.error && <div className="approval-result-error">{result.error}</div>}
          {result.stdout && (
            <>
              <pre className="exec-out approval-out">
                {expanded ? result.stdout : output.text}
              </pre>
              {output.truncated && (
                <button
                  type="button"
                  className="ghost-btn"
                  onClick={onToggleExpand}
                  aria-expanded={expanded}
                >
                  <ChevronIcon open={expanded} />
                  {expanded
                    ? "Ocultar salida"
                    : `Ver todo (${output.hiddenLines} líneas más)`}
                </button>
              )}
            </>
          )}
        </div>
      )}

      {isUncertain(approval) && (
        <p className="approval-uncertain" role="note">
          Resultado incierto: la solicitud no se reintenta. Revisa el equipo
          antes de pedir la operación de nuevo.
        </p>
      )}

      {isConsumed(approval) && (
        <p className="approval-consumed">
          Ya ejecutada {approval.executed_at ? `· ${approval.executed_at}` : ""}
          {approval.exit_code !== null ? ` · exit ${approval.exit_code}` : ""}
          {approval.duration_ms ? ` · ${formatDuration(approval.duration_ms)}` : ""}
        </p>
      )}

      {approval.result_error && !isUncertain(approval) && (
        <p className="approval-result-error">{approval.result_error}</p>
      )}

      {pending && (
        <div className="approval-actions">
          <button
            type="button"
            className="btn primary"
            onClick={onApprove}
            disabled={busy || !actionable}
            aria-label={
              actionable
                ? `Aprobar y ejecutar ${approval.operation} en ${approval.host}`
                : `No se puede aprobar: caduca en menos de 10 segundos`
            }
          >
            <CheckIcon />
            Aprobar
          </button>
          <button
            type="button"
            className="btn"
            onClick={onReject}
            disabled={busy}
            aria-label={`Rechazar ${approval.operation} en ${approval.host}`}
          >
            <XIcon />
            Rechazar
          </button>
        </div>
      )}

      {pending && !actionable && (
        <p className="approval-expired-note">
          Quedan menos de 10 segundos: recarga para ver el estado real.
        </p>
      )}
    </article>
  );
}

export function Aprobaciones({ csrfToken, onUnauthorized, onPendingCount }: Props) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [confirm, setConfirm] = useState<{
    approval: Approval;
    kind: ConfirmKind;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);
  // Aviso corto para lectores de pantalla (no duplica el detalle de la
  // tarjeta, que se lee al navegar).
  const [announcement, setAnnouncement] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const data = await api.approvals(undefined, 100);
        const items = data.approvals ?? [];
        setApprovals(items);
        // El badge de la pestaña se alimenta desde aqui cuando esta abierta.
        onPendingCount?.(items.filter((a) => a.status === "pending").length);
        setListError(null);
        setOffline(false);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onUnauthorized();
          return;
        }
        setListError(
          err instanceof ApiError
            ? err.message
            : "No se pudo contactar con el servicio de aprobaciones",
        );
        setOffline(true);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [onUnauthorized, onPendingCount],
  );

  // Refresco cada 10 s y al volver a enfocar la app.
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(true), REFRESH_MS);
    const onFocus = () => void load(true);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  // Cuenta atrás: un tic por segundo.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  const handleActionError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError) {
        switch (err.status) {
          case 401:
            onUnauthorized();
            return;
          case 403:
            setActionError(
              "Permiso denegado (403): la sesión o el token de seguridad ya no son válidos. Vuelve a iniciar sesión.",
            );
            return;
          case 409:
            setActionError(`No se pudo completar (409): ${err.message}`);
            return;
          case 422:
            setActionError(`Petición no válida (422): ${err.message}`);
            return;
          case 429:
            setActionError("Demasiadas solicitudes pendientes. Espera un momento.");
            return;
          case 503:
            setActionError(
              "El servicio de aprobaciones no está configurado en el servidor.",
            );
            return;
          default:
            setActionError(err.message);
            return;
        }
      }
      setActionError("No se pudo contactar con Jarvis. Revisa la conexión.");
    },
    [onUnauthorized],
  );

  const runAction = useCallback(async () => {
    if (!confirm || busy) return;
    const { approval, kind } = confirm;
    setBusy(true);
    setActionError(null);
    try {
      if (kind === "approve") {
        await api.approve(approval.id, csrfToken);
        const execution = await api.execute(approval.id, csrfToken);
        const consumed = execution.state === "consumed";
        setResult({
          id: approval.id,
          ok: consumed && execution.ok === true,
          message: consumed
            ? `Aprobada y ejecutada: ${approval.operation} en ${approval.host}`
            : `Aprobada, pero la ejecución quedó en estado «${execution.state}»`,
          stdout: execution.stdout,
          exitCode: execution.exit_code,
          durationMs: execution.duration_ms,
          error: execution.error,
        });
        setAnnouncement(
          consumed
            ? "Solicitud aprobada y ejecutada correctamente."
            : `Solicitud aprobada; la ejecución quedó en estado ${execution.state}.`,
        );
      } else {
        await api.reject(approval.id, csrfToken, "");
        setResult({
          id: approval.id,
          ok: true,
          message: `Rechazada: ${approval.operation} en ${approval.host}`,
        });
        setAnnouncement("Solicitud rechazada.");
      }
      setConfirm(null);
      await load(true);
    } catch (err) {
      handleActionError(err);
      if (err instanceof ApiError && (err.status === 409 || err.status === 401)) {
        await load(true);
      }
    } finally {
      setBusy(false);
    }
  }, [confirm, busy, csrfToken, load, handleActionError]);

  const open = useMemo(() => approvals.filter(isOpen), [approvals]);
  const history = useMemo(() => approvals.filter((a) => !isOpen(a)), [approvals]);
  const pendingCount = useMemo(
    () => approvals.filter((a) => a.status === "pending").length,
    [approvals],
  );

  const openConfirm = useCallback((approval: Approval, kind: ConfirmKind) => {
    setActionError(null);
    setConfirm({ approval, kind });
  }, []);

  const closeConfirm = useCallback(() => {
    if (busy) return;
    setConfirm(null);
    setActionError(null);
  }, [busy]);

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Aprobaciones</h1>
        <div className="head-meta">
          {pendingCount > 0 && (
            <span className="pill pending">
              {pendingCount} pendiente{pendingCount === 1 ? "" : "s"}
            </span>
          )}
          <button
            type="button"
            className="icon-btn"
            onClick={() => void load()}
            aria-label="Actualizar solicitudes"
          >
            <RefreshIcon />
          </button>
        </div>
      </div>

      <p className="status-announcer" role="status" aria-live="polite">
        {announcement}
      </p>

      {listError && (
        <div className={`notice ${offline ? "warn" : ""}`} role="alert">
          {listError}
          {offline && " · reintentando automáticamente"}
        </div>
      )}

      {loading && approvals.length === 0 && (
        <div className="notice">Consultando solicitudes…</div>
      )}

      {!loading && approvals.length === 0 && !listError && (
        <div className="card empty-state">
          <span className="empty-mark" aria-hidden="true">
            <ShieldCheckIcon />
          </span>
          <p className="muted">
            No hay solicitudes. Cuando Jarvis necesite tu permiso para una
            operación, aparecerá aquí.
          </p>
        </div>
      )}

      {open.length > 0 && (
        <section aria-label="Solicitudes pendientes">
          <div className="section-head">
            <h2>Pendientes</h2>
            <span className="muted">{open.length}</span>
          </div>
          <div className="approval-list">
            {open.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                now={now}
                busy={busy}
                result={result}
                expanded={Boolean(expanded[approval.id])}
                onApprove={() => openConfirm(approval, "approve")}
                onReject={() => openConfirm(approval, "reject")}
                onToggleExpand={() =>
                  setExpanded((prev) => ({
                    ...prev,
                    [approval.id]: !prev[approval.id],
                  }))
                }
              />
            ))}
          </div>
        </section>
      )}

      {history.length > 0 && (
        <section aria-label="Historial de solicitudes">
          <div className="section-head">
            <h2>Historial</h2>
            <span className="muted">{history.length}</span>
          </div>
          <div className="approval-list">
            {history.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                now={now}
                busy={busy}
                result={result}
                expanded={Boolean(expanded[approval.id])}
                onApprove={() => openConfirm(approval, "approve")}
                onReject={() => openConfirm(approval, "reject")}
                onToggleExpand={() =>
                  setExpanded((prev) => ({
                    ...prev,
                    [approval.id]: !prev[approval.id],
                  }))
                }
              />
            ))}
          </div>
        </section>
      )}

      {confirm && (
        <ApprovalDialog
          approval={confirm.approval}
          kind={confirm.kind}
          busy={busy}
          now={now}
          error={actionError}
          onConfirm={() => void runAction()}
          onCancel={closeConfirm}
        />
      )}
    </div>
  );
}
