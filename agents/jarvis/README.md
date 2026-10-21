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
| `filebrowser` | 8080 | `127.0.0.1:8080` -> Tailscale Serve HTTPS **y** proxy same-origin en `/files/` |

> Tailscale Serve proxya al **loopback del host**: los puertos deben estar
> publicados con `ports: "127.0.0.1:<p>:<p>"`. `expose:` solo documenta y no
> basta (produce 502).
>
> **FileBrowser también vive en `/files/`** (proxy del agente, mismo origen que
> la PWA): mismo TLS, misma sesión y sin segundo puerto. El puerto 8080 sigue
> existiendo para acceso directo. Portainer **no** admite subrutas, así que
> conserva su puerto propio.

## Almacenamiento y montaje

- El disco externo (HGST 750 GB, ext4) se monta en `/mnt/data` por **UUID**
  en `/etc/fstab`.
- `docker.service` tiene un drop-in que lo ata al montaje:
  `Requires=` + `After=` + `BindsTo=mnt-data.mount`. Si el disco no esta, los
  contenedores que dependen de `/mnt/data` no arrancan (ni escriben por
  error sobre la eMMC).
- 3 de 4 servicios dependen de `/mnt/data`: `jarvis-agent`, `portainer` y
  `filebrowser`. `jarvis-tools` no.
- **Nunca montes `/mnt/data` a mano**: systemd ya lo gestiona. Un `mount`
  manual apila un segundo montaje y el `umount` solo quita el de arriba,
  dejando el disco montado. Usa `systemctl start mnt-data.mount`.
- Comprobado de forma controlada: con el disco no disponible,
  `systemctl start docker` falla con *"A dependency job for docker.service
  failed"* y Docker **no arranca**.

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

## Despliegue y actualización

Un solo comando, con verificación de extremo a extremo:

```bash
cd agents/jarvis
./deploy-all.sh --full      # agente + ejecutor + PWA + verificación
./deploy-all.sh --agent     # solo el agente
./deploy-all.sh --executor  # solo el ejecutor
./deploy-all.sh --pwa       # solo la PWA (sin sudo)
./deploy-all.sh --check     # solo verifica: versión, /health, bundle, /files
./deploy-all.sh --test      # tests de agente y PWA, y luego --full
```

**Sudo: se pide una sola vez.** Cada `ssh` abre una sesión distinta y el ticket
de sudo no se comparte entre sesiones, así que todo lo que necesita sudo viaja
en **un único `ssh -t`** (comando como *argumento*, nunca por stdin: si se
ocupa stdin, ssh no puede darte la terminal para escribir la contraseña). Por
eso `deploy-all.sh` **debe ejecutarse desde tu terminal**, no por un pipe.

El script no dice "desplegado" sin comprobarlo: `jarvis-agent` debe reportar en
`/health` la versión y el bundle recién compilado, y el proxy `/files/` debe
responder 200. También `pwa/deploy.sh` (build + publicación en dos fases +
`--watch`) sigue disponible para solo la interfaz.

Desarrollo de la interfaz con recarga en caliente:

```bash
cd pwa && npm run dev
# Vite hace proxy de /api, /health y /files a http://localhost:8888
```

## Banco de pruebas

Cinco capas, de más rápida a más real. Ninguna necesita sudo.

```bash
# Agente (93): API, proxy /files, memoria, timeline y ramas del grafo
cd agent && python3 -m pytest

# Ejecutor (105 + 6 omitidas): policy engine, SSH, aprobaciones, auditoría
cd executor && python3 -m pytest

# PWA (47): componentes y reconstrucción del historial (vitest + jsdom)
cd pwa && npm run test

# Navegador real (14 + 1 omitida): iPhone 13 sobre Chromium
cd pwa && npm run test:e2e
```

**Y contra el servidor de verdad**, para medir lo que siente el usuario:

```bash
JARVIS_PASSWORD=... ./scripts/bench-chat.py            # desde el Mac, sin sudo
JARVIS_PASSWORD=... ./scripts/bench-chat.py --loop 5   # p50/p95
```

