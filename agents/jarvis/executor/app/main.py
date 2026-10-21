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
from pydantic import BaseModel, ConfigDict, Field

from . import approvals, audit, policy
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


async def require_approvals_token(
    x_approvals_token: str | None = Header(default=None, alias="X-Approvals-Token"),
) -> None:
    """Token DEDICADO a las aprobaciones (Fase C).

    Deliberadamente distinto del token del ejecutor: el agente lo usa
    unicamente como proxy autenticado de la PWA y nunca lo inyecta en el
    flujo de herramientas del LLM. Asi, aunque el LLM controle el token del
    ejecutor, no puede listar, aprobar ni ejecutar aprobaciones.
    """
    settings = get_settings()
    if not settings.approvals_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "JARVIS_EXECUTOR_APPROVALS_TOKEN no configurado",
        )
    if not x_approvals_token or not hmac.compare_digest(
        x_approvals_token, settings.approvals_token
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de aprobaciones invalido")


class ExecRequest(BaseModel):
    # `extra="forbid"`: si el LLM intenta colar un `approval_id` (o cualquier
    # campo desconocido), la peticion se RECHAZA con 422 en lugar de
    # ignorarse en silencio. La ejecucion de una aprobacion solo se puede
    # lanzar por /approvals/{id}/execute, con el token de aprobaciones.
    model_config = ConfigDict(extra="forbid")

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
    """Catalogo de operaciones.

    Incluye las auto-aprobables y las que exigen aprobacion humana
    (`requires_approval: true`): asi el cliente sabe que existen y que no
    van a ejecutarse solas. Verlas NO da ninguna capacidad: el Policy
    Engine sigue denegando por defecto y estas no se ejecutan sin un humano.
    """
    registry = load_registry()
    items = []
    for op in registry.operations.values():
        items.append(
            {
                "id": op.id,
                "title": op.title,
                "description": op.description,
                "hosts": list(op.hosts),
                "risk": op.risk,
                "auto": op.auto,
                "requires_approval": not op.auto,
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

    # Operacion valida que exige aprobacion humana: NO se ejecuta. Se abre
    # (o se reutiliza) una solicitud pendiente y se devuelve al agente.
    # El LLM no decide nada aqui: solo puede pedir.
    if decision.requires_approval:
        operation = registry.operation(body.operation)
        try:
            approval = approvals.ensure_pending(
                requested_by="agente",
                host=body.host,
                operation=body.operation,
                params=body.params or {},
                decision=decision,
                risk=operation.risk if operation else "",
                risk_description=operation.description if operation else "",
            )
        except RuntimeError as exc:
            audit.record(
                body.host, body.operation, body.params or {}, allowed=False, ok=False,
                error=str(exc),
            )
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
        audit.record(
            body.host, body.operation, body.params or {}, allowed=False, ok=False,
            error=f"pendiente de aprobacion: {approval['id']}",
            approval_id=approval["id"],
        )
        log.info(
            "solicitud de aprobacion %s host=%s op=%s", approval["id"], body.host, body.operation
        )
        return {
            "ok": False,
            "denied": False,
            "state": "pending_approval",
            "host": body.host,
            "operation": body.operation,
            "reason": decision.reason,
            "approval": approvals.public(approval),
        }

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


# ============================================================================
# APROBACIONES HUMANAS (Fase C)
# ---------------------------------------------------------------------------
# Estos endpoints NO son alcanzables por el LLM:
#   - exigen `X-Approvals-Token`, token distinto del del ejecutor;
#   - el agente solo los usa como proxy de la PWA (sesion humana + CSRF);
#   - `/exec` NO acepta `approval_id`: la ejecucion de una aprobacion se
#     dispara por la ruta, sin cuerpo, y usa solo lo persistido.
# ============================================================================


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., description="id del host")
    operation: str = Field(..., description="id de la operacion del catalogo")
    params: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Field(default="agente", description="quien la solicita (el agente)")


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(default="", description="usuario humano autenticado")
    reason: str = Field(default="")


@app.post("/approvals", tags=["approvals"], status_code=201,
          dependencies=[Depends(require_approvals_token)])
async def create_approval(body: ApprovalCreate) -> dict:
    """Abre una solicitud de aprobacion para una operacion valida del catalogo.

    Deny by default: si la operacion no existe, no aplica al host, o viola
    las raices, NO hay nada que aprobar (403 con el motivo real).
    """
    registry = load_registry()
    decision = await policy.check(registry, body.host, body.operation, body.params or {})
    if decision.allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"la operacion {body.operation} no requiere aprobacion (es auto-aprobable)",
        )
    if not decision.requires_approval:
        audit.record(
            body.host, body.operation, body.params or {}, allowed=False, ok=False,
            error=decision.reason,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)

    operation = registry.operation(body.operation)
    try:
        approval = approvals.ensure_pending(
            requested_by=body.requested_by or "agente",
            host=body.host,
            operation=body.operation,
            params=body.params or {},
            decision=decision,
            risk=operation.risk if operation else "",
            risk_description=operation.description if operation else "",
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    return {"approval": approvals.public(approval)}


@app.get("/approvals", tags=["approvals"], dependencies=[Depends(require_approvals_token)])
def list_approvals(
    status: str | None = Query(default=None),
    host: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items = [approvals.public(a) for a in approvals.list_approvals(status, host, limit)]
    return {"approvals": items, "count": len(items)}


@app.get("/approvals/{approval_id}", tags=["approvals"],
         dependencies=[Depends(require_approvals_token)])
def get_approval(approval_id: str) -> dict:
    approval = approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "aprobacion desconocida")
    return {"approval": approvals.public(approval)}


@app.post("/approvals/{approval_id}/approve", tags=["approvals"],
          dependencies=[Depends(require_approvals_token)])
def approve(approval_id: str, body: ApprovalDecision) -> dict:
    """Aprueba. `approved_by` es la identidad HUMANA (obligatoria)."""
    try:
        approval = approvals.decide(
            approval_id, approve=True, approved_by=body.approved_by, reason=body.reason
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "aprobacion desconocida")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    log.info("aprobacion %s aprobada por %s", approval_id, approval.get("approved_by"))
    return {"approval": approvals.public(approval)}


@app.post("/approvals/{approval_id}/reject", tags=["approvals"],
          dependencies=[Depends(require_approvals_token)])
def reject(approval_id: str, body: ApprovalDecision) -> dict:
    """Rechaza. Un rechazo no consume nada: la solicitud queda cerrada."""
    try:
        approval = approvals.decide(
            approval_id, approve=False, approved_by=body.approved_by, reason=body.reason
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "aprobacion desconocida")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    log.info("aprobacion %s rechazada", approval_id)
    return {"approval": approvals.public(approval)}


@app.post("/approvals/{approval_id}/execute", tags=["approvals"],
          dependencies=[Depends(require_approvals_token)])
async def execute_approval(approval_id: str) -> dict:
    """Ejecuta UNA aprobacion ya concedida. Sin cuerpo: todo viene de lo
    persistido y verificado.

    Secuencia (en este orden, sin excepciones):
      1. la solicitud debe estar `approved` (y no expirada);
      2. se re-evalua la POLITICA con los parametros almacenados y se
         compara la huella del argv normalizado (si el catalogo, las
         raices, el host o los parametros cambiaron, se invalida);
      3. se RECLAMA de forma atomica (un solo uso, con expiracion);
      4. se ejecuta EXCLUSIVAMENTE el argv persistido;
      5. se cierra la solicitud y se audita el resultado.
    """
    registry = load_registry()
    settings = get_settings()

    approval = approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "aprobacion desconocida")
    if approval["status"] != approvals.APPROVED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"la aprobacion no esta lista para ejecutar (estado: {approval['status']})",
        )

    # 2. Re-validacion de politica + vinculo exacto.
    decision = await policy.check(
        registry, approval["host"], approval["operation"], approval["params"]
    )
    mismatch = approvals.verify_against_registry(approval, decision)
    if mismatch:
        approvals.mark_failed(approval_id, mismatch)
        audit.record(
            approval["host"], approval["operation"], approval["params"], allowed=False,
            ok=False, error=f"aprobacion invalidada: {mismatch}",
        )
        raise HTTPException(status.HTTP_409_CONFLICT, f"la aprobacion ya no es valida: {mismatch}")

    # 3. Reclamo atomico (un solo uso).
    try:
        claimed = approvals.claim(approval_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "aprobacion desconocida")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    # 4. Ejecucion: EXCLUSIVAMENTE el argv persistido y verificado.
    host = registry.host(claimed["host"])
    if host is None:
        approvals.finish(approval_id, executed=False, error="host desconocido")
        raise HTTPException(status.HTTP_409_CONFLICT, "el host ya no existe en el registro")
    argv = list(claimed["argv"])
    timeout = float(claimed["timeout_s"] or settings.command_timeout)
    result = await run(host, argv, timeout=timeout)

    stdout = result.stdout
    if len(stdout) > settings.max_output_bytes:
        stdout = stdout[: settings.max_output_bytes] + "\n[...salida truncada...]"

    # 5. Cierre + auditoria ATÓMICOS: aprobación y fila de auditoría viven en
    #    el mismo SQLite, así que van en una sola transacción. Si el proceso
    #    muere, o quedan ambas o ninguna (nunca una ejecución sin auditoría).
    executed = result.error is None
    with audit.transaction() as conn:
        approvals.finish(
            approval_id,
            executed=executed,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            error=result.error,
            conn=conn,
        )
        audit.record(
            claimed["host"],
            claimed["operation"],
            claimed["params"] or {},
            allowed=True,
            ok=result.ok,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=stdout,
            error=result.error,
            approval_id=approval_id,
            conn=conn,
        )
    return {
        "ok": result.ok,
        "denied": False,
        "state": "consumed" if executed else "failed",
        "host": claimed["host"],
        "operation": claimed["operation"],
        "approval_id": approval_id,
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": result.stderr[-4000:],
        "duration_ms": result.duration_ms,
        "error": result.error,
        "resolved_path": claimed["resolved_path"],
    }
