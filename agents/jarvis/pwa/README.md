# Jarvis PWA

Interfaz mobile-first del asistente Jarvis (React + Vite + TypeScript).

```bash
npm install
npm run dev     # proxy /api y /health a http://localhost:8888
npm run build   # compila en ../agent/static (lo sirve FastAPI en /)
```

Decisiones clave:

- **Mismo origen** que la API: sin CORS y sin exponer un segundo puerto.
- **Sin token en localStorage**: la sesión vive en una cookie `HttpOnly`.
- **La API nunca se cachea**: el service worker solo precachea assets.
- Iconos generados con la marca geométrica de Jarvis (anillo + punto).
