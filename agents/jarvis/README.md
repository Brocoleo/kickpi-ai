# Jarvis v0.2

Asistente privado para la **KICKPI K2B V2**: cerebro con **FastAPI + LangGraph +
SQLite**, PWA mobile-first para iPhone y herramientas de **solo lectura** sobre
el servidor. Inferencia en la nube (OpenRouter / cualquier API compatible).

## Arquitectura

```
   iPhone (PWA instalada) - MacBook (SSH) - navegador
         |  HTTPS, mismo origen (Tailscale)
         v
   Tailscale Serve (host-level)
     :8888 -> jarvis-agent     :8443 -> filebrowser     / -> portainer
         |
   +------------------+   HTTP interno    +----------------------+
   |   jarvis-agent   | ---------------> |   jarvis-tools       |
   |   FastAPI + PWA  |  X-Tools-Token   |   psutil + Docker    |
   |   LangGraph      |                  +-----------+----------+
   |   SQLite         |                              | tcp://:2375
   +--------+---------+                              v
            | HTTPS                    +------------------------------+
            v                          |  docker-socket-proxy (haproxy)|
     OpenRouter / LLM                  |  ACL por endpoint exacto     |
                                       +--------------+---------------+
                                                      | /var/run/docker.sock
                                                      v
                        +-----------------------------+--------------+
                        |  Docker                                    |
                        |  jarvis-agent jarvis-tools portainer  fb  |
                        +-----------------------------+--------------+
                                                      v
                            /mnt/data  (HDD 750 GB, ext4, UUID en fstab)
                            |-- docker/...    datos persistentes
                            `-- nas/files/    raiz del FileBrowser
```

- **jarvis-agent** no ve Docker ni el sistema: solo habla con `jarvis-tools`.
- **jarvis-tools** **no monta `docker.sock`**: habla con el proxy por TCP.
- **docker-socket-proxy** es el unico que monta el socket (junto a Portainer,
  que necesita la API completa) y aplica una **ACL por endpoint exacto**.
- La **PWA se sirve desde el propio agente** (`/`), asi que la API (`/api/*`) y
  la interfaz comparten origen: **cero CORS** y una sola superficie expuesta.

## Servicios y puertos

| Servicio | Puerto | Exposicion |
|---|---|---|
| `jarvis-agent` | 8888 | `127.0.0.1:8888` -> Tailscale Serve HTTPS |
| `jarvis-tools` | 8000 | interna en la red `jarvis` (no publicada) |
| `docker-socket-proxy` | 2375 | interna en la red `dsp` (no publicada, sin salida a internet) |
| `portainer` | 9443 | `127.0.0.1:9443` -> Tailscale Serve HTTPS |
| `filebrowser` | 8080 | `127.0.0.1:8080` -> Tailscale Serve HTTPS |

> Tailscale Serve proxya al **loopback del host**: los puertos deben estar
> publicados con `ports: "127.0.0.1:<p>:<p>"`. `expose:` solo documenta y no
> basta (produce 502).

## Almacenamiento y montaje

- El disco externo (HGST 750 GB, ext4) se monta en `/mnt/data` por **UUID**
  en `/etc/fstab`.
- `docker.service` tiene un drop-in que lo ata al montaje:
  `Requires=` + `After=` + `BindsTo=mnt-data.mount`. Si el disco no esta, los
  contenedores que dependen de `/mnt/data` no arrancan (ni escriben por
  error sobre la eMMC).
- 3 de 4 servicios dependen de `/mnt/data`: `jarvis-agent`, `portainer` y
  `filebrowser`. `jarvis-tools` no.

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
| `docker_ps` | Contenedores: estado, salud, puertos, imagen |
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
| Métricas | CPU/RAM/temperatura y 5 contenedores reales |
| Reinicio del agente | Historial SQLite conservado |
| Tailscale Serve | Solo accesible desde la tailnet |

## Endurecimiento (Fase A aplicada)

### 1. Socket de Docker: proxy con ACL por endpoint

`tecnativa/docker-socket-proxy:v0.5.0` (arm64) con config propia en
`deploy/docker-socket-proxy/haproxy.cfg`. La ACL de la imagen
(`CONTAINERS=1`) es un match de **prefijo** y permite
`GET /containers/{id}/json` (inspect), que devuelve `Config.Env` con los
secretos de los otros contenedores. Aqui la ACL es exacta:

| Endpoint | Permitido | Uso |
|---|---|---|
| `GET /version` | si | negociacion del SDK |
| `GET /_ping` | si | health del cliente |
| `GET /containers/json` | si | listar contenedores |
| `GET /containers/{n}/logs` | si | tool `container_logs` |
| `GET /containers/{n}/stats` | si | tool `container_stats` |
| `GET /containers/{n}/json` | **no** | *inspect = fuga de `Config.Env`* |
| `POST`/`DELETE` (cualquiera) | **no** | toda mutacion de la API Docker |
| `/info`, `/images`, `/volumes`, `/exec`, `/events` | **no** | no necesarios |

Por eso `tools/app/docker_api.py` **no usa el cliente de alto nivel de
docker-py** (`containers.list()` y `containers.get()` inspeccionan siempre):
habla la API de Docker directamente sobre los tres endpoints permitidos.
Consecuencia: `docker_ps` no puede devolver `started_at`/`restart_count`
(requieren inspect); el resto de campos (`name`, `image`, `state`, `health`,
`ports`) se derivan de `/containers/json`.

### 2. Proteccion del montaje

Drop-in en `/etc/systemd/system/docker.service.d/override.conf`:
`Requires=` + `After=` + `BindsTo=mnt-data.mount`.

### 3. FileBrowser sin soporte

`filebrowser/filebrowser` (upstream) esta **archivado** desde 2026-09-01.
Migrado a **FileBrowser Quantum `1.5.8-stable-slim`** (fork mantenido,
Apache-2.0, arm64), que conserva **BoltDB**: el usuario `admin` y su hash
bcrypt se mantienen, sin perdida. Config en `deploy/filebrowser/config.yaml`.
Rollback: `/mnt/data/docker/filebrowser/{cfg,db}` (BD original) y
`/mnt/data/nas/backups/filebrowser-pre-migration-*/`.

### Pendiente

1. **Volumenes huerfanos** (`filebrowser_cfg`, `filebrowser_db`,
   `filebrowser_srv`, `portainer_data`): ya no se montan, pero siguen
   ocupando espacio. No se borran sin autorizacion explicita.
2. **Rotacion de tokens** y revocacion de sesiones desde Ajustes.
3. **Notificaciones Telegram** (opcional) para alertas del agente.
4. **`jarvis-executor`** para ejecucion remota multi-host con motor de
   politicas y aprobaciones.
