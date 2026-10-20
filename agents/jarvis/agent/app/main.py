"""API del agente Jarvis + PWA servida en el mismo origen.

Rutas:
  /health              → monitorización (sin auth)
  /api/auth/*          → login / logout / sesión
  /api/chat, /api/...  → requieren token de API o cookie de sesión
  /                    → PWA compilada (React + Vite)
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

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
from .llm import CIRCUIT
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

VERSION = "0.2.0"


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
    return data


@app.post("/api/aprobaciones/{approval_id}/ejecutar", tags=["aprobaciones"])
async def ejecutar_aprobacion(
    approval_id: str,
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
@app.post(
    "/api/chat",
    response_model=ChatResponse,
    tags=["chat"],
    dependencies=[Depends(require_auth)],
)
async def chat(body: ChatRequest) -> ChatResponse:
    settings = get_settings()

    conv_id = body.conversation_id
    if conv_id:
        exists = await asyncio.to_thread(memory.conversation_exists, conv_id)
        if not exists:
            raise HTTPException(404, "Conversación no encontrada")
    else:
        conv_id = await asyncio.to_thread(memory.create_conversation, body.message[:60])

    await asyncio.to_thread(memory.add_message, conv_id, "user", body.message)
    history = await asyncio.to_thread(memory.get_messages, conv_id, settings.history_limit)

    base_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    state = {
        "messages": base_messages,
        "tool_trace": [],
        "iterations": 0,
        "conversation_id": conv_id,
    }

    result = await AGENT.ainvoke(state)

    final = result["messages"][-1]
    reply = final.get("content") or ""

    # Persistimos solo lo generado en este turno (respuestas intermedias,
    # llamadas y resultados), para conservar el contexto completo.
    for msg in result["messages"][len(base_messages):]:
        await asyncio.to_thread(
            memory.add_message,
            conv_id,
            msg.get("role", "assistant"),
            msg.get("content"),
            msg.get("tool_calls"),
            msg.get("tool_call_id"),
            msg.get("name"),
        )
    await asyncio.to_thread(memory.touch_conversation, conv_id)

    return ChatResponse(
        conversation_id=conv_id,
        reply=reply,
        tool_trace=result.get("tool_trace", []),
        model=result.get("model_used"),
    )


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