Mide tiempo al primer evento, primer token y cierre, cuántas herramientas usó y
si la respuesta llegó vacía. Falla (exit 1) si el stream no cierra, si no llega
texto o si el caso esperaba herramientas y no hubo: sirve también en un cron.
La contraseña va **solo** por entorno (nunca por argumento, que quedaría en el
historial del shell) y las conversaciones que crea se borran al terminar.

El banco E2E **no toca la placa**: simula el backend con interceptación de rutas,
así que comprueba la interfaz de verdad en segundos y sin contraseñas. Cubre el
streaming SSE (segmento → herramienta → tokens → cierre), el historial
restaurado con fechas y actividad agrupada, la exportación a Markdown, el enlace
`/files/` del mismo origen, el estado por host en Equipos y la actividad
reciente en Aprobaciones.

El único caso que habla con el servidor real es `e2e/integrado.spec.ts` (login,
aprobación, ejecución por SSH y auditoría). Se omite salvo que se le den las
credenciales por entorno — nunca por archivo:

```bash
JARVIS_E2E_URL=http://127.0.0.1:8889 JARVIS_E2E_PASSWORD=... \
  npx playwright test e2e/integrado.spec.ts
```

## Copias de seguridad

```bash
./deploy/backup.sh            # copia en la placa, la trae al Mac y verifica
./deploy/backup.sh --drill    # verifica la última copia (simulacro)
./deploy/backup.sh --list     # qué copias hay y su sha256
./deploy/backup.sh --restore  # instrucciones de restauración real
```

Sin sudo: lee las bases (son legibles) y nunca toca los contenedores. Tres
copias en tres máquinas:

| # | Dónde | Para qué |
|---|-------|----------|
| 1 | placa `/mnt/data/respaldos/` | origen, rotación de 14 |
| 2 | MacBook `~/respaldos/jarvis/` | segunda copia, y viaja contigo |
| 3 | Xeon `~/respaldos/jarvis/` | tercera copia, cuando está encendido |

Qué entra: las bases (agente, auditoría y FileBrowser), el `compose`, el
`.env` y los catálogos del ejecutor. El paquete queda con permisos 600 porque
contiene secretos.

**No es un `cp`.** Las bases están en WAL y el agente escribe mientras se
copian; se copia el trío `db`/`-wal`/`-shm` y se hace un checkpoint, así la
instantánea es coherente aunque haya un turno en curso.

**El simulacro comprueba algo de verdad.** Para cada base: `integrity_check`,
claves foráneas y el número de filas de cada tabla contra el manifiesto,
además del sha256 y el tamaño. Probado con paquetes dañados:

| Sabotaje | Resultado |
|---|---|
| paquete truncado | `el paquete está dañado` |
| base corrompida con hash del manifiesto reajustado | `database disk image is malformed` |
| `stack/.env` eliminado | `falta stack/.env` |

Una copia que no pasa el simulacro **no se queda en el Mac** ni se replica.

Restauración real (la haces tú, con el stack parado): `--restore` imprime los
comandos. Las bases viejas son compatibles: el agente migra el esquema al
arrancar.

Para que sea diaria, un LaunchAgent en el Mac o un `cron` de la placa llaman
al mismo script; no se instala nada sin pedirlo.

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

Seis secciones: **Chat**, **Métricas**, **Equipos**, **Archivos**,
**Aprobaciones**, **Ajustes**.

- Chat con memoria persistente, chips de herramientas y modelo usado.
- Métricas en vivo (CPU, RAM, temperatura, discos, carga, uptime) y
  contenedores; refresco cada 10 s.
