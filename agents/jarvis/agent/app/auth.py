"""Autenticación para la API y la PWA.

Dos vías, deliberadamente:

1. **Token de API** (`X-API-Token` o `Authorization: Bearer`), para scripts
   y automatizaciones. Se compara en tiempo constante.
2. **Sesión de navegador**: cookie `HttpOnly` + `Secure` + `SameSite=Strict`.
   El PWA nunca ve el token en JavaScript ni lo guarda en localStorage.

Diseño fail-closed: si el servidor no tiene ni token ni contraseña, los
endpoints protegidos responden 503. No se confía en cabeceras de proxy
(`X-Forwarded-For`) para autorizar.
"""
import hmac

from fastapi import HTTPException, Request, status

from . import sessions
from .config import get_settings
from .security import session_fingerprint


def _api_token_from_request(request: Request) -> str | None:
    provided = request.headers.get("X-API-Token")
    if provided:
        return provided
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def auth_method(request: Request) -> str | None:
    """Devuelve 'token', 'session' o None. No lanza excepciones."""
    settings = get_settings()

    provided = _api_token_from_request(request)
    if (
        settings.api_token
        and provided
        and hmac.compare_digest(provided, settings.api_token)
    ):
        return "token"

    session_token = request.cookies.get(settings.cookie_name)
    if session_token and sessions.valid(session_fingerprint(session_token)):
        return "session"

    return None


async def require_auth(request: Request) -> None:
    settings = get_settings()
    if not settings.api_token and not settings.admin_password_hash:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Ni JARVIS_API_TOKEN ni JARVIS_ADMIN_PASSWORD_HASH están configurados",
        )

    if auth_method(request) is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )
