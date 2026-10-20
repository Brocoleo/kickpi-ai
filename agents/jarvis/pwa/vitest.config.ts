import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Configuracion SOLO de pruebas (la de la app vive en vite.config.ts).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    css: false,
  },
});
