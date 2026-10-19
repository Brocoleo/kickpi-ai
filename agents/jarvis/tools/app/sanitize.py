"""Saneado de texto antes de devolverlo al agente.

Objetivo: que los logs no filtren secretos ni crezcan sin límite.
No es un redactor perfecto, pero elimina los patrones más habituales.
"""
import re

_MAX_LINE = 2000

_PATTERNS = [
    # clave = valor  /  clave: valor (token, password, secret, api_key...)
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|authorization|"
        r"auth|bearer|private[_-]?key|access[_-]?key)\b"
        r"(\s*[:=]\s*)(\S+)"
    ),
    # Authorization: Bearer xxxxx
    re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-]{8,})"),
    # user:pass@host  (URLs con credenciales embebidas)
    re.compile(r"(?i)(://[^/\s:@]+):([^/\s@]+)@"),
]

_REPLACEMENTS = [
    lambda m: f"{m.group(1)}{m.group(2)}***",
    lambda m: f"{m.group(1)} ***",
    lambda m: f"{m.group(1)}:***@",  # deja el separador @ fuera del último grupo
]


def _redact(line: str) -> str:
    line = _PATTERNS[0].sub(_REPLACEMENTS[0], line)
    line = _PATTERNS[1].sub(_REPLACEMENTS[1], line)
    # El tercer patrón: conservar el esquema y usuario, ocultar la contraseña.
    line = _PATTERNS[2].sub(lambda m: f"{m.group(1)}:***@", line)
    return line


def sanitize_text(text: str, tail: int, max_bytes: int) -> str:
    """Limita el número de líneas/bytes y redacta secretos evidentes."""
    if not text:
        return ""

    lines = text.splitlines()
    if tail > 0 and len(lines) > tail:
        lines = lines[-tail:]

    out = []
    for line in lines:
        if len(line) > _MAX_LINE:
            line = line[:_MAX_LINE] + " …[truncado]"
        out.append(_redact(line))

    result = "\n".join(out)

    if len(result.encode("utf-8", "ignore")) > max_bytes:
        # Recorta por bytes desde el final para no perder el arranque del error.
        encoded = result.encode("utf-8", "ignore")[-max_bytes:]
        result = encoded.decode("utf-8", "ignore")
        result = "…[recortado]\n" + result

    return result
