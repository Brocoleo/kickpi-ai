# Jarvis v0.2

Asistente privado para la **KICKPI K2B V2**: cerebro con **FastAPI + LangGraph +
SQLite**, PWA mobile-first para iPhone y herramientas de **solo lectura** sobre
el servidor. Inferencia en la nube (OpenRouter / cualquier API compatible).

## Arquitectura

```
   iPhone (PWA instalada)
        │  HTTPS, mismo origen
        ▼
   Tailscale Serve :8888
        │
   ┌─────────────────┐   HTTP interno   ┌──────────────────┐
   │  jarvis-agent   │ ───────────────► │  jarvis-tools    │
   │  FastAPI + PWA  │  X-Tools-Token   │  psutil + docker │
   │  LangGraph      │                  └────────┬─────────┘
   │  SQLite         │                           │
   └────────┬────────┘                           │ /var/run/docker.sock
            │ HTTPS                              ▼
            ▼                             Docker (SOLO lectura)
     OpenRouter / LLM
```

- **jarvis-agent** no ve Docker ni el sistema: solo habla con `jarvis-tools`.
- **jarvis-tools** es el único con el socket de Docker y solo expone `GET`.
- La **PWA se sirve desde el propio agente** (`/`), así que la API (`/api/*`) y
  la interfaz comparten origen: **cero CORS** y una sola superficie expuesta.

## Servicios y puertos

| Servicio | Puerto | Exposición |
|---|---|---|
| `jarvis-agent` | 8888 | `127.0.0.1:8888` → Tailscale Serve HTTPS |
| `jarvis-tools` | 8000 | interna en la red `jarvis` (no publicada) |

## Puesta en marcha

```bash
cd agents/jarvis
cp .env.example .env

# Dos tokens distintos
openssl rand -hex 32   # -> JARVIS_API_TOKEN
openssl rand -hex 32   # -> JARVIS_TOOLS_TOKEN

# Clave del LLM (gratuita, p. ej. OpenRouter :free)
#   JARVIS_LLM_API_KEY=...

# 1) Compila la PWA dentro de la imagen del agente
cd pwa && npm install && npm run build && cd ..
#    (genera agent/static/)

# 2) Levanta los servicios
sudo docker compose up -d --build

# 3) Expón el agente SOLO a la tailnet
sudo tailscale serve --bg --https=8888 http://localhost:8888

# 4) Define tu contraseña de navegador (interactiva, no pasa por el chat)
./set-admin-password.sh
```

URL de la PWA: `https://kickpi-jarvis.tail1e3b4d.ts.net:8888/`

Desarrollo de la interfaz con recarga en caliente:

```bash
cd pwa && npm run dev
# Vite hace proxy de /api y /health a http://localhost:8888
```

## Autenticación

Dos vías deliberadamente separadas:

1. **Navegador (PWA)**: `POST /api/auth/login` con la contraseña → cookie de
   sesión `HttpOnly`, `Secure`, `SameSite=Strict`, 30 días. El token **nunca**
   se guarda en `localStorage` ni es accesible desde JavaScript.
2. **API (scripts/automatización)**: `X-API-Token` o `Authorization: Bearer`.

Detalles:

- Las sesiones se guardan en SQLite como **SHA-256 del identificador**, no el
  token en claro. Caducan y se purgan en cada login.
- La contraseña se almacena como hash **scrypt** (`scrypt:n:r:p:salt:hash`).
  Si `JARVIS_ADMIN_PASSWORD_HASH` está vacío, el login acepta el
  `JARVIS_API_TOKEN` (útil para la primera prueba).
- **Fail-closed**: sin token ni contraseña configurados, los endpoints
  protegidos responden `503`.
- Limitador de intentos: 10 fallos / 15 min.
- No se confía en `X-Forwarded-For` para autorizar.
- Cabeceras: CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.

## PWA (iPhone)

Cuatro secciones: **Chat**, **Métricas**, **Archivos**, **Ajustes**.

- Chat con memoria persistente, chips de herramientas y modelo usado.
- Métricas en vivo (CPU, RAM, temperatura, discos, carga, uptime) y
  contenedores; refresco cada 10 s.
