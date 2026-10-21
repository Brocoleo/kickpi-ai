import { expect, test, type Page } from "@playwright/test";

/**
 * Banco de pruebas de la PWA (chat, historial, exportación y navegación).
 *
 * Todo el backend se simula con interceptación de rutas: no hace falta la
 * placa, ni Tailscale, ni contraseñas. Así se puede comprobar cada cambio de
 * interfaz en segundos y de forma reproducible.
 *
 * Lo que se cubre aquí:
 *  - streaming SSE real (segment → tool → token → done),
 *  - actividad de herramientas agrupada, también mientras está en curso,
 *  - historial restaurado desde SQLite (fechas, herramientas, respuesta),
 *  - exportación a Markdown,
 *  - pestañas Archivos (mismo origen) y Equipos (estado por host).
 */

// --------------------------------------------------------------------------- //
// Utilidades
// --------------------------------------------------------------------------- //

type Fila = {
  id: number;
  role: string;
  content: string | null;
  tool_calls?: unknown[] | null;
  tool_call_id?: string | null;
  name?: string | null;
  created_at: string;
  tool_ok?: boolean;
  tool_duration_ms?: number;
  tool_summary?: string;
};

const AHORA = new Date();

function sse(eventos: Array<[string, unknown]>): string {
  return eventos
    .map(([nombre, datos]) => `event: ${nombre}\ndata: ${JSON.stringify(datos)}\n\n`)
    .join("");
}

/** Historial de un turno real: pregunta, herramienta ejecutada y respuesta. */
function turnoConHerramienta(): Fila[] {
  const creado = new Date(AHORA);
  creado.setMinutes(creado.getMinutes() - 5);
  const iso = creado.toISOString();

  return [
    { id: 1, role: "user", content: "¿cómo está el sistema?", created_at: iso },
    {
      id: 2,
      role: "assistant",
      content: null,
      created_at: iso,
      tool_calls: [
        {
          id: "call_1",
          type: "function",
          function: { name: "system_stats", arguments: "{}" },
        },
      ],
    },
    {
      id: 3,
      role: "tool",
      content: '{"cpu":5}',
      tool_call_id: "call_1",
      name: "system_stats",
      created_at: iso,
      tool_ok: true,
      tool_duration_ms: 12,
      tool_summary: "CPU 5%",
    },
    { id: 4, role: "assistant", content: "Todo tranquilo.", created_at: iso },
  ];
}

type Backend = {
  /** Filas que devuelve GET /api/conversations/{id}. */
  historial?: Fila[];
  /** Cuerpo SSE de POST /api/chat/stream. */
  stream?: string;
  /** Id de la conversación creada por el stream. */
  conversationId?: string;
  /** Deja la petición colgada (para probar «Detener»). */
  colgar?: boolean;
};

async function mockBackend(page: Page, backend: Backend = {}): Promise<void> {
  const conversacion = backend.conversationId ?? "c1";

  await page.route("**/health", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "0.6.0",
        llm_configured: true,
        llm_model: "test/free",
        tools_reachable: true,
        pwa: true,
      }),
    });
  });

  await page.route("**/api/**", async (route) => {
    const peticion = route.request();
    const ruta = new URL(peticion.url()).pathname.replace(/^\/api/, "");

    const json = (cuerpo: unknown, estado = 200) =>
      route.fulfill({
        status: estado,
        contentType: "application/json",
        body: JSON.stringify(cuerpo),
      });

    if (ruta === "/auth/me") {
      return json({
        authenticated: true,
        method: "session",
        password_configured: true,
        session_ttl_days: 30,
        version: "0.6.0",
        csrf_token: "csrf-de-prueba",
        user: "leandro",
      });
    }

    if (ruta === "/aprobaciones") {
      return json({ approvals: [], count: 0 });
    }

    if (ruta === "/chat/stream" && peticion.method() === "POST") {
      if (backend.colgar) {
        // Nunca responde: el turno queda «en curso» hasta que se aborta.
        await new Promise(() => {});
      }
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-store" },
        body: backend.stream ?? "",
      });
    }

    if (ruta === "/conversations") {
      return json(
        backend.historial
          ? {
              conversations: [
                { id: conversacion, title: "", updated_at: AHORA.toISOString() },
              ],
              count: 1,
            }
          : { conversations: [], count: 0 },
      );
    }

    if (ruta === `/conversations/${conversacion}`) {
      if (!backend.historial) return json({ detail: "no encontrada" }, 404);
      return json({ conversation_id: conversacion, messages: backend.historial });
    }

    if (ruta === "/equipos") {
      return json({
        hosts: [
          {
            id: "kickpi",
            label: "kickpi",
            transport: "local",
            enabled: true,
            address: null,
            os: "Debian 13",
            roots: ["/home/leandro/jarvis"],
            online: true,
            latency_ms: 3,
          },
          {
            id: "desktop",
            label: "desktop",
            transport: "ssh",
            enabled: true,
            address: "100.73.247.126",
            os: "Linux",
            roots: ["/mnt/data"],
            online: false,
            reason: "SSH: TimeoutError",
          },
        ],
        executions: [],
        count: 2,
      });
    }

    return json({ detail: `ruta no simulada: ${ruta}` }, 404);
  });
}

