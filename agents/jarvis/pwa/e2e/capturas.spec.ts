import { test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

/**
 * Capturas de la pestana Aprobaciones en 390 x 844 (para el informe).
 * No es una prueba: solo guarda imagenes en /tmp/opencode/shots.
 */
const OUT = "/tmp/opencode/shots";

const APPROVAL = {
  id: "abc123",
  created_at: "2026-10-20T03:55:00+00:00",
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
  risk_description:
    "Resumen de los cambios sin commitear de un repositorio autorizado. Solo lectura: no escribe nada, no dispara hooks y no modifica el repositorio.",
  expires_at: new Date(Date.now() + 240_000).toISOString(),
  status: "pending",
  decided_at: null,
  executed_at: null,
  exit_code: null,
  duration_ms: null,
  result_error: null,
};

test("capturas de la pestaña Aprobaciones", async ({ page }) => {
  await mkdir(OUT, { recursive: true });

  const state = { approval: { ...APPROVAL } };

  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "0.2.0",
        llm_configured: true,
        llm_model: "test",
        tools_reachable: true,
        pwa: true,
      }),
    }),
  );

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const json = (body: unknown) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (path === "/auth/me") {
      return json({
        authenticated: true,
        method: "session",
        password_configured: true,
        session_ttl_days: 30,
        version: "0.2.0",
        csrf_token: "csrf-de-prueba",
        user: "leandro",
      });
    }
    if (path === "/aprobaciones") {
      return json({ approvals: [state.approval], count: 1 });
    }
    if (path.endsWith("/aprobar")) {
      state.approval = {
        ...state.approval,
        status: "approved",
        approved_by: "leandro",
      };
      return json({ approval: state.approval });
    }
    if (path.endsWith("/ejecutar")) {
      state.approval = {
        ...state.approval,
        status: "consumed",
        approved_by: "leandro",
        exit_code: 0,
        duration_ms: 42,
        executed_at: new Date().toISOString(),
      };
      return json({
        ok: true,
        state: "consumed",
        approval_id: APPROVAL.id,
        exit_code: 0,
        stdout: [
          " agents/jarvis/README.md                    | 36 ++++++++++-----",
          " agents/jarvis/agent/app/executor_client.py | 34 +++++++++---",
          " agents/jarvis/executor/app/main.py         | 12 +++---",
          " agents/jarvis/executor/config/catalog.yml  | 23 ++++--",
          " 4 files changed, 85 insertions(+), 20 deletions(-)",
        ].join("\n"),
        stderr: null,
        duration_ms: 42,
        error: null,
      });
    }
    return json({ detail: path }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Aprobaciones/ }).click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/c4-1-aprobaciones.png` });

  await page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first().click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/c4-2-confirmacion.png` });

  await page.getByRole("dialog").getByRole("button", { name: "Confirmar y ejecutar" }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/c4-3-resultado.png` });
});
