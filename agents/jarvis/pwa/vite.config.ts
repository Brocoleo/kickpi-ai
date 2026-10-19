import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// La PWA se compila directamente dentro de la imagen del agente:
//   pwa/  →  agent/static/  →  GET /  (mismo origen que /api, sin CORS)
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "apple-touch-icon.png"],
      manifest: {
        name: "Jarvis — KICKPI",
        short_name: "Jarvis",
        description: "Asistente privado y panel de control de tu KICKPI",
        lang: "es",
        theme_color: "#07090d",
        background_color: "#07090d",
        display: "standalone",
        orientation: "portrait",
        start_url: "/",
        scope: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "icon-512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallback: "/index.html",
        // La API y la salud NUNCA se sirven desde caché.
        navigateFallbackDenylist: [/^\/api\//, /^\/health$/],
        runtimeCaching: [],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://localhost:8888",
      "/health": "http://localhost:8888",
    },
  },
  build: {
    outDir: "../agent/static",
    emptyOutDir: true,
  },
});