// --------------------------------------------------------------------------- //
// Pruebas
// --------------------------------------------------------------------------- //

test("el chat transmite en vivo y muestra la actividad agrupada", async ({ page }) => {
  // Sin historial previo: así lo que se ve viene SOLO del stream y no se
  // confunde con la conversación restaurada.
  await mockBackend(page, {
    conversationId: "c1",
    stream: sse([
      ["segment", {}],
      [
        "tool",
        {
          tool: "system_stats",
          arguments: {},
          ok: true,
          duration_ms: 12,
          summary: "CPU 5%",
        },
      ],
      ["segment", {}],
      ["token", { text: "Todo " }],
      ["token", { text: "tranquilo." }],
      [
        "done",
        {
          conversation_id: "c1",
          reply: "Todo tranquilo.",
          tool_trace: [],
          model: "test/free",
        },
      ],
    ]),
  });

  await page.goto("/");

  const caja = page.getByPlaceholder(/Escribe a Jarvis/);
  await caja.fill("¿cómo está el sistema?");
  await caja.press("Enter");

  // La pregunta aparece al instante (no se espera al servidor).
  await expect(page.getByText("¿cómo está el sistema?")).toBeVisible();

  // La respuesta final llega por tokens.
  await expect(page.getByText("Todo tranquilo.")).toBeVisible();

  // La herramienta queda agrupada, con su duración, no como chip suelto.
  await expect(page.getByText("system_stats")).toBeVisible();
  await expect(page.getByText(/1 acción · 12 ms/)).toBeVisible();
  await expect(page.getByText("CPU 5%")).toBeVisible();

  // Y aparece la fecha del día.
  await expect(page.getByText("Hoy")).toBeVisible();
});

test("restaura un historial con fecha, herramientas y respuesta", async ({ page }) => {
  await mockBackend(page, { historial: turnoConHerramienta() });

  await page.goto("/");

  await expect(page.getByText("¿cómo está el sistema?")).toBeVisible();
  await expect(page.getByText("Todo tranquilo.")).toBeVisible();
  await expect(page.getByText("Hoy")).toBeVisible();
  await expect(page.getByText(/1 acción · 12 ms/)).toBeVisible();

  // El bloque nace plegado cuando hay varias acciones; con una, abierto.
  await expect(page.getByRole("button", { name: /Ocultar actividad/ })).toBeVisible();
  await expect(page.getByText("CPU 5%")).toBeVisible();
});

test("exporta la conversación a Markdown", async ({ page }) => {
  await mockBackend(page, { historial: turnoConHerramienta() });

  await page.goto("/");
  await expect(page.getByText("Todo tranquilo.")).toBeVisible();

  await page.getByRole("button", { name: "Exportar" }).click();

  const [descarga] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Descargar Markdown" }).click(),
  ]);

  expect(descarga.suggestedFilename()).toMatch(/^jarvis-\d{8}-\d{4}\.md$/);

  const flujo = await descarga.createReadStream();
  const trozos: Buffer[] = [];
  for await (const trozo of flujo) trozos.push(Buffer.from(trozo));
  const texto = Buffer.concat(trozos).toString("utf8");

  expect(texto).toContain("# Conversación con Jarvis");
  expect(texto).toContain("## Tú");
  expect(texto).toContain("## Jarvis");
  expect(texto).toContain("`system_stats` ok (12 ms) — CPU 5%");
  expect(texto).toContain("Todo tranquilo.");
});

