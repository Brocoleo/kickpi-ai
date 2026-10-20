import { expect, test } from "@playwright/test";

/**
 * PRUEBA INTEGRADA (C5) contra el backend REAL, en entorno AISLADO:
 *
 *   PWA (este navegador)
 *     → agente  (sesión humana + CSRF + Origin)
 *       → ejecutor (token de aprobaciones)
 *         → SSH al MacBook (forced command)
 *           → auditoría (SQLite)
 *
 * No usa mocks: el backend se levanta aparte, con sus propias bases de
 * datos temporales y su catalogo real. La PWA que se sirve es el bundle
 * compilado de verdad.
 *
 * Se salta salvo que se indique el backend:
 *   JARVIS_E2E_URL=http://127.0.0.1:8889 \
 *   JARVIS_E2E_PASSWORD=... \
 *   npx playwright test e2e/integrado.spec.ts
 */

const URL = process.env.JARVIS_E2E_URL;
const PASSWORD = process.env.JARVIS_E2E_PASSWORD;
const TARGET_PATH = process.env.JARVIS_E2E_PATH ?? "/home/leandro/kickpi-ai";

test.describe("flujo integrado real", () => {
  test.skip(!URL || !PASSWORD, "requiere JARVIS_E2E_URL y JARVIS_E2E_PASSWORD");

  test("el humano aprueba y la operacion se ejecuta en el MacBook", async ({ page }) => {
    await page.goto(URL!);

    // 1) Login con la sesion humana.
    await page.getByPlaceholder("••••••••••••").fill(PASSWORD!);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page.getByText("Aprobaciones")).toBeVisible();

    // 2) La pestana muestra la solicitud pendiente que creo el agente.
    await page.getByRole("button", { name: /Aprobaciones/ }).click();
    const card = page.locator("article").filter({ hasText: "host_git_diff_stat" });
    await expect(card).toBeVisible();
    await expect(card.getByText("macbook")).toBeVisible();
    await expect(
      card.getByText(`git --no-optional-locks -C ${TARGET_PATH} diff --stat`),
    ).toBeVisible();

    // 3) Confirmacion en dos pasos: nada se ejecuta antes de confirmar.
    await card.getByRole("button", { name: /^Aprobar y ejecutar/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("macbook")).toBeVisible();

    // 4) Confirmar: aprueba y ejecuta contra el MacBook de verdad.
    const executed = page.waitForResponse((r) => r.url().includes("/ejecutar"));
    await dialog.getByRole("button", { name: "Confirmar y ejecutar" }).click();
    const response = await executed;
    expect(response.status()).toBe(200);
    await expect(page.getByText(/Aprobada y ejecutada/)).toBeVisible();

    // 5) La salida real del `git diff --stat` del MacBook.
    await expect(page.getByText(/file[s]? changed/)).toBeVisible();

    // 6) La solicitud pasa al historial y ya NO se puede volver a ejecutar.
    await expect(page.getByText("ejecutada").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /^Aprobar y ejecutar/ })).toHaveCount(0);
  });
});
