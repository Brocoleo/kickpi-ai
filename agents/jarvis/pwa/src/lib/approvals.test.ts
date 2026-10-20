import { describe, expect, it } from "vitest";
import {
  MIN_SECONDS_TO_APPROVE,
  canApprove,
  commandLine,
  confirmationFacts,
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
} from "./approvals";
import type { Approval } from "../types";

const NOW = Date.parse("2026-10-20T12:00:00Z");

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "abc",
    created_at: "2026-10-20T11:59:00Z",
    requested_by: "agente",
    approved_by: null,
    host: "macbook",
    project_id: null,
    operation: "host_git_diff_stat",
    params: { path: "/home/leandro/kickpi-ai" },
    argv: ["git", "--no-optional-locks", "-C", "/home/leandro/kickpi-ai", "diff", "--stat"],
    resolved_path: "/home/leandro/kickpi-ai",
    timeout_s: 30,
    request_hash: "rh",
    decision_hash: "dh",
    risk: "read",
    risk_description: "Resumen de cambios.",
    expires_at: "2026-10-20T12:05:00Z",
    status: "pending",
    decided_at: null,
    executed_at: null,
    exit_code: null,
    duration_ms: null,
    result_error: null,
    ...overrides,
  };
}

describe("estado de la solicitud", () => {
  it("distingue cada estado", () => {
    expect(statusMeta("pending").label).toBe("pendiente");
    expect(statusMeta("approved").label).toBe("aprobada");
    expect(statusMeta("consumed").label).toBe("ejecutada");
    expect(statusMeta("rejected").label).toBe("rechazada");
    expect(statusMeta("expired").label).toBe("expirada");
    expect(statusMeta("failed").label).toBe("fallida");
    expect(statusMeta("executing").label).toBe("ejecutando");
  });

  it("un estado desconocido no rompe la interfaz", () => {
    const meta = statusMeta("raro");
    expect(meta.label).toBe("raro");
    expect(meta.tone).toBe("off");
  });

  it("fallida significa resultado incierto: no se reintenta", () => {
    expect(isUncertain(approval({ status: "failed" }))).toBe(true);
    expect(isUncertain(approval({ status: "pending" }))).toBe(false);
  });

  it("consumida ya no se vuelve a ejecutar", () => {
    expect(isConsumed(approval({ status: "consumed" }))).toBe(true);
    expect(isConsumed(approval({ status: "approved" }))).toBe(false);
  });

  it("las abiertas van a pendientes y las demas al historial", () => {
    expect(isOpen(approval({ status: "pending" }))).toBe(true);
    expect(isOpen(approval({ status: "approved" }))).toBe(true);
    expect(isOpen(approval({ status: "executing" }))).toBe(true);
    expect(isOpen(approval({ status: "consumed" }))).toBe(false);
    expect(isOpen(approval({ status: "rejected" }))).toBe(false);
  });
});

describe("caducidad", () => {
  it("cuenta los segundos que faltan", () => {
    expect(secondsLeft("2026-10-20T12:05:00Z", NOW)).toBe(300);
    expect(secondsLeft("2026-10-20T11:59:00Z", NOW)).toBe(0);
    expect(secondsLeft("no-es-una-fecha", NOW)).toBe(0);
  });

  it("formatea la cuenta atras", () => {
    expect(formatCountdown(300)).toBe("5:00");
    expect(formatCountdown(67)).toBe("1:07");
    expect(formatCountdown(9)).toBe("0:09");
    expect(formatCountdown(-5)).toBe("0:00");
  });

  it("no se puede aprobar con menos de 10 segundos", () => {
    const casi = approval({ expires_at: "2026-10-20T12:00:05Z" });
    expect(canApprove(casi, NOW)).toBe(false);
    expect(isExpiring(casi, NOW)).toBe(true);

    const justo = approval({ expires_at: "2026-10-20T12:00:10Z" });
    expect(canApprove(justo, NOW)).toBe(true);
    expect(isExpiring(justo, NOW)).toBe(false);
  });

  it("una solicitud ya aprobada o consumida no se puede volver a aprobar", () => {
    expect(canApprove(approval({ status: "approved" }), NOW)).toBe(false);
    expect(canApprove(approval({ status: "consumed" }), NOW)).toBe(false);
  });

  it("el minimo es de 10 segundos", () => {
    expect(MIN_SECONDS_TO_APPROVE).toBe(10);
  });
});

describe("datos mostrados", () => {
  it("el comando es el argv exacto, sin inventar", () => {
    expect(commandLine(approval())).toBe(
      "git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat",
    );
  });

  it("los parametros se muestran tal cual", () => {
    expect(paramsText(approval())).toBe("path=/home/leandro/kickpi-ai");
    expect(paramsText(approval({ params: {} }))).toBe("sin parámetros");
  });

  it("la confirmacion incluye los datos exactos del backend", () => {
    const facts = confirmationFacts(approval(), NOW);
    const labels = facts.map((f) => f.label);
    expect(labels).toContain("Equipo");
    expect(labels).toContain("Operación");
    expect(labels).toContain("Riesgo");
    expect(labels).toContain("Comando exacto");
    expect(labels).toContain("Parámetros");
    expect(labels).toContain("Caduca en");

    const equipo = facts.find((f) => f.label === "Equipo")!;
    expect(equipo.value).toBe("macbook");
    const comando = facts.find((f) => f.label === "Comando exacto")!;
    expect(comando.value).toBe(
      "git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat",
    );
    const caducidad = facts.find((f) => f.label === "Caduca en")!;
    expect(caducidad.value).toBe("5:00");
  });

  it("el proyecto solo aparece si existe", () => {
    const conProyecto = confirmationFacts(approval({ project_id: "jarvis" }), NOW);
    expect(conProyecto.find((f) => f.label === "Proyecto")!.value).toBe("jarvis");
    const sinProyecto = confirmationFacts(approval(), NOW);
    expect(sinProyecto.find((f) => f.label === "Proyecto")).toBeUndefined();
  });
});

describe("salida extensa", () => {
  it("se trunca visualmente y avisa de cuanto falta", () => {
    const output = Array.from({ length: 20 }, (_, i) => `linea ${i}`).join("\n");
    const result = truncateOutput(output, 8);
    expect(result.truncated).toBe(true);
    expect(result.hiddenLines).toBe(12);
    expect(result.text.split("\n")).toHaveLength(8);
  });

  it("una salida corta no se trunca", () => {
    const result = truncateOutput("una linea", 8);
    expect(result.truncated).toBe(false);
    expect(result.hiddenLines).toBe(0);
    expect(result.text).toBe("una linea");
  });
});

describe("duracion", () => {
  it("se formatea en ms o segundos", () => {
    expect(formatDuration(340)).toBe("340 ms");
    expect(formatDuration(1234)).toBe("1.2 s");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
  });
});
