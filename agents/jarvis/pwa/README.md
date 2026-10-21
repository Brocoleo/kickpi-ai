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

## Despliegue (build a prueba de balas)

La PWA **ya no se hornea en la imagen del agente**: `docker-compose.yml`
monta `./agent/static` en `/app/static` (solo lectura). Publicar un build
nuevo es sincronizar archivos, sin reconstruir la imagen:

```bash
./deploy.sh            # compila, sincroniza a la placa y VERIFICA
./deploy.sh --check    # solo verifica (build local vs placa vs /health)
./deploy.sh --watch    # vigila src/ y despliega en cada cambio (opcional)
./deploy.sh --test     # corre vitest y luego despliega
```

El script falla si:

- no hay build local válido (y entonces **no toca** la placa),
- el `index.html` de la placa referencia otro bundle,
- `/health` reporta un `static_asset` distinto (backend viejo o caché).

`GET /health` incluye `static_asset` (p. ej. `index--SMPJ4-r.js`), y la
pantalla **Ajustes → Sesión → Build PWA** lo muestra: si el iPhone no ve la
pestaña esperada, ese campo delata al instante qué build está corriendo.

Cache: el HTML, `sw.js` y el manifest se sirven con `no-store`; los assets
con hash, `immutable`. Así el teléfono nunca queda atascado en un build
antiguo.
