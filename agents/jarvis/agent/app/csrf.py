"""Protección CSRF criptográfica y validación de Origin.

Modelo: **token vinculado a la sesión**, no un doble envío de cookie.

- El token es `HMAC-SHA256(clave, "csrf-v1|" + sha256(token_de_sesion))`.
- La clave se deriva de un secreto del servidor (`JARVIS_CSRF_SECRET`), y si
  no existe, del `JARVIS_API_TOKEN` o del hash de la contraseña de
  administrador (los tres son secretos que solo el servidor conoce).
- El navegador recibe el token en la respuesta del login (y en
  `/api/auth/me`), lo mantiene **en memoria** y lo envía en la cabecera
  `X-CSRF-Token`.
- El servidor lo recalcula a partir de la cookie de sesión que tiene a mano y
  lo compara en tiempo constante.

Por qué esto resiste a un ataque cross-site: el atacante puede enviar un
`Origin` o un `X-CSRF-Token` cualquiera, pero **no puede conocer la cookie
de sesión** (`HttpOnly`) ni, por tanto, calcular el HMAC que el servidor
espera. Un token falsificado se rechaza.

Además se valida el `Origin` de forma estricta en las rutas que cambian
estado: debe coincidir con un origen configurado o con el mismo host de la
petición (mismo origen). Detrás de un proxy TLS (Tailscale Serve) el
esquema puede diferir, por eso se compara el *host*, no el esquema.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from .config import Settings, get_settings

CSRF_HEADER = "X-CSRF-Token"
_CONTEXT = "csrf-v1"


def csrf_key(settings: Settings | None = None) -> bytes:
    """Clave del HMAC. Derivada de un secreto del servidor; vacía si no hay."""
    settings = settings or get_settings()
    material = settings.csrf_secret or settings.api_token or settings.admin_password_hash
    if not material:
        return b""
    return hashlib.sha256(material.encode("utf-8")).digest()


def issue_token(fingerprint: str, settings: Settings | None = None) -> str:
    """Token CSRF de una sesión concreta ("" si el servidor no tiene clave)."""
    key = csrf_key(settings)
    if not key or not fingerprint:
        return ""
    message = f"{_CONTEXT}|{fingerprint}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_token(
    fingerprint: str, provided: str | None, settings: Settings | None = None
) -> bool:
    """Comprueba el token contra el esperado para ESA sesión."""
    expected = issue_token(fingerprint, settings)
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def _normalize(url: str) -> str:
    return url.strip().rstrip("/").lower()


def configured_origins(settings: Settings | None = None) -> set[str]:
    """Orígenes declarados en la configuración (normalizados)."""
    settings = settings or get_settings()
    configured = (settings.allowed_origins or "").strip()
    if not configured:
        return set()
    return {_normalize(item) for item in configured.split(",") if item.strip()}


def allowed_origins(request: Request, settings: Settings | None = None) -> set[str]:
    """Orígenes admitidos: los configurados + el propio origen de la petición."""
    settings = settings or get_settings()
    allowed = configured_origins(settings)
    # Mismo origen: el host de la petición. Se compara el netloc (host:puerto)
    # porque detrás de Tailscale Serve el esquema puede ser http o https.
    host_header = request.headers.get("host", "").strip().lower()
    if host_header:
        allowed.add(host_header)
    return allowed


def check_origin(request: Request, settings: Settings | None = None) -> None:
    """Valida el `Origin` de una petición que cambia estado. 403 si no vale."""
    settings = settings or get_settings()
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Falta la cabecera Origin: las peticiones que cambian estado "
            "deben indicar su origen",
        )

    normalized = _normalize(origin)
    origin_netloc = urlsplit(origin).netloc.lower()

    # 1) Mismo origen: el netloc del Origin coincide con el Host de la
    #    petición. Es válido detrás de un proxy TLS (Tailscale Serve), donde
    #    el esquema puede diferir (http hacia el contenedor, https en el
    #    navegador).
    host_header = request.headers.get("host", "").strip().lower()
    if host_header and origin_netloc == host_header:
        return

    # 2) Orígenes declarados en la configuración.
    for candidate in configured_origins(settings):
        if normalized == candidate:
            return
        if urlsplit(candidate).netloc.lower() == origin_netloc:
            return

    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Origin no autorizado: {normalized}",
    )


def check_csrf(
    request: Request, fingerprint: str, settings: Settings | None = None
) -> None:
    """Valida el token CSRF de la sesión. 403 si no coincide."""
    provided = request.headers.get(CSRF_HEADER)
    if not verify_token(fingerprint, provided or "", settings):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Token CSRF inválido o ausente",
        )
