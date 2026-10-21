"""API del agente Jarvis + PWA servida en el mismo origen.

Rutas:
  /health              → monitorización (sin auth)
  /api/auth/*          → login / logout / sesión
  /api/chat, /api/...  → requieren token de API o cookie de sesión
  /                    → PWA compilada (React + Vite)
"""
import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from . import approvals_client, memory, sessions
from .approvals_client import ApprovalsError
from .auth import (
    auth_method,
    require_auth,
    require_human_mutation,
    require_human_session,
    session_fingerprint_of,
)
from .config import SYSTEM_PROMPT, get_settings
from .csrf import check_origin, issue_token
from .db import init_db
from .graph import AGENT
from .llm import CIRCUIT, STREAM_EMIT
from .security import (
    check_admin_password,
    login_allowed,
    new_session_token,
    record_login_failure,
    reset_login_failures,
    session_fingerprint,
)
from .executor_client import ExecutorError, call_executor
from .tools_client import TOOL_SPECS, ToolsError, call_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis-agent")

VERSION = "0.5.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(init_db)
    settings = get_settings()
    if not settings.api_token:
        log.warning("JARVIS_API_TOKEN vacío: los endpoints protegidos darán 503")
    if not settings.admin_password_hash:
        log.info("Sin JARVIS_ADMIN_PASSWORD_HASH: el login usará JARVIS_API_TOKEN")
    if not settings.llm_configured:
        log.warning("JARVIS_LLM_API_KEY vacía: /api/chat devolverá un aviso")
    log.info("Jarvis Agent %s listo (modelo=%s)", VERSION, settings.llm_model)
    yield


