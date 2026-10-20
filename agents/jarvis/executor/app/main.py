"""API del ejecutor multi-host.

Superficie minima y deliberadamente pequena:
- El LLM NUNCA envia una linea de shell.
- Solo se aceptan operaciones del catalogo, con parametros tipados.
- Todo pasa por el Policy Engine (deny by default).
- Todo queda auditado en SQLite.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from . import audit, policy
from .config import get_settings
from .registry import Host, load_registry
from .runner import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis-executor")

app = FastAPI(
    title="Jarvis Executor",
    version="0.3.0",
    description="Ejecucion remota multi-host con Policy Engine (solo catalogo autorizado).",
)

# Cache corta del estado de los hosts para no abrir una conexion SSH por consulta.
_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 8.0


async def require_token(
    x_executor_token: str | None = Header(default=None, alias="X-Executor-Token"),
) -> None:
    settings = get_settings()
    if not settings.token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "JARVIS_EXECUTOR_TOKEN no configurado"
        )
    if not x_executor_token or not hmac.compare_digest(x_executor_token, settings.token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de ejecutor invalido")


class ExecRequest(BaseModel):
    host: str = Field(..., description="id del host (kickpi, macbook, desktop)")
    operation: str = Field(..., description="id de la operacion del catalogo")
    params: dict[str, Any] = Field(default_factory=dict)


@app.get("/health", tags=["meta"])
def health() -> dict:
    settings = get_settings()
    registry = load_registry()
    return {
        "status": "ok",
        "service": "jarvis-executor",
        "version": "0.3.0",
        "hosts": len(registry.hosts),
        "operations": len(registry.operations),
        "projects": len(registry.projects),
        "token_configured": bool(settings.token),
    }


@app.get("/operations", tags=["meta"], dependencies=[Depends(require_token)])
def list_operations() -> dict:
    """Catalogo de operaciones auto-aprobables."""
    registry = load_registry()
    items = []
    for op in registry.operations.values():
        if not op.auto:
            continue
        items.append(
            {
                "id": op.id,
                "title": op.title,
                "description": op.description,
                "hosts": list(op.hosts),
                "risk": op.risk,
                "params": [
                    name
                    for name in ("path", "project_id", "count")
                    if (
                        (name == "path" and op.path_param)
                        or (name == "project_id" and op.project_param)
                        or (name == "count" and op.int_param)
                    )
                ],
            }
        )
    return {"operations": items, "count": len(items)}


@app.get("/projects", tags=["meta"], dependencies=[Depends(require_token)])
def list_projects() -> dict:
    registry = load_registry()
    items = [
        {
            "id": p.id,
            "label": p.label,
            "host": p.host,
            "has_tests": bool(p.test_command),
        }
        for p in registry.projects.values()
    ]
    return {"projects": items, "count": len(items)}


@app.get("/hosts", tags=["hosts"], dependencies=[Depends(require_token)])
def list_hosts() -> dict:
    registry = load_registry()
    items = []
    for host in registry.hosts.values():
        items.append(
            {
                "id": host.id,
                "label": host.label,
                "transport": host.transport,
                "enabled": host.enabled,
                "address": host.address,
                "os": host.os,
                "roots": list(host.roots),
            }
        )
    return {"hosts": items, "count": len(items)}


async def _probe(host: Host) -> dict:
    """Comprueba si el host responde. Cache corta."""
    now = time.monotonic()
    cached = _STATUS_CACHE.get(host.id)
    if cached and now - cached[0] < _STATUS_TTL:
        return cached[1]

    settings = get_settings()
    started = time.monotonic()
    if not host.enabled:
        result = {"online": False, "reason": "host deshabilitado"}
    elif host.transport == "ssh" and not host.address:
        result = {"online": False, "reason": "sin direccion configurada"}
    else:
        run_result = await run(host, ["uname", "-sr"], timeout=settings.status_timeout)
        if run_result.error:
            result = {"online": False, "reason": run_result.error}
        else:
            result = {
                "online": True,
                "reason": "ok",
                "kernel": run_result.stdout.strip(),
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
    _STATUS_CACHE[host.id] = (now, result)
    return result


@app.get("/hosts/{host_id}/status", tags=["hosts"], dependencies=[Depends(require_token)])
async def host_status(host_id: str) -> dict:
    registry = load_registry()
    host = registry.host(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"host desconocido: {host_id}")
    status_info = await _probe(host)
    return {"host": host_id, "label": host.label, **status_info}


@app.get("/executions", tags=["audit"], dependencies=[Depends(require_token)])
def executions(
    limit: int = Query(default=50, ge=1, le=200),
    host: str | None = Query(default=None),
) -> dict:
    rows = audit.by_host(host, limit) if host else audit.recent(limit)
    return {"executions": rows, "count": len(rows)}


@app.post("/exec", tags=["exec"], dependencies=[Depends(require_token)])
async def execute(body: ExecRequest) -> dict:
    registry = load_registry()
    settings = get_settings()

    decision = await policy.check(registry, body.host, body.operation, body.params or {})
    if not decision.allowed:
        audit.record(
            body.host, body.operation, body.params or {}, allowed=False, ok=False,
            error=decision.reason,
        )
        log.warning("denegado host=%s op=%s motivo=%s", body.host, body.operation, decision.reason)
        return {
            "ok": False,
            "denied": True,
            "host": body.host,
            "operation": body.operation,
            "reason": decision.reason,
        }

    assert decision.argv is not None
    timeout = float(decision.timeout or settings.command_timeout)
    result = await run(registry.host(body.host), decision.argv, timeout=timeout)

    stdout = result.stdout
    if len(stdout) > settings.max_output_bytes:
        stdout = stdout[: settings.max_output_bytes] + "\n[...salida truncada...]"

    audit.record(
        body.host,
        body.operation,
        body.params or {},
        allowed=True,
        ok=result.ok,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        output=stdout,
        error=result.error,
    )

    return {
        "ok": result.ok,
        "denied": False,
        "host": body.host,
        "operation": body.operation,
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": result.stderr[-4000:],
        "duration_ms": result.duration_ms,
        "error": result.error,
        "resolved_path": decision.resolved_path,
    }