- Archivos enlaza a FileBrowser y Portainer (cada uno con su propio login).
- Ajustes muestra la cadena de modelos, el circuit breaker y cierra sesión.
- Instalación: Safari → Compartir → **Añadir a pantalla de inicio**.
- Service worker: precachea solo los assets; **la API nunca se cachea**.

## Herramientas disponibles (solo lectura)

| Herramienta | Qué devuelve |
|---|---|
| `system_stats` | CPU (total y por núcleo), carga, temperatura, RAM, swap, uptime |
| `disk_usage` | Uso de disco de `/`, `/mnt/data`, `/data` |
| `docker_ps` | Contenedores: estado, salud, puertos, imagen, reinicios |
| `container_logs` | Últimas N líneas, saneadas (secretos redactados, tamaño acotado) |

## Fallback de modelos (disponibilidad sin coste)

Cadena: `JARVIS_LLM_MODEL` + `JARVIS_LLM_FALLBACK_MODELS` (separados por comas).

| Situación | Comportamiento |
|---|---|
| HTTP 429 | Sin reintento → siguiente modelo |
| HTTP 5xx | Reintento breve con backoff → siguiente modelo |
| Timeout / error de red | Siguiente modelo |
| HTTP 401/403 | **Detener** (credenciales mal configuradas) |
| Respuesta válida | Se mantiene y se devuelve ese modelo |
| Todos fallan | `LLMError` controlado con resumen |

**Circuit breaker:** tras 2 fallos consecutivos, un modelo queda en enfriamiento
60 s. Si *todos* están abiertos, se fuerza el primero para no quedarnos sin
servicio.

**Sin acciones duplicadas:** el fallback solo repite la llamada al modelo; las
`tool_calls` no se reenvían y las herramientas se ejecutan una única vez.

**Límites de los `:free`:** OpenRouter aplica ~20 req/min y un tope diario según
la cuenta. Modelos gratuitos verificados con tool calling:
`nvidia/nemotron-3-super-120b-a12b:free`, `minimax/minimax-m3.1-flash:free`,
`google/gemma-4-26b-a4b-it:free`, `openrouter/free`.

## API

Salvo `/health`, todas las rutas viven bajo `/api` y aceptan **cookie de sesión**
o **token de API**.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado (sin auth) |
| POST | `/api/auth/login` | `{"password": "..."}` → cookie de sesión |
| POST | `/api/auth/logout` | Invalida la sesión |
| GET | `/api/auth/me` | Estado de autenticación |
| POST | `/api/chat` | `{"message": "...", "conversation_id": "..."}` |
| GET | `/api/conversations` | Lista de conversaciones |
| GET | `/api/conversations/{id}` | Mensajes de una conversación |
| GET | `/api/tools` | Herramientas permitidas |
| POST | `/api/tools/{name}` | Invoca una herramienta (widgets) |
| GET | `/api/llm/status` | Cadena de modelos y circuit breaker |
| GET | `/api/audit` | Auditoría de llamadas a herramientas |

## Criterios de aceptación (v0.2)

| Prueba | Resultado esperado |
|---|---|
| `/health` | 200 y `pwa: true` |
| `/` | `index.html` de la PWA |
| `POST /api/chat` sin sesión | 401 |
| Login correcto | Cookie `Secure` + `HttpOnly` y `/api/auth/me` autenticado |
| Login incorrecto | 401 |
| Logout | La sesión deja de valer (401) |
| Chat con LLM | Respuesta válida + `tool_trace` |
| Métricas | CPU/RAM/temperatura y 4 contenedores reales |
| Reinicio del agente | Historial SQLite conservado |
| Tailscale Serve | Solo accesible desde la tailnet |

## Endurecimiento (siguiente iteración)

1. **Socket de Docker**: interponer `tecnativa/docker-socket-proxy` (rechaza
   cualquier método no-GET) y retirar el montaje directo.
2. **Contenedor de tools**: `read_only`, `cap_drop: [ALL]`, usuario sin
   privilegios.
3. **Rotación de tokens** y revocación de sesiones desde Ajustes.
4. **Notificaciones Telegram** (opcional) para alertas del agente.
