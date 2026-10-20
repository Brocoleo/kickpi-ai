"""Cliente proxy de las aprobaciones humanas (Fase C, lado agente).

Este modulo es el UNICO que conoce el token de aprobaciones del ejecutor
(`JARVIS_APPROVALS_TOKEN`). Ese token:

- NUNCA sale del backend: no se devuelve en ninguna respuesta de la API, no
  se registra en logs y no forma parte de ninguna herramienta del LLM.
- Solo se usa aquí, contra los endpoints `/approvals/*` del ejecutor, que
  exigen `X-Approvals-Token`. Las herramientas del LLM (`equipos_*`) usan el
  token del ejecutor (`X-Executor-Token`) y **no** pueden llegar a las
  rutas de aprobación: son surfaces distintas y con credenciales distintas.

El agente actúa como proxy: la PWA habla con el agente (sesión de
navegador + CSRF) y el agente habla con el ejecutor (token dedicado).
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from .config import get_settings


class ApprovalsError(RuntimeError):
    """Fallo al invocar el servicio de aprobaciones del ejecutor."""

    def __init__(self, message: str, code: int = 502) -> None:
        super().__init__(message)
        self.code = code


def _base() -> tuple[str, str, float]:
    settings = get_settings()
    return settings.executor_url, settings.approvals_token, settings.executor_timeout


async def _request(
    method: str,
    path: str,
    body: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Llamada HTTP al servicio de aprobaciones. Token siempre en cabecera."""
    url, token, timeout = _base()
    if not token:
        raise ApprovalsError(
            "El servicio de aprobaciones no está configurado "
            "(JARVIS_APPROVALS_TOKEN vacío)",
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    headers = {"X-Approvals-Token": token}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(f"{url}{path}", params=params, headers=headers)
            else:
                resp = await client.request(
                    method, f"{url}{path}", json=body, headers=headers
                )
    except httpx.HTTPError as exc:
        raise ApprovalsError(f"No se pudo contactar con el ejecutor: {exc}") from exc

    if resp.status_code == 401:
        raise ApprovalsError("Token de aprobaciones rechazado", code=401)
    if resp.status_code == 503:
        raise ApprovalsError(
            "El ejecutor no tiene configurado el token de aprobaciones",
            code=503,
        )
    if resp.status_code >= 400:
        detail = resp.text
        try:
            payload = resp.json()
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
        except ValueError:
            pass
        raise ApprovalsError(detail or f"HTTP {resp.status_code}", code=resp.status_code)

    try:
        return resp.json()
    except ValueError as exc:
        raise ApprovalsError("Respuesta no válida del servicio de aprobaciones") from exc


# --------------------------------------------------------------------------- #
# Operaciones del proxy
# --------------------------------------------------------------------------- #
async def list_approvals(
    status_filter: str | None = None, host: str | None = None, limit: int = 50
) -> dict:
    params: dict = {"limit": max(1, min(int(limit), 200))}
    if status_filter:
        params["status"] = status_filter
    if host:
        params["host"] = host
    return await _request("GET", "/approvals", params=params)


async def get_approval(approval_id: str) -> dict:
    return await _request("GET", f"/approvals/{approval_id}")


async def approve(approval_id: str, approved_by: str) -> dict:
    """Aprueba. `approved_by` lo decide el SERVIDOR (la sesion), no el
    navegador: este endpoint no acepta ningun campo de identidad."""
    return await _request(
        "POST", f"/approvals/{approval_id}/approve", body={"approved_by": approved_by}
    )


async def reject(approval_id: str, approved_by: str, reason: str = "") -> dict:
    return await _request(
        "POST",
        f"/approvals/{approval_id}/reject",
        body={"approved_by": approved_by, "reason": reason},
    )


async def execute(approval_id: str) -> dict:
    """Ejecuta una aprobacion ya concedida. SIN cuerpo: el ejecutor usa
    exclusivamente los datos persistidos y verificados de la solicitud."""
    return await _request("POST", f"/approvals/{approval_id}/execute")


def to_http_error(exc: ApprovalsError) -> HTTPException:
    return HTTPException(exc.code, str(exc))
