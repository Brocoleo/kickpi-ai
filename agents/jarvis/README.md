# Jarvis Agent v0.1

Cerebro del asistente: **FastAPI + LangGraph + SQLite**, con inferencia en la
nube (OpenRouter / OpenAI / cualquier API compatible). Solo **herramientas de
lectura**; no puede reiniciar, crear, borrar ni modificar nada.

## Arquitectura

```
    iPhone / MacBook
          │  (Tailscale + token de API)
          ▼
   ┌─────────────────┐        HTTP interno        ┌──────────────────┐
   │  jarvis-agent   │ ─────────────────────────► │  jarvis-tools    │
   │  FastAPI        │      X-Tools-Token         │  FastAPI         │
   │  LangGraph      │                            │  psutil + docker │
   │  SQLite         │                            └────────┬─────────┘
   └────────┬────────┘                                     │
            │ HTTPS                                        │ /var/run/docker.sock
            ▼                                              ▼
   OpenRouter / Google AI                             Docker (SOLO lectura)
```

- **jarvis-agent** no ve Docker ni el sistema: solo habla con `jarvis-tools`.
- **jarvis-tools** es el único con el socket de Docker y solo expone `GET`.

## Servicios y puertos

| Servicio | Puerto | Exposición |
|---|---|---|
| `jarvis-agent` | 8888 | `127.0.0.1:8888` (solo local) |
| `jarvis-tools` | 8000 | interna en la red `jarvis` (no publicada) |

El agente **no** se publica a la tailnet hasta validar seguridad y pruebas.

## Puesta en marcha

```bash
cd agents/jarvis
cp .env.example .env

# Genera dos tokens distintos
openssl rand -hex 32   # -> JARVIS_API_TOKEN
openssl rand -hex 32   # -> JARVIS_TOOLS_TOKEN

# Edita .env y añade tu clave del LLM (JARVIS_LLM_API_KEY)
sudo docker compose up -d --build
```

## Herramientas disponibles (solo lectura)

| Herramienta | Qué devuelve |
|---|---|
| `system_stats` | CPU (total y por núcleo), carga, temperatura, RAM, swap, uptime |
| `disk_usage` | Uso de disco de `/`, `/mnt/data`, `/data` |
| `docker_ps` | Contenedores: estado, salud, puertos, imagen, reinicios |
| `container_logs` | Últimas N líneas, saneadas (secretos redactados, tamaño acotado) |

## Fallback de modelos (disponibilidad sin coste)

El agente mantiene una **cadena de modelos**: `JARVIS_LLM_MODEL` y luego
`JARVIS_LLM_FALLBACK_MODELS` (separados por comas). Política:

| Situación | Comportamiento |
|---|---|
| HTTP 429 | Sin reintento → siguiente modelo |
| HTTP 5xx | Reintento breve con backoff → siguiente modelo |
| Timeout / error de red | Siguiente modelo |
| HTTP 401/403 | **Detener** (credenciales mal configuradas) |
| Respuesta válida | Se mantiene y se devuelve ese modelo |
| Todos fallan | `LLMError` controlado con resumen |

**Circuit breaker:** tras 2 fallos consecutivos, un modelo queda en
enfriamiento 60 s y se salta. Al terminar, se permite un intento de prueba.
Si *todos* están abiertos, se fuerza el primero para no quedarnos sin servicio.

**Sin acciones duplicadas:** el fallback solo repite la llamada al modelo.
Los mensajes y las `tool_calls` no se reenvían; las herramientas se ejecutan
una única vez en el nodo `tools` del grafo.

**Límites de los `:free`:** OpenRouter aplica ~20 req/min y un tope diario
según la cuenta. Verificar disponibilidad con:

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import sys,json; [print(m['id']) for m in json.load(sys.stdin)['data'] if float(m['pricing']['prompt'])==0 and 'tools' in (m.get('supported_parameters') or [])]"
```

Modelos gratuitos verificados con tool calling: `nvidia/nemotron-3-super-120b-a12b:free`,
`minimax/minimax-m3.1-flash:free`, `google/gemma-4-26b-a4b-it:free`, `openrouter/free`.

## API

Todas las rutas salvo `/health` exigen `X-API-Token: <JARVIS_API_TOKEN>`
(o `Authorization: Bearer <token>`).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado (sin auth) |
| POST | `/chat` | `{"message": "...", "conversation_id": "..."}` |
| GET | `/conversations` | Lista de conversaciones |
| GET | `/conversations/{id}` | Mensajes de una conversación |
| GET | `/tools` | Herramientas permitidas |
| GET | `/llm/status` | Cadena de modelos y estado del circuit breaker |
| GET | `/audit` | Auditoría de llamadas a herramientas |

## Criterios de aceptación (Fase 3)

| Prueba | Resultado esperado | Cómo |
|---|---|---|
| `/health` | 200 | `curl -s localhost:8888/health` |
| Sin token | 401 | `curl -s -o /dev/null -w '%{http_code}' localhost:8888/chat -X POST -d '{}'` |
| Chat con LLM | Respuesta válida | `POST /chat` con token |
| `system_stats` | CPU y RAM reales | vía chat o `GET /docker`… |
| `docker_ps` | Contenedores reales | vía chat |
| `container_logs` | Logs acotados y sin secretos | vía chat |
| Reinicio del agente | Memoria SQLite conservada | `docker compose restart jarvis-agent` + `GET /conversations/{id}` |
| Acceso remoto | Deshabilitado hasta validar | puerto solo en 127.0.0.1 |

## Endurecimiento (siguiente iteración)

1. **Socket de Docker**: un bind-mount de unix socket no se puede hacer
   *read-only* de verdad. La mitigación actual es que `jarvis-tools` solo
   implementa operaciones de lectura. Paso siguiente: interponer
   `tecnativa/docker-socket-proxy` (que sí rechaza cualquier método no-GET).
2. **Aislamiento del contenedor de tools**: `read_only`, `cap_drop: [ALL]`,
   usuario sin privilegios en cuanto el proxy sustituya al socket directo.
3. **Rate limiting** y rotación de tokens.
4. **Notificaciones Telegram** (opcional) para alertas del agente.
