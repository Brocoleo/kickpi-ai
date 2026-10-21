import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Verificacion de la pestana "Aprobaciones" en 390 x 844.
 *
 * El backend se simula en el propio navegador (route interception): asi se
 * comprueba el flujo completo de la interfaz sin tocar ningun servidor.
 */

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
    "Resumen de los cambios sin commitear de un repositorio autorizado.",
  expires_at: new Date(Date.now() + 240_000).toISOString(),
  status: "pending",
  decided_at: null,
  executed_at: null,
  exit_code: null,
  duration_ms: null,
  result_error: null,
};

const DIFF_STDOUT = [
  " agents/jarvis/README.md                    | 36 ++++++++++-----",
  " agents/jarvis/agent/app/executor_client.py | 34 +++++++++---",
  " agents/jarvis/executor/app/main.py         | 12 +++---",
  " agents/jarvis/executor/config/catalog.yml  | 23 ++++--",
  " 4 files changed, 85 insertions(+), 20 deletions(-)",
].join("\n");

type Recorded = { method: string; url: string; headers: Record<string, string> };

async function mockBackend(page: Page) {
  const recorded: Recorded[] = [];
  const state = { approval: { ...APPROVAL } };

  await page.route("**/health", async (route: Route) => {
    await route.fulfill({
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
    });
  });

  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    recorded.push({
      method: request.method(),
      url: request.url(),
      headers: request.headers(),
    });

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
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
    if (path === "/health") {
      return json({
        status: "ok",
        version: "0.2.0",
        llm_configured: true,
        llm_model: "test",
        tools_reachable: true,
        pwa: true,
      });
    }
    if (path === "/aprobaciones" && request.method() === "GET") {
      return json({ approvals: [state.approval], count: 1 });
    }
    if (path === "/equipos" && request.method() === "GET") {
      return json({
        hosts: [
          {
            id: "kickpi",
            label: "kickpi",
            transport: "local",
            enabled: true,
            address: null,
            os: "Debian 13",
            roots: ["/mnt/data"],
            online: true,
          },
        ],
        executions: [],
        count: 1,
      });
    }
    if (path === "/executions" && request.method() === "GET") {
      // Historial del ejecutor: lo que se ve tras aprobar.
      return json({
        executions: [
          {
            id: 7,
            created_at: new Date(Date.now() - 70_000).toISOString(),
            host_id: "macbook",
            operation: "host_git_diff_stat",
            params_json: "{}",
            allowed: 1,
            ok: 1,
            exit_code: 0,
            duration_ms: 42,
            output_excerpt: " 1 file changed",
            error: null,
          },
          {
            id: 6,
            created_at: new Date(Date.now() - 600_000).toISOString(),
            host_id: "desktop",
            operation: "host_disk_usage",
            params_json: "{}",
            allowed: 0,
            ok: 0,
            exit_code: null,
            duration_ms: null,
            output_excerpt: null,
            error: "operación no permitida para este host",
          },
        ],
        count: 2,
      });
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
        exit_code: 0,
        duration_ms: 42,
        executed_at: new Date().toISOString(),
      };
      return json({
        ok: true,
        state: "consumed",
        approval_id: state.approval.id,
        exit_code: 0,
        stdout: DIFF_STDOUT,
        stderr: null,
        duration_ms: 42,
        error: null,
      });
    }
    if (path.endsWith("/rechazar")) {
      state.approval = { ...state.approval, status: "rejected" };
      return json({ approval: state.approval });
    }
    return json({ detail: `ruta no simulada: ${path}` }, 404);
  });

  return { recorded, state };
}