test("Archivos abre FileBrowser en el mismo origen", async ({ page }) => {
  await mockBackend(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Archivos" }).click();

  const filebrowser = page.getByRole("link", { name: /FileBrowser/ });
  await expect(filebrowser).toBeVisible();
  await expect(filebrowser).toHaveAttribute("href", "/files/");

  // Portainer sigue en su puerto: no admite subrutas.
  const portainer = page.getByRole("link", { name: /Portainer/ });
  await expect(portainer).toHaveAttribute("href", /:9443\/$/);
});

test("Equipos muestra el estado de cada host", async ({ page }) => {
  await mockBackend(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Equipos" }).click();

  const tarjetaKickpi = page.locator(".host-card", { hasText: "kickpi" });
  await expect(tarjetaKickpi.getByText("en línea")).toBeVisible();

  const tarjetaDesktop = page.locator(".host-card", { hasText: "desktop" });
  await expect(tarjetaDesktop).toBeVisible();
  await expect(tarjetaDesktop.getByText("sin conexión")).toBeVisible();
  await expect(tarjetaDesktop.getByText("SSH: TimeoutError")).toBeVisible();
});

test("un stream cortado se avisa y se puede reintentar", async ({ page }) => {
  // Llega texto, pero nunca el cierre: es lo que pasa si se cae la red o el
  // proveedor a mitad de respuesta. Antes se veía como una respuesta completa.
  await mockBackend(page, {
    stream: sse([
      ["segment", {}],
      ["token", { text: "Voy a revisar" }],
    ]),
  });

  await page.goto("/");
  const caja = page.getByPlaceholder(/Escribe a Jarvis/);
  await caja.fill("¿cómo está el sistema?");
  await caja.press("Enter");

  await expect(page.getByText(/se cortó antes de terminar/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
});

test("un error del proveedor se puede reintentar", async ({ page }) => {
  // Primera petición: el proveedor free falla. Segunda: responde.
  let peticiones = 0;

  await mockBackend(page, {});
  // Sustituye el stream simulado por uno que falla solo la primera vez.
  await page.unroute("**/api/chat/stream");
  await page.route("**/api/chat/stream", async (route) => {
    peticiones += 1;
    if (peticiones === 1) {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse([
          ["segment", {}],
          ["error", { detail: "el proveedor gratuito no respondió" }],
        ]),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sse([
        ["segment", {}],
        ["token", { text: "Ahora sí." }],
        [
          "done",
          {
            conversation_id: "c1",
            reply: "Ahora sí.",
            tool_trace: [],
            model: "test/free",
          },
        ],
      ]),
    });
  });

  await page.goto("/");
  const caja = page.getByPlaceholder(/Escribe a Jarvis/);
  await caja.fill("¿qué contenedores hay?");
  await caja.press("Enter");

  await expect(page.getByText(/el proveedor gratuito no respondió/)).toBeVisible();
  await page.getByRole("button", { name: "Reintentar" }).click();

  await expect(page.getByText("Ahora sí.")).toBeVisible();
  expect(peticiones).toBe(2);
});

test("mientras genera se puede detener", async ({ page }) => {
  await mockBackend(page, { colgar: true });

  await page.goto("/");
  const caja = page.getByPlaceholder(/Escribe a Jarvis/);
  await caja.fill("una pregunta que tarda");
  await caja.press("Enter");

  const detener = page.getByRole("button", { name: "Detener la respuesta" });
  await expect(detener).toBeVisible();
  await detener.click();

  // Al abortar, el compositor vuelve a estar disponible y no queda un turno
  // colgado esperando para siempre.
  await expect(page.getByRole("button", { name: "Enviar mensaje" })).toBeVisible();
});

test("muestra el diagnóstico del turno al cerrar", async ({ page }) => {
  await mockBackend(page, {
    stream: sse([
      ["segment", {}],
      [
        "tool",
        {
          tool: "system_stats",
          arguments: {},
          ok: true,
          duration_ms: 12,
          summary: "CPU 5%",
        },
      ],
      ["segment", {}],
      ["token", { text: "Todo tranquilo." }],
      [
        "done",
        {
          conversation_id: "c1",
          reply: "Todo tranquilo.",
          tool_trace: [],
          model: "test/free",
          elapsed_ms: 1234,
          tools_count: 1,
        },
      ],
    ]),
  });

  await page.goto("/");
  const caja = page.getByPlaceholder(/Escribe a Jarvis/);
  await caja.fill("¿cómo está el sistema?");
  await caja.press("Enter");

  await expect(page.getByText("Todo tranquilo.")).toBeVisible();
  // Modelo · duración · herramientas, todo en la misma línea de diagnóstico.
  await expect(page.getByText(/test\/free · 1\.2 s · 1 herramienta/)).toBeVisible();
});