app = FastAPI(title="Jarvis Agent", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def headers_middleware(request: Request, call_next):
    """Cabeceras de seguridad + no-store para la API y el HTML."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    # El proxy de FileBrowser trae su propio HTML/JS (Vue compilado con
    # scripts inline). Le imponemos la CSP estricta de Jarvis solo si no
    # trae la suya, para no romper su interfaz.
    if not request.url.path.startswith("/files"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; manifest-src 'self'; "
            "worker-src 'self' blob:; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        response.headers["Cache-Control"] = "no-store"
    else:
        # El "shell" de la PWA (HTML, service worker, manifest) NUNCA se
        # cachea: si se cachea, el iPhone sigue abriendo un build viejo.
        # Los assets llevan hash en el nombre, así que son inmutables.
        path = request.url.path
        if path in ("/", "/index.html") or path.endswith(
            ("/sw.js", "/manifest.webmanifest")
        ):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        elif path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    tool_trace: list[dict] = []
    model: str | None = None


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------------- #
# Salud
# --------------------------------------------------------------------------- #
_ASSET_RE = re.compile(r"index-[A-Za-z0-9_-]+\.js")


def _static_asset() -> str | None:
    """Nombre del bundle JS que la PWA sirve ahora mismo.

    Permite detectar builds viejos: el script de despliegue compara este
    valor con el bundle recién compilado y falla si no coinciden.
    """
    index = Path(get_settings().static_dir) / "index.html"
    try:
        match = _ASSET_RE.search(index.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(0) if match else None


@app.get("/health", tags=["meta"])
async def health() -> dict:
    settings = get_settings()
    tools_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.tools_url}/health")
            tools_ok = r.status_code == 200
    except httpx.HTTPError:
        tools_ok = False

    return {
        "status": "ok",
        "service": "jarvis-agent",
        "version": VERSION,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "tools_reachable": tools_ok,
        "api_token_configured": bool(settings.api_token),
        "pwa": bool(Path(settings.static_dir).is_dir()),
        "static_asset": _static_asset(),
    }


# --------------------------------------------------------------------------- #
# Autenticación de navegador
# --------------------------------------------------------------------------- #
@app.post("/api/auth/login", tags=["auth"])
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    settings = get_settings()

    # Login es una operacion que cambia estado (crea una sesion): se valida
    # el origen para que una pagina ajena no pueda iniciar sesion en el
    # navegador del administrador.
    check_origin(request)

    if not login_allowed():
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos.")

    if not check_admin_password(body.password):
        record_login_failure()
        log.warning("login fallido")
        raise HTTPException(401, "Contraseña incorrecta")

    reset_login_failures()
    token = new_session_token()
    fingerprint = session_fingerprint(token)
    await asyncio.to_thread(
        sessions.create, fingerprint, settings.session_ttl_days, settings.admin_user
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    # El token CSRF viaja en el cuerpo de la respuesta (queda en memoria en
    # el navegador, nunca en localStorage) y esta ligado a ESTA sesion.
    csrf_token = issue_token(fingerprint)
    log.info("login correcto (sesión de %d días)", settings.session_ttl_days)
    return {
        "ok": True,
        "expires_days": settings.session_ttl_days,
        "csrf_token": csrf_token,
    }


@app.post("/api/auth/logout", tags=["auth"])
async def logout(request: Request, response: Response) -> dict:
    settings = get_settings()
    check_origin(request)
    token = request.cookies.get(settings.cookie_name)
    if token:
        await asyncio.to_thread(
            sessions.delete, session_fingerprint(token)
        )
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@app.get("/api/auth/me", tags=["auth"])
async def me(request: Request) -> dict:
    settings = get_settings()
    method = auth_method(request)
    payload = {
        "authenticated": method is not None,
        "method": method,
        "password_configured": bool(settings.admin_password_hash),
        "session_ttl_days": settings.session_ttl_days,
        "version": VERSION,
    }
    # El token CSRF se entrega de nuevo tras recargar la PWA: es la misma
    # sesion, asi que el token es identico (HMAC de la huella de sesion).
    if method == "session":
        fingerprint = session_fingerprint_of(request)
        payload["csrf_token"] = issue_token(fingerprint) if fingerprint else ""
        payload["user"] = sessions.subject(fingerprint) if fingerprint else None
    return payload


# --------------------------------------------------------------------------- #
# Meta (protegido)
# --------------------------------------------------------------------------- #
@app.get("/api/tools", tags=["meta"], dependencies=[Depends(require_auth)])
async def list_tools() -> dict:
    return {
        "read_only": True,
        "tools": [t["function"]["name"] for t in TOOL_SPECS],
        "specs": TOOL_SPECS,
    }


# --------------------------------------------------------------------------- #
# Equipos (ejecutor multi-host)
# --------------------------------------------------------------------------- #
@app.get("/api/equipos", tags=["equipos"], dependencies=[Depends(require_auth)])
async def list_equipos() -> dict:
    """Equipos registrados con su estado y las últimas ejecuciones."""
    try:
        hosts = await call_executor("equipos_list")
    except ExecutorError as exc:
        raise HTTPException(502, str(exc)) from exc

    # Estado en paralelo para no encadenar latencias.
    items = []
    host_list = hosts.get("hosts") or []
    statuses = await asyncio.gather(
        *[call_executor("equipos_status", {"host": h.get("id")}) for h in host_list],
        return_exceptions=True,
    )
    for host, status in zip(host_list, statuses):
        entry = {**host}
        if isinstance(status, Exception):
            entry["online"] = False
            entry["reason"] = str(status)
        else:
            entry.update(status)
        items.append(entry)

    try:
        history = await call_executor("equipos_historial", {"limit": 20})
    except ExecutorError:
        history = {"executions": []}

    return {
        "hosts": items,
        "executions": history.get("executions") or [],
        "count": len(items),
    }


@app.get("/api/llm/status", tags=["meta"], dependencies=[Depends(require_auth)])
async def llm_status() -> dict:
    """Cadena de modelos, estado del circuit breaker y política de fallback."""
    settings = get_settings()
    return {
        "configured": settings.llm_configured,
        "base_url": settings.llm_base_url,
        "models": settings.llm_model_chain,
        "circuit_breaker": CIRCUIT.snapshot(),
        "policy": {
            "429": "sin reintento -> siguiente modelo",
            "5xx": f"reintento breve (max {settings.llm_max_retries_server}) -> siguiente modelo",
            "timeout/red": "siguiente modelo",
            "401/403": "detener (error de credenciales)",
            "circuit_threshold": settings.llm_circuit_threshold,
            "circuit_cooldown_s": settings.llm_circuit_cooldown,
            "total_budget_s": settings.llm_total_budget,
        },
    }


# --------------------------------------------------------------------------- #
# Aprobaciones humanas (proxy autenticado hacia el ejecutor)
# --------------------------------------------------------------------------- #
# Separación estricta respecto a las herramientas del LLM:
#   - las tools `equipos_*` van al ejecutor con X-Executor-Token y NUNCA
#     pueden alcanzar estas rutas;
#   - estas rutas exigen SESIÓN DE NAVEGADOR (no vale el token de API) y,
#     si cambian estado, además Origin estricto + token CSRF ligado a la
#     sesión;
#   - el token de aprobaciones del ejecutor no sale nunca del backend.
# --------------------------------------------------------------------------- #


class RejectBody(BaseModel):
    """El navegador NO puede enviar la identidad: esa la deriva la sesión."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class EmptyBody(BaseModel):
    """Cuerpo vacío: impide ejecutar una aprobación con otros parámetros."""

    model_config = ConfigDict(extra="forbid")


@app.get("/api/aprobaciones", tags=["aprobaciones"],
         dependencies=[Depends(require_human_session)])
async def list_aprobaciones(
    status_filter: str | None = Query(default=None, alias="status"),
    host: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Solicitudes de aprobación visibles para el humano autenticado."""
    try:
        data = await approvals_client.list_approvals(status_filter, host, limit)
    except ApprovalsError as exc:
        raise approvals_client.to_http_error(exc) from exc
    return {
        "approvals": data.get("approvals") or [],
        "count": data.get("count", 0),
    }


@app.get("/api/aprobaciones/{approval_id}", tags=["aprobaciones"],
         dependencies=[Depends(require_human_session)])
async def get_aprobacion(approval_id: str) -> dict:
    try:
        data = await approvals_client.get_approval(approval_id)
    except ApprovalsError as exc:
        raise approvals_client.to_http_error(exc) from exc
    return data


@app.post("/api/aprobaciones/{approval_id}/aprobar", tags=["aprobaciones"])
async def aprobar_aprobacion(
    approval_id: str,
    body: EmptyBody | None = None,
    subject: str = Depends(require_human_mutation),
) -> dict:
    """Aprueba una solicitud. La identidad la pone la SESIÓN, no el
    navegador: el cuerpo de la petición no puede contener `approved_by`
    (ni nada más: se rechaza con 422)."""
    started = time.monotonic()
    try:
        data = await approvals_client.approve(approval_id, subject)
    except ApprovalsError as exc:
        raise approvals_client.to_http_error(exc) from exc

    await asyncio.to_thread(
        memory.add_audit,
        "aprobacion:aprobar",
        {"approval_id": approval_id},
        True,
        int((time.monotonic() - started) * 1000),
        f"aprobada por {subject}",
        None,
    )
    return data


@app.post("/api/aprobaciones/{approval_id}/rechazar", tags=["aprobaciones"])
async def rechazar_aprobacion(
    approval_id: str,
    background: BackgroundTasks,
    body: RejectBody | None = None,
    subject: str = Depends(require_human_mutation),
) -> dict:
    """Rechaza una solicitud (la identidad también la pone la sesión)."""
    started = time.monotonic()
    reason = body.reason if body else ""
    try:
        data = await approvals_client.reject(approval_id, subject, reason)
    except ApprovalsError as exc:
        raise approvals_client.to_http_error(exc) from exc

    await asyncio.to_thread(
        memory.add_audit,
        "aprobacion:rechazar",
        {"approval_id": approval_id, "reason": reason},
        True,
        int((time.monotonic() - started) * 1000),
        f"rechazada por {subject}",
        None,
    )
    # Cierra el turno en el chat: Jarvis explica que no se ejecuto nada.
    background.add_task(
        _resume_after_decision,
        approval_id,
        _rejection_payload(approval_id, reason),
    )
    return data


@app.post("/api/aprobaciones/{approval_id}/ejecutar", tags=["aprobaciones"])
async def ejecutar_aprobacion(
    approval_id: str,
    background: BackgroundTasks,
    body: EmptyBody | None = None,
    subject: str = Depends(require_human_mutation),
) -> dict:
    """Ejecuta una solicitud ya aprobada.

    El cuerpo DEBE estar vacío: los parámetros los toma el ejecutor de lo
    persistido y verificado. Un solo uso garantizado por el ejecutor (reclamo
    atómico); si ya se consumió, responde 409.
    """
    started = time.monotonic()
    try:
        data = await approvals_client.execute(approval_id)
    except ApprovalsError as exc:
        raise approvals_client.to_http_error(exc) from exc

    await asyncio.to_thread(
        memory.add_audit,
        "aprobacion:ejecutar",
        {"approval_id": approval_id},
        bool(data.get("ok")),
        int((time.monotonic() - started) * 1000),
        f"ejecutada por {subject}: {data.get('state')}",
        None,
    )
    # Continua la conversacion que origino la solicitud con el resultado real.
    background.add_task(
        _resume_after_decision, approval_id, _execution_payload(data)
    )
    return data


# --------------------------------------------------------------------------- #
# Herramientas (widgets de la PWA)
# --------------------------------------------------------------------------- #
@app.post(
    "/api/tools/{tool_name}", tags=["tools"], dependencies=[Depends(require_auth)]
)
async def invoke_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Invoca una herramienta de solo lectura directamente.

    Sirve para validar el camino agente→tools sin depender del LLM y alimenta
    los widgets de métricas y contenedores de la PWA.
    """
    names = {t["function"]["name"] for t in TOOL_SPECS}
    if tool_name not in names:
        raise HTTPException(404, f"Herramienta no encontrada: {tool_name}")

    args = arguments or {}
    started = time.monotonic()
    try:
        result = await call_tool(tool_name, args)
        ok = True
    except ToolsError as exc:
        result = {"error": str(exc)}
        ok = False
    duration = int((time.monotonic() - started) * 1000)

    await asyncio.to_thread(
        memory.add_audit, tool_name, args, ok, duration, "", None
    )
    if not ok:
        raise HTTPException(502, result["error"])
    return result


# --------------------------------------------------------------------------- #
# Conversación
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Persistencia de un turno (compartida por /api/chat y /api/chat/stream)
# --------------------------------------------------------------------------- #
def _pending_approval_id(content: str | None) -> str | None:
    """Extrae el id de aprobacion de un resultado de herramienta pendiente."""
    if not content or "pending_approval" not in content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("state") != "pending_approval":
        return None
    approval = payload.get("approval") or {}
    if not isinstance(approval, dict):
        return None
    approval_id = approval.get("id")
    return approval_id if isinstance(approval_id, str) and approval_id else None


def _persist_turn_sync(conv_id: str, base_len: int, result: dict) -> str:
    """Persiste los mensajes nuevos de un turno y devuelve el texto final.

    Recibe el estado final del grafo. Los resultados de herramientas que
    terminan en `pending_approval` se enlazan con la conversacion para poder
    reanudarla cuando el humano decida.
    """
    final = result["messages"][-1]
    reply = final.get("content") or ""
    # Nunca devolver una burbuja vacía: si el modelo no produjo texto (fallo
    # del proveedor, respuesta vacía, etc.) se muestra un mensaje explícito
    # y se persiste con ese texto para que el historial sea coherente.
    if not reply.strip():
        if final.get("role") == "assistant" and not final.get("tool_calls"):
            reply = (
                "El modelo no devolvió texto en esta ocasión. "
                "Puede ser un fallo transitorio del proveedor gratuito: "
                "reintenta la petición o reformúlala."
            )
            final["content"] = reply
        else:
            reply = "No obtuve una respuesta final del agente. Reintenta la petición."

    # Persistimos solo lo generado en este turno (respuestas intermedias,
    # llamadas y resultados), para conservar el contexto completo.
    for msg in result["messages"][base_len:]:
        memory.add_message(
            conv_id,
            msg.get("role", "assistant"),
            msg.get("content"),
            msg.get("tool_calls"),
            msg.get("tool_call_id"),
            msg.get("name"),
        )
        if msg.get("role") == "tool":
            approval_id = _pending_approval_id(msg.get("content"))
            if approval_id:
                memory.link_approval(approval_id, conv_id, msg.get("tool_call_id"))
    memory.touch_conversation(conv_id)
    return reply


def _prepare_chat(body: ChatRequest) -> tuple[str, list[dict]]:
    """Valida/crea la conversacion y guarda el mensaje del usuario.

    Devuelve (conversation_id, base_messages) donde base_messages ya incluye
    el system prompt y el historial completo.
    """
    settings = get_settings()
    conv_id = body.conversation_id
    if conv_id and not memory.conversation_exists(conv_id):
        raise HTTPException(404, "Conversación no encontrada")
    if not conv_id:
        conv_id = memory.create_conversation(body.message[:60])
    memory.add_message(conv_id, "user", body.message)
    history = memory.get_messages(conv_id, settings.history_limit)
    return conv_id, [{"role": "system", "content": SYSTEM_PROMPT}] + history


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    tags=["chat"],
    dependencies=[Depends(require_auth)],
)
async def chat(body: ChatRequest) -> ChatResponse:
    conv_id, base_messages = await asyncio.to_thread(_prepare_chat, body)
    state = {
        "messages": base_messages,
        "tool_trace": [],
        "iterations": 0,
        "conversation_id": conv_id,
    }

    result = await AGENT.ainvoke(state)
    reply = await asyncio.to_thread(
        _persist_turn_sync, conv_id, len(base_messages), result
    )

    return ChatResponse(
        conversation_id=conv_id,
        reply=reply,
        tool_trace=result.get("tool_trace", []),
        model=result.get("model_used"),
    )


@app.post(
    "/api/chat/stream",
    tags=["chat"],
    dependencies=[Depends(require_auth)],
)
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """Chat en streaming (SSE).

    Eventos: `segment` (nueva burbuja), `token` (texto incremental),
    `tool` (herramienta terminada), `done` (turno persistido) y `error`.
    """
    conv_id, base_messages = await asyncio.to_thread(_prepare_chat, body)
    state = {
        "messages": base_messages,
        "tool_trace": [],
        "iterations": 0,
        "conversation_id": conv_id,
    }

    async def event_source():
        queue: asyncio.Queue = asyncio.Queue()
        final_state: dict | None = None

        def emit(event: dict) -> None:
            queue.put_nowait(event)

        async def run_graph() -> None:
            try:
                async for snapshot in AGENT.astream(state, stream_mode="values"):
                    await queue.put({"event": "__state", "state": snapshot})
            except Exception as exc:  # noqa: BLE001 - se informa al cliente
                log.exception("chat en streaming fallo")
                await queue.put({"event": "__error", "detail": str(exc)})
            finally:
                await queue.put({"event": "__end"})

        # El ContextVar lo heredan las tareas que crea el grafo.
        token = STREAM_EMIT.set(emit)
        task = asyncio.create_task(run_graph())
        try:
            while True:
                event = await queue.get()
                kind = event.get("event")
                if kind == "__state":
                    final_state = event["state"]
                    continue
                if kind == "__error":
                    yield _sse("error", {"detail": event["detail"]})
                    break
                if kind == "__end":
                    break
                yield _sse(kind, event)

            if final_state is not None:
                reply = await asyncio.to_thread(
                    _persist_turn_sync, conv_id, len(base_messages), final_state
                )
                yield _sse(
                    "done",
                    {
                        "conversation_id": conv_id,
                        "reply": reply,
                        "tool_trace": final_state.get("tool_trace", []),
                        "model": final_state.get("model_used"),
                    },
                )
        finally:
            STREAM_EMIT.reset(token)
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            # Evita que proxies intermedios acumulen el stream.
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Reanudar la conversacion cuando el humano decide
# --------------------------------------------------------------------------- #
def _execution_payload(data: dict) -> dict:
    stdout = data.get("stdout") or ""
    if len(stdout) > 12000:
        stdout = stdout[:12000] + "\n…(salida recortada)"
    stderr = data.get("stderr") or ""
    if len(stderr) > 2000:
        stderr = stderr[:2000] + "\n…(recortado)"
    return {
        "ok": bool(data.get("ok")),
        "state": data.get("state"),
        "approval_id": data.get("approval_id"),
        "host": data.get("host"),
        "operation": data.get("operation"),
        "exit_code": data.get("exit_code"),
        "stdout": stdout,
        "stderr": stderr or None,
        "duration_ms": data.get("duration_ms"),
        "error": data.get("error"),
        "note": "resultado real devuelto por el ejecutor tras la aprobación humana",
    }


def _rejection_payload(approval_id: str, reason: str) -> dict:
    return {
        "ok": False,
        "state": "rejected",
        "approval_id": approval_id,
        "error": reason or "el usuario rechazó la solicitud",
        "note": "la operación NO se ejecutó: el humano la rechazó",
    }


async def _resume_after_decision(approval_id: str, payload: dict) -> None:
    """Continua el grafo con el resultado de la aprobacion.

    El turno original termino con un tool-message "pending_approval". Aqui se
    sustituye ese mensaje por el resultado real y se vuelve a invocar al
    agente para que redacte la respuesta final en la misma conversacion.
    """
    link = await asyncio.to_thread(memory.get_approval_context, approval_id)
    if not link:
        return
    conv_id = link["conversation_id"]
    tool_call_id = link.get("tool_call_id")
    messages = await asyncio.to_thread(memory.get_messages, conv_id, None)

    index = None
    if tool_call_id:
        for position, message in enumerate(messages):
            if (
                message.get("role") == "tool"
                and message.get("tool_call_id") == tool_call_id
            ):
                index = position
                break
    if index is None:
        for position in range(len(messages) - 1, -1, -1):
            message = messages[position]
            if message.get("role") == "tool" and approval_id in (
                message.get("content") or ""
            ):
                index = position
                break
    if index is None:
        log.warning("sin contexto de herramienta para la aprobacion %s", approval_id)
        return

    history = [dict(message) for message in messages[: index + 1]]
    history[index]["content"] = json.dumps(payload, ensure_ascii=False)
    base = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    state = {
        "messages": base,
        "tool_trace": [],
        "iterations": 0,
        "conversation_id": conv_id,
    }

    try:
        result = await AGENT.ainvoke(state)
        reply = await asyncio.to_thread(
            _persist_turn_sync, conv_id, len(base), result
        )
        await asyncio.to_thread(
            memory.add_audit,
            "aprobacion:reanudar",
            {"approval_id": approval_id},
            True,
            0,
            f"chat reanudado: {reply[:80]}",
            conv_id,
        )
    except Exception as exc:  # noqa: BLE001 - nunca romper la respuesta humana
        log.exception("no se pudo reanudar el chat tras %s", approval_id)
        fallback = (
            "La decisión se registró, pero no pude redactar el resumen en el "
            f"chat ({exc}). El resultado real está en la pestaña Aprobaciones."
        )
        await asyncio.to_thread(memory.add_message, conv_id, "assistant", fallback)
        await asyncio.to_thread(memory.touch_conversation, conv_id)


@app.get(
    "/api/conversations", tags=["chat"], dependencies=[Depends(require_auth)]
)
async def list_conversations(limit: int = 50) -> dict:
    limit = max(1, min(limit, 200))
    items = await asyncio.to_thread(memory.list_conversations, limit)
    return {"conversations": items, "count": len(items)}


@app.get(
    "/api/conversations/{conversation_id}",
    tags=["chat"],
    dependencies=[Depends(require_auth)],
)
async def get_conversation(conversation_id: str) -> dict:
    exists = await asyncio.to_thread(memory.conversation_exists, conversation_id)
    if not exists:
        raise HTTPException(404, "Conversación no encontrada")
    messages = await asyncio.to_thread(memory.get_messages, conversation_id, None)
    return {"conversation_id": conversation_id, "messages": messages}


@app.get("/api/audit", tags=["meta"], dependencies=[Depends(require_auth)])
async def audit(limit: int = 50) -> dict:
    limit = max(1, min(limit, 200))
    items = await asyncio.to_thread(memory.list_audit, limit)
    return {"audit": items, "count": len(items)}


# --------------------------------------------------------------------------- #
# Proxy de FileBrowser (mismo origen que la PWA)
# --------------------------------------------------------------------------- #
# FileBrowser vive en su propio contenedor con baseURL "/files". En vez de
# exponer otro puerto (otro origen, otra sesión, otro login a la vista del
# usuario), el agente reenvía /files/* tal cual. Como el prefijo no cambia,
# la cookie "auth" de FileBrowser (Path=/files) y sus rutas relativas siguen
# funcionando sin reescrituras.
_PROXY_DROP_REQUEST = {
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
# Las mismas cabeceras hop-by-hop valen para la respuesta. "Content-Length"
# y "Content-Encoding" SÍ se conservan: el cuerpo se reenvía en crudo
# (aiter_raw), así que la longitud y la compresión siguen siendo válidas y
# el navegador puede mostrar progreso en las descargas.
_PROXY_DROP_RESPONSE = _PROXY_DROP_REQUEST


def _forward_headers(request: Request, session_cookie: str) -> dict[str, str]:
    """Cabeceras del navegador listas para el contenedor.

    Se quitan las hop-by-hop y, además, la cookie de sesión de Jarvis: el
    contenedor de FileBrowser no tiene por qué verla. La cookie propia de
    FileBrowser ("auth") sí viaja.
    """
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in _PROXY_DROP_REQUEST:
            continue
        if lower == "cookie":
            pairs = [
                part.strip()
                for part in value.split(";")
                if part.strip() and not part.strip().startswith(f"{session_cookie}=")
            ]
            if pairs:
                headers[key] = "; ".join(pairs)
            continue
        headers[key] = value
    return headers


async def _files_proxy(request: Request, rest: str = "") -> Response:
    settings = get_settings()
    base = (settings.filebrowser_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "El proxy de FileBrowser está desactivado")

    # Se conserva el sufijo EXACTO que pidió el navegador (incluida la barra
    # final): FileBrowser responde 307 a "/files" pero 200 a "/files/", así
    # que normalizar aquí crearía un bucle de redirecciones. Se usa la ruta
    # cruda del ASGI para no perder caracteres escapados.
    raw = request.scope.get("raw_path")
    if raw:
        ruta = raw.decode("latin-1")
    else:
        ruta = "/files" + (f"/{rest}" if rest else "")

    sufijo = ruta[len("/files") :] if ruta.startswith("/files") else ""
    url = f"{base}/files{sufijo}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = _forward_headers(request, settings.cookie_name)

    timeout = httpx.Timeout(
        connect=5.0,
        read=settings.filebrowser_timeout,
        write=settings.filebrowser_timeout,
        pool=5.0,
    )
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    upstream_request = client.build_request(
        request.method, url, headers=headers, content=request.stream()
    )

    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        log.warning("FileBrowser inaccesible (%s): %s", url, exc)
        raise HTTPException(502, "FileBrowser no responde") from exc

    # httpx entrega las cabeceras en minúsculas. Se reenvían tal cual salvo
    # las hop-by-hop; "set-cookie" se trata aparte porque puede haber varias.
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _PROXY_DROP_RESPONSE and key.lower() != "set-cookie"
    }
    location = response_headers.get("location")
    if location and location.startswith(base):
        response_headers["location"] = location[len(base) :] or "/files/"

    cookies = upstream.headers.get_list("set-cookie")

    async def cleanup() -> None:
        await upstream.aclose()
        await client.aclose()

    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(cleanup),
    )
    for cookie in cookies:
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    return response


_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@app.api_route(
    "/files",
    methods=_PROXY_METHODS,
    include_in_schema=False,
    dependencies=[Depends(require_auth)],
)
async def files_root(request: Request) -> Response:
    return await _files_proxy(request)


@app.api_route(
    "/files/{rest:path}",
    methods=_PROXY_METHODS,
    include_in_schema=False,
    dependencies=[Depends(require_auth)],
)
async def files_any(request: Request, rest: str) -> Response:
    return await _files_proxy(request, rest)


# --------------------------------------------------------------------------- #
# PWA (mismo origen, sin CORS). Debe ir AL FINAL: es un catch-all.
# --------------------------------------------------------------------------- #
_static_dir = Path(get_settings().static_dir)
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="pwa")
else:

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "service": "jarvis-agent",
            "version": VERSION,
            "pwa": False,
            "hint": "Compila la PWA para servirla aquí (ver README).",
        }