- Equipos muestra el estado de los equipos remotos (y solo eso).
- Archivos enlaza a FileBrowser y Portainer (cada uno con su propio login).
- **Aprobaciones**: consulta, aprueba y rechaza las solicitudes que Jarvis
  necesita (ver *Fase C*). Refresco cada 10 s y al volver a enfocar;
  confirmación en dos pasos; cuenta atrás por solicitud; historial con
  estado, `exit code` y duración. Badge con el número de pendientes.
  Incluye la **actividad reciente** del ejecutor: lo aprobado se convierte
  en una ejecución con su resultado, denegación o error, en la misma
  pantalla (la consecuencia de lo que apruebas).
- Ajustes muestra la cadena de modelos, el circuit breaker y cierra sesión.
- Instalación: Safari → Compartir → **Añadir a pantalla de inicio**.
- Service worker: precachea solo los assets; **la API nunca se cachea**.

### Aprobaciones humanas (Fase C)

Flujo completo, con las fronteras de confianza bien separadas:

```
LLM ──▶ /exec (ejecutor, token del ejecutor)
          └─▶ Policy Engine: valida y NO ejecuta
              └─▶ solicitud PENDING (requested_by="agente")

PWA ──▶ /api/aprobaciones (agente: sesión humana + Origin + CSRF)
          ├─▶ aprobar  → el AGENTE pone approved_by (identidad de la sesión)
          └─▶ ejecutar → el EJECUTOR revalida, reclama (un solo uso) y ejecuta
```

- El **token CSRF** viaja en la cabecera `X-CSRF-Token`, es un HMAC ligado a
  la sesión y **solo vive en memoria** en el navegador: nunca en
  `localStorage` ni en una cookie legible por JavaScript.
- El **token del ejecutor** y el **de aprobaciones** no salen nunca del
  backend: el navegador solo habla con el agente.
- El LLM **no puede** aprobar, rechazar ni ejecutar: sus rutas exigen el
  token de aprobaciones, que el agente solo usa como proxy autenticado.
- `approved_by` lo deriva el **servidor** de la sesión; el navegador no puede
  enviarlo (se rechaza con 422).
- Una solicitud `failed` significa *resultado incierto*: **no se reintenta**.
- Una solicitud `consumed` ya se ejecutó: se informa, no se repite.

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
| POST | `/api/auth/login` | `{"password": "..."}` → cookie de sesión + token CSRF |
| POST | `/api/auth/logout` | Invalida la sesión |
| GET | `/api/auth/me` | Estado de autenticación (+ token CSRF si hay sesión) |
| POST | `/api/chat` | `{"message": "...", "conversation_id": "..."}` |
| POST | `/api/chat/stream` | Igual, pero responde **SSE** (`segment`, `token`, `tool`, `done`) |
| GET | `/api/conversations` | Lista de conversaciones |
| GET | `/api/conversations/{id}` | Mensajes de una conversación (con id, fecha y metadatos de herramientas) |
| DELETE | `/api/conversations/{id}` | Borra la conversación y sus mensajes (sesión + CSRF) |
| GET | `/api/tools` | Herramientas permitidas |
| POST | `/api/tools/{name}` | Invoca una herramienta (widgets) |
| GET | `/api/llm/status` | Cadena de modelos y circuit breaker |
| GET | `/api/audit` | Auditoría de llamadas a herramientas |
| GET | `/api/equipos` | Equipos registrados con su estado |
| GET | `/api/executions` | Actividad del ejecutor (sin sondear hosts) |
| * | `/files/*` | Proxy same-origin a FileBrowser (ver más abajo) |

El proxy `/files/*` reenvía a `filebrowser:8080/files/*` conservando el prefijo
(la cookie de FileBrowser tiene `Path=/files`) y sin reescribir rutas. No
propaga cabeceras hop-by-hop ni la cookie de sesión de Jarvis, y deja pasar
todas las `Set-Cookie` de FileBrowser. Exige la misma autenticación que el
resto de la API.