test.describe("pestaña Aprobaciones (390x844)", () => {
  test("la pestaña Aprobaciones muestra el badge y la solicitud", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");

    const tab = page.getByRole("button", { name: /Aprobaciones/ });
    await expect(tab).toBeVisible();
    // El badge anuncia el numero de pendientes.
    await expect(tab).toHaveAttribute("aria-label", /1 solicitud pendiente/);

    await tab.click();

    await expect(page.getByText("host_git_diff_stat").first()).toBeVisible();
    await expect(page.getByText("macbook").first()).toBeVisible();
    await expect(page.getByText(/Resumen de los cambios sin commitear/)).toBeVisible();
    // El comando exacto se muestra tal cual lo envia el backend.
    await expect(
      page.getByText("git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat"),
    ).toBeVisible();
    // Cuenta atras visible.
    await expect(page.getByRole("timer")).toBeVisible();
  });

  test("aprobar exige confirmacion en dos pasos y ejecuta sola", async ({ page }) => {
    const { recorded } = await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Aprobaciones/ }).click();

    await page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first().click();

    // Paso 2: el dialogo muestra los datos EXACTOS del backend.
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("host_git_diff_stat")).toBeVisible();
    await expect(dialog.getByText("macbook")).toBeVisible();
    await expect(
      dialog.getByText("git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat"),
    ).toBeVisible();

    // Todavia no se ha llamado a la API: la confirmacion es obligatoria.
    expect(recorded.some((r) => r.url.includes("/aprobar"))).toBe(false);

    const executed = page.waitForResponse((r) => r.url().includes("/ejecutar"));
    await dialog.getByRole("button", { name: "Confirmar y ejecutar" }).click();
    await executed;

    await expect(dialog).toBeHidden();
    await expect(page.getByText(/Aprobada y ejecutada/)).toBeVisible();

    // Se aprueba y se ejecuta, en ese orden, y con el token CSRF.
    const approve = recorded.find((r) => r.url.includes("/aprobar"));
    const execute = recorded.find((r) => r.url.includes("/ejecutar"));
    expect(approve).toBeTruthy();
    expect(execute).toBeTruthy();
    expect(recorded.indexOf(approve!)).toBeLessThan(recorded.indexOf(execute!));
    expect(approve!.headers["x-csrf-token"]).toBe("csrf-de-prueba");
    expect(execute!.headers["x-csrf-token"]).toBe("csrf-de-prueba");

    // Tras ejecutar, la solicitud sale de "pendientes" y el badge desaparece.
    await expect(page.getByText("HISTORIAL")).toBeVisible();
    await expect(page.getByText("ejecutada").first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Aprobaciones/ }),
    ).not.toHaveAttribute("aria-label", /solicitud pendiente/);
  });

  test("el resultado se muestra con exit code, duracion y salida", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Aprobaciones/ }).click();
    await page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first().click();
    const executed = page.waitForResponse((r) => r.url().includes("/ejecutar"));
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirmar y ejecutar" })
      .click();
    await executed;

    await expect(page.getByText(/exit 0 · 42 ms/)).toBeVisible();
    await expect(page.getByText(/4 files changed/)).toBeVisible();
  });

  test("no hay desbordamiento horizontal ni objetivos tactiles pequenos", async ({
    page,
  }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Aprobaciones/ }).click();
    await expect(
      page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first(),
    ).toBeVisible();

    // Sin scroll horizontal a 390 px.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);

    // Los botones de accion alcanzan el minimo tactil (44 px).
    const button = page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first();
    const box = await button.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  });

  test("ningun secreto del backend llega al navegador", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Aprobaciones/ }).click();
    await page.getByRole("button", { name: /^Aprobar y ejecutar/ }).first().click();

    const html = await page.content();
    expect(html).not.toContain("JARVIS_APPROVALS_TOKEN");
    expect(html).not.toContain("X-Approvals-Token");
    expect(html).not.toContain("JARVIS_EXECUTOR_TOKEN");
    // El token CSRF del navegador no se persiste en disco.
    const storage = await page.evaluate(() => ({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
    }));
    expect(storage.local).toHaveLength(0);
    expect(storage.session).toHaveLength(0);
  });
});

test.describe("actividad reciente en Aprobaciones", () => {
  test("muestra las ejecuciones y distingue denegadas", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Aprobaciones/ }).click();

    const actividad = page
      .getByRole("region", { name: /Actividad reciente/ });
    await expect(actividad).toBeVisible();
    // La operación aparece también en la solicitud: se mira solo la lista.
    await expect(actividad.getByText("host_git_diff_stat")).toBeVisible();
    // Una ejecución denegada no es un fallo del host: se etiqueta aparte.
    await expect(actividad.getByText("denegado")).toBeVisible();
    await expect(actividad.getByText("hace 1 min")).toBeVisible();
  });

  test("Equipos ya no repite la actividad: solo hosts", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Equipos/ }).click();

    // El nombre y el id del host coinciden: basta con que haya tarjeta.
    await expect(page.getByText("kickpi").first()).toBeVisible();
    await expect(page.getByText("Actividad reciente")).toHaveCount(0);
    await expect(page.getByText("Recursos del servidor")).toBeVisible();
  });
});
