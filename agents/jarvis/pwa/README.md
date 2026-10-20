# Jarvis PWA

Interfaz mobile-first del asistente Jarvis (React + Vite + TypeScript).

```bash
npm install
npm run dev     # proxy /api y /health a http://localhost:8888
npm run build   # compila en ../agent/static (lo sirve FastAPI en /)
npm test        # pruebas unitarias y de integracion (Vitest + Testing Library)
npm run test:e2e  # verificacion en 390x844 (Playwright, backend simulado)
```

Decisiones clave:

- **Mismo origen** que la API: sin CORS y sin exponer un segundo puerto.
- **Sin token en localStorage**: la sesión vive en una cookie `HttpOnly`.
- **El token CSRF vive solo en memoria**: se recibe en el login y en
  `/api/auth/me`, y se envía en `X-CSRF-Token` en las peticiones que cambian
  estado. Nunca se persiste en disco.
- **La API nunca se cachea**: el service worker solo precachea assets.
- Iconos generados con la marca geométrica de Jarvis (anillo + punto).

## Pestaña Aprobaciones

- Seis pestañas: Chat, Métricas, Equipos, Archivos, Aprobaciones, Ajustes.
- Refresco cada 10 s y al volver a enfocar la aplicación.
- **Confirmación en dos pasos**: pulsar «Aprobar» abre un diálogo con los
  datos exactos que envía el backend (equipo, operación, comando exacto,
  parámetros, riesgo, caducidad). Solo al confirmar se aprueba y se ejecuta.
- Cuenta atrás por solicitud; con menos de 10 s el botón queda deshabilitado.
- Una solicitud `failed` (resultado incierto) no se puede reintentar; una
  `consumed` ya se ejecutó y no se repite.
- Historial con estado, `exit code`, duración y la salida de la ejecución
  (truncada, con opción de expandir).
- **Resultado incierto**: si se pierde la respuesta del servidor, la app
  consulta el estado persistido (es una lectura) y **no reintenta la
  ejecución**. Un error de red se informa como error de red, nunca como
  "la ejecución falló".
