import { defineConfig, devices } from "@playwright/test";

/**
 * Verificacion de la PWA en el tamano real del iPhone (390 x 844).
 *
 * Se usa el descriptor del iPhone 13 (390x844, tactil) sobre Chromium: en
 * este equipo WebKit no arranca (faltan librerias del sistema que
 * requieren sudo), asi que la comprobacion de Safari se hace por codigo
 * (dvh, safe-area, prefijos -webkit) y se documenta aparte.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    ...devices["iPhone 13"],
    // Chromium no es Safari: se avisa del motor usado en las trazas.
    browserName: "chromium",
  },
  projects: [{ name: "iphone-390x844" }],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 90_000,
  },
});
