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
from .csrf import check_csrf, check_origin
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


# --------------------------------------------------------------------------- #
# Sesión HUMANA (para las aprobaciones)
# --------------------------------------------------------------------------- #
def session_fingerprint_of(request: Request) -> str | None:
    """Huella de la cookie de sesión (None si no hay cookie)."""
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    return session_fingerprint(token)


def require_human_session(request: Request) -> str:
    """Exige una SESIÓN DE NAVEGADOR y devuelve la identidad humana.

    A diferencia de `require_auth`, el token de API **no** sirve aquí: las
    decisiones de aprobación son humanas, y el LLM (o un script) no puede
    tomar esa decisión por su cuenta aunque conozca el token de la API.
    """
    settings = get_settings()
    method = auth_method(request)
    if method is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No autenticado: se requiere una sesión de navegador",
        )
    if method != "session":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Esta operación exige una sesión de navegador; el token de API no basta",
        )

    fingerprint = session_fingerprint_of(request)
    if not fingerprint:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión no válida")

    subject = sessions.subject(fingerprint) or settings.admin_user
    return subject


def require_human_mutation(request: Request) -> str:
    """Sesión humana + Origin estricto + token CSRF ligado a esa sesión.

    Es la dependencia de TODA ruta que cambia estado (aprobar, rechazar,
    ejecutar). Devuelve la identidad humana, que es la que queda registrada
    como `approved_by` en el ejecutor.
    """
    subject = require_human_session(request)

    check_origin(request)
    fingerprint = session_fingerprint_of(request)
    if not fingerprint:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión no válida")
    check_csrf(request, fingerprint)
    return subject
