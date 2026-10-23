"""Contraseñas y sesiones para el navegador.

- Hash de contraseña con **scrypt** (stdlib, sin dependencias extra).
- Identificadores de sesión aleatorios; en la base solo se guarda su SHA-256,
  de modo que un volcado de SQLite no permite reutilizar sesiones.
- Limitador de intentos de login en memoria (ventana deslizante).
"""
import hashlib
import hmac
import secrets
import threading
import time

from .config import get_settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_LOGIN_WINDOW_S = 900
_LOGIN_MAX_FAILURES = 10
_login_failures: list[float] = []


# --------------------------------------------------------------------------- #
# Contraseñas
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Devuelve `scrypt:n:r:p:salt:hash`.

    Se usan dos puntos (no `$`) para que el valor sobreviva intacto a la
    interpolación de variables de Docker Compose en `.env`.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_BYTES,
    )
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{salt.hex()}:{key.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_hex, key_hex = encoded.split(":")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def check_admin_password(password: str) -> bool:
    """Valida contra el hash configurado; si no hay hash, contra el token API."""
    settings = get_settings()
    if settings.admin_password_hash:
        return verify_password(password, settings.admin_password_hash)
    if settings.api_token:
        return hmac.compare_digest(password, settings.api_token)
    return False


# --------------------------------------------------------------------------- #
# Sesiones
# --------------------------------------------------------------------------- #
def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Limitador de intentos
# --------------------------------------------------------------------------- #
def login_allowed() -> bool:
    now = time.monotonic()
    _login_failures[:] = [t for t in _login_failures if now - t < _LOGIN_WINDOW_S]
    return len(_login_failures) < _LOGIN_MAX_FAILURES


def record_login_failure() -> None:
    _login_failures.append(time.monotonic())


def reset_login_failures() -> None:
    _login_failures.clear()


# --------------------------------------------------------------------------- #
# Limitador de subidas (ventana deslizante por IP)
# --------------------------------------------------------------------------- #
_UPLOAD_WINDOW_S = 60
_upload_hits: dict[str, list[float]] = {}
_upload_lock = threading.Lock()


def upload_allowed(ip: str, limit: int) -> tuple[bool, int]:
    """¿Puede esta IP subir otro archivo ahora? Devuelve (ok, reintentar_en).

    Es una ventana deslizante en memoria: suficiente con un solo proceso.
    Si el agente se reinicia, el contador también; no es un control de
    cuotas, es un freno para que un error o un script no llenen el disco.
    """
    if limit <= 0:
        return True, 0
    ahora = time.monotonic()
    with _upload_lock:
        hits = [t for t in _upload_hits.get(ip, []) if ahora - t < _UPLOAD_WINDOW_S]
        if len(hits) >= limit:
            reintentar = max(1, int(_UPLOAD_WINDOW_S - (ahora - hits[0])) + 1)
            _upload_hits[ip] = hits
            return False, reintentar
        hits.append(ahora)
        _upload_hits[ip] = hits
        # Higiene: no acumular IPs que ya no aparecen.
        if len(_upload_hits) > 512:
            for clave in list(_upload_hits):
                vivas = [
                    t
                    for t in _upload_hits[clave]
                    if ahora - t < _UPLOAD_WINDOW_S
                ]
                if vivas:
                    _upload_hits[clave] = vivas
                else:
                    _upload_hits.pop(clave, None)
    return True, 0


def reset_upload_limit() -> None:
    """Solo para pruebas: limpia la ventana de subidas."""
    with _upload_lock:
        _upload_hits.clear()


# --------------------------------------------------------------------------- #
# Utilidad CLI:  python -m app.security
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import getpass

    first = getpass.getpass("Nueva contraseña de Jarvis: ")
    second = getpass.getpass("Repítela: ")
    if not first or first != second:
        raise SystemExit("Las contraseñas no coinciden o están vacías")
    print("\nJARVIS_ADMIN_PASSWORD_HASH=" + hash_password(first))
