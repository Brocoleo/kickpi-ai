/**
 * Confirmacion humana en dos pasos (Fase C).
 *
 * Pulsar "Aprobar" NO ejecuta nada: solo abre esta ventana con los datos
 * EXACTOS que ha recibido del backend (equipo, operacion, comando, riesgo,
 * caducidad). El unico acto es confirmar.
 *
 * El dialogo es modal, se cierra con Escape, devuelve el foco al boton que
 * lo abrio y anuncia el resultado por un region `aria-live`.
 */
import { useEffect, useRef } from "react";
import type { Approval } from "../types";
import { confirmationFacts } from "../lib/approvals";
import { ShieldCheckIcon, XIcon } from "./icons";

export type ConfirmKind = "approve" | "reject";

type Props = {
  approval: Approval;
  kind: ConfirmKind;
  busy: boolean;
  now: number;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ApprovalDialog({
  approval,
  kind,
  busy,
  now,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const isApprove = kind === "approve";
  const facts = confirmationFacts(approval, now);

  // Al abrir: el foco entra en el dialogo (y vuelve al salir).
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  // Escape cierra (salvo que haya una accion en curso).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  // Mantiene el foco dentro del dialogo (no se sale con Tab).
  useEffect(() => {
    const node = panelRef.current;
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = node.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    node.addEventListener("keydown", onKey);
    return () => node.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="modal-backdrop" onClick={() => !busy && onCancel()}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-desc"
        ref={panelRef}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <span className={`modal-mark ${isApprove ? "ok" : "danger"}`} aria-hidden="true">
            <ShieldCheckIcon />
          </span>
          <div className="modal-titles">
            <h2 id="confirm-title">
              {isApprove ? "¿Apruebas esta solicitud?" : "¿Rechazas esta solicitud?"}
            </h2>
            <p id="confirm-desc">
              {isApprove
                ? "Se aprobará y se ejecutará inmediatamente con los datos de abajo."
                : "La solicitud se cerrará sin ejecutar nada."}
            </p>
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={onCancel}
            disabled={busy}
            aria-label="Cerrar sin cambios"
          >
            <XIcon />
          </button>
        </div>

        <dl className="facts">
          {facts.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>

        <p className="modal-note">
          Jarvis no puede aprobar esto por ti: la decisión es tuya y queda
          registrada con tu nombre.
        </p>

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="btn"
            onClick={onCancel}
            disabled={busy}
            ref={cancelRef}
          >
            Cancelar
          </button>
          <button
            type="button"
            className={`btn ${isApprove ? "primary" : "danger-solid"}`}
            onClick={onConfirm}
            disabled={busy}
            ref={confirmRef}
          >
            {busy
              ? "Trabajando…"
              : isApprove
                ? "Confirmar y ejecutar"
                : "Confirmar rechazo"}
          </button>
        </div>
      </div>
    </div>
  );
}