Rutas de aprobación (Fase C). Exigen **sesión de navegador** (el token de API
no basta) y, si cambian estado, además `Origin` estricto y `X-CSRF-Token`:

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/aprobaciones` | Solicitudes (filtro `?status=` y `?host=`) |
| GET | `/api/aprobaciones/{id}` | Detalle de una solicitud |
| POST | `/api/aprobaciones/{id}/aprobar` | Aprueba (la identidad la pone la sesión) |
| POST | `/api/aprobaciones/{id}/rechazar` | Rechaza (`{"reason": "..."}`) |
| POST | `/api/aprobaciones/{id}/ejecutar` | Ejecuta lo ya aprobado (sin cuerpo) |

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

## Fase B — Ejecutor multi-host (v0.3)

Servicio independiente `jarvis-executor` (FastAPI + asyncssh). Corre **en el
board** y alcanza los otros equipos por SSH. **No monta `docker.sock`**, corre
como `1000:1000`, con `cap_drop: ALL` y `read_only: true`.

```
   iPhone (PWA) → jarvis-agent ──HTTP interno──► jarvis-executor
                                                     │  Policy Engine
                                                     │  (deny by default)
                                                     ▼
                            macbook (SSH)   desktop (SSH)
                            100.126.29.56   (sin direccion: no esta en la tailnet)
