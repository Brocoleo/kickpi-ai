"""API del agente Jarvis."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import memory
from .auth import require_token
from .config import SYSTEM_PROMPT, get_settings
from .db import init_db
from .graph import AGENT
from .llm import CIRCUIT
from .tools_client import TOOL_SPECS, ToolsError, call_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis-agent")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(init_db)
    settings = get_settings()
    if not settings.api_token:
        log.warning("JARVIS_API_TOKEN vacío: los endpoints protegidos darán 503")
    if not settings.llm_configured:
        log.warning("JARVIS_LLM_API_KEY vacía: /chat devolverá un aviso, no respuestas")
    log.info("Jarvis Agent listo (modelo=%s)", settings.llm_model)
    yield


app = FastAPI(title="Jarvis Agent", version="0.1.0", lifespan=lifespan)


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


# --------------------------------------------------------------------------- #
# Meta
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
        "version": "0.1.0",
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "tools_reachable": tools_ok,
        "api_token_configured": bool(settings.api_token),
    }


@app.get("/tools", tags=["meta"], dependencies=[Depends(require_token)])
async def list_tools() -> dict:
    return {
        "read_only": True,
        "tools": [t["function"]["name"] for t in TOOL_SPECS],
        "specs": TOOL_SPECS,
    }


@app.get("/llm/status", tags=["meta"], dependencies=[Depends(require_token)])
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


@app.post("/tools/{tool_name}", tags=["tools"], dependencies=[Depends(require_token)])
async def invoke_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Invoca una herramienta de solo lectura directamente.

    Sirve para validar el camino agente→tools sin depender del LLM, y será la
    base de los widgets de la futura PWA (CPU, discos, contenedores...).
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
@app.post("/chat", response_model=ChatResponse, tags=["chat"], dependencies=[Depends(require_token)])
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


@app.get("/conversations", tags=["chat"], dependencies=[Depends(require_token)])
async def list_conversations(limit: int = 50) -> dict:
    limit = max(1, min(limit, 200))
    items = await asyncio.to_thread(memory.list_conversations, limit)
    return {"conversations": items, "count": len(items)}


@app.get("/conversations/{conversation_id}", tags=["chat"], dependencies=[Depends(require_token)])
async def get_conversation(conversation_id: str) -> dict:
    exists = await asyncio.to_thread(memory.conversation_exists, conversation_id)
    if not exists:
        raise HTTPException(404, "Conversación no encontrada")
    messages = await asyncio.to_thread(memory.get_messages, conversation_id, None)
    return {"conversation_id": conversation_id, "messages": messages}


@app.get("/audit", tags=["meta"], dependencies=[Depends(require_token)])
async def audit(limit: int = 50) -> dict:
    limit = max(1, min(limit, 200))
    items = await asyncio.to_thread(memory.list_audit, limit)
    return {"audit": items, "count": len(items)}
