"""Autenticación por token de API.

Diseño deliberadamente fail-closed: si no hay token configurado en el
servidor, ningún endpoint protegido responde (503). No confiamos en
cabeceras de proxy (X-Forwarded-For) para decidir autorización.
"""
import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


async def require_token(
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not settings.api_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "JARVIS_API_TOKEN no configurado en el servidor",
        )

    provided = x_api_token
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not hmac.compare_digest(provided, settings.api_token):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token de API ausente o inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