```

### Herramientas

| Herramienta | Qué hace |
|---|---|
| `equipos_list` | Lista los equipos registrados y si están habilitados |
| `equipos_status` | Comprueba si un equipo responde (online/offline) |
| `equipos_ejecutar` | Ejecuta una operación **del catálogo** en un equipo |
| `equipos_historial` | Últimas ejecuciones auditadas |

### El LLM NO escribe comandos de shell

`equipos_ejecutar` acepta el **id de una operación del catálogo** y sus
parámetros tipados. El catálogo vive en `executor/config/catalog.yml`:

| Operación | Riesgo | Parámetros | Aprobación |
|---|---|---|---|
| `host_uname`, `host_uptime`, `host_disk_free`, `host_mem_free` | read | — | automática |
| `host_list_dir` | read | `path` | automática |
| `host_git_status` | read | `path` | automática |
| `host_git_log` | read | `path`, `count` (1..30) | automática |
| `host_git_diff_stat` | read | `path` | **humana** |
| `host_project_test` | execute | `project_id` | automática |

Lo que no está en el catálogo **no existe**: el Policy Engine lo deniega.

`host_git_diff_stat` (`git --no-optional-locks -C <ruta> diff --stat`) es la
operación de demostración del flujo de aprobación humana: es inocua (solo
lectura, sin hooks, sin escritura) pero **nunca** es automática. Al pedirla,
el ejecutor no ejecuta nada: abre una solicitud pendiente que solo una
persona autenticada puede aprobar.

### Policy Engine (deny by default)

Una operación solo se ejecuta si se cumplen **todas** estas reglas:

1. El host existe y está habilitado.
2. La operación está en el catálogo.
3. La operación es auto-aprobable **o** cuenta con la aprobación humana
   explícita. Si no es auto-aprobable, el Policy Engine la valida por
   completo pero **no** la ejecuta: abre una solicitud pendiente. Si
   cualquier otra regla falla, es un deniego normal y no hay nada que
   aprobar (una aprobación nunca convierte una operación prohibida en
   permitida).
4. La operación aplica a ese host.
5. Las rutas caen dentro de las `roots` del host, **resueltas en el host
   destino** (`readlink -f`), de modo que un symlink que apunte fuera se
   rechaza.
6. Los proyectos existen, están habilitados y pertenecen al host.

### SSH endurecido

- **Clave dedicada por host** (`id_ed25519_<host>`), nunca la del usuario.
- **Verificación estricta de host key**: `known_hosts` fijado de antemano con
  la clave real del equipo. Nunca TOFU: si la clave no está, no se conecta.
- **Usuario de sistema dedicado** (`jarvis`) en el equipo destino, no el
  usuario personal.
- **Forced command**: la clave del ejecutor lleva
  `command="/usr/local/libexec/jarvis-ssh-wrapper",restrict`. El wrapper es la
  última línea de defensa: parsea con `shlex` y solo permite el catálogo
  dentro de las raíces autorizadas. Rechaza `sh`, `rm`, rutas fuera de raíz y
  `env` en cualquier forma que no sea `-C <dir>`.
- **Sin shell**: el argv se cita con `shlex.quote` antes de enviarlo por el
  canal `exec` (que el shell remoto interpreta).
- **Sin reintentos duplicados**: solo se reintenta la *conexión*; una orden ya
  entregada no se repite.

### Pruebas

| Suite | Comando | Cobertura |
|---|---|---|
| Ejecutor | `cd executor && python3 -m pytest` | 93 pruebas: denegaciones, rutas, ejecución, SSH, aprobaciones (C1), operación con aprobación y flujo con git real (C3), integridad y estado incierto (C5) |
| Agente | `cd agent && python3 -m pytest` | 66 pruebas: CSRF, Origin, sesión humana, proxy de aprobaciones (C2), catálogo cerrado (C3), auditoría de seguridad (C5) |
| PWA | `cd pwa && npm test` | 41 pruebas: lógica de aprobaciones, componente y resultado incierto (Vitest + Testing Library) |
| PWA E2E | `cd pwa && npm run test:e2e` | 6 pruebas a 390×844 (Playwright, backend simulado) |
| Integrada | `scripts/prueba-integrada-fase-c.sh` | PWA real → agente → ejecutor → ejecución → auditoría, en entorno aislado |

Detalle de la cobertura del ejecutor:

- denegaciones: operación inexistente, host deshabilitado, operación no
  auto-aprobable, host no aplicable, proyecto de otro host, sin suite de tests
- rutas prohibidas: fuera de raíz, con `..`, relativas, symlink que escapa
- ejecución: comando inexistente, exit != 0, timeout, inyección de shell
  (`echo "a; id"` llega literal), **orden ejecutada una sola vez**
- SSH: host inalcanzable, sin `known_hosts`, sin clave, sin dirección
- aprobaciones: un solo uso, reclamo atómico, expiración, fila alterada,
  LLM sin capacidad de aprobar, `approval_id` rechazado con 422

### Estado del stack (auditoría C5, 2026-10-20)

**El stack de Jarvis está CAÍDO** y el despliegue de la Fase C tiene un
bloqueante previo:

| Comprobación | Resultado |
|---|---|
| `systemctl is-active docker` | `inactive` (pero `enabled`) |
| `/mnt/data` (disco externo de 698 GB) | **sin montar** (sí está en `fstab`) |
| `GET /health` del agente | sin respuesta (ni en loopback ni por la tailnet) |

Con el disco sin montar, Docker crearía volúmenes **vacíos** en la eMMC: el
ejecutor se quedaría sin su clave SSH dedicada (que solo vive en ese disco)
y se perdería la auditoría. El procedimiento completo, con copia de
seguridad verificable, rotación de sudo, tokens, rebuild, health checks, E2E,
validación de auditoría y rollback, está en
[`docs/DESPLIEGUE-C.md`](docs/DESPLIEGUE-C.md).

### Pendiente

- **PC Xeon (Linux Mint)**: no aparece en la tailnet. El registro lo tiene
  como `desktop` **deshabilitado** hasta que se configure su dirección real.
- **Aprobación humana**: implementada y probada (C1 ejecutor, C2 proxy
  autenticado del agente, C3 operación de demostración `host_git_diff_stat`,
  C4 pestaña «Aprobaciones» de la PWA) pero **sin desplegar**: faltan el
  token de aprobaciones en el `.env` del board, reconstruir los contenedores
  y compilar la PWA.
- **Safari real**: la PWA se ha verificado en Chromium a 390×844; WebKit no
  arranca en este equipo (faltan librerías del sistema que requieren sudo).
  El código evita los patrones problemáticos en Safari (`dvh`, safe-area,
  prefijos `-webkit-`).
- **Segunda ubicación de respaldos**: hoy `/mnt/data/nas/backups` está en el
  mismo disco HGST que los datos: protege de errores lógicos, no de la
  fallo del disco.

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
