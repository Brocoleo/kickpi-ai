"""Ajustes del NAS editables en caliente (tabla `app_config`).

Los valores de entorno son el punto de partida; lo que se guarda aquí manda y
se aplica sin reiniciar. La ruta del inbox **no** es editable: está atada al
volumen de escritura del contenedor, y cambiarla en caliente desincronizaría
la subida (disco) del borrado (ruta relativa del NAS).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .classifier import ErrorReglas, serializar
from .config import get_settings
from .db import session

RANGO_MB = (1, 500)
RANGO_RATE = (0, 600)


class ErrorConfig(ValueError):
    """Valor de configuración inválido (mensaje pensado para el usuario)."""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _leer_todo() -> dict[str, str]:
    """Todos los valores guardados; {} si la tabla aún no existe."""
    try:
        with session() as conn:
            filas = conn.execute("SELECT clave, valor FROM app_config").fetchall()
    except sqlite3.Error:
        return {}
    return {fila["clave"]: fila["valor"] for fila in filas}


def _entero(valor: object, defecto: int) -> int:
    try:
        return int(valor) if valor not in (None, "") else defecto
    except (TypeError, ValueError):
        return defecto


def _validar_entero(
    valor: object, rango: tuple[int, int], campo: str, detalle: str
) -> int:
    if isinstance(valor, bool):
        raise ErrorConfig(f"{campo}: debe ser un número entero")
    try:
        numero = int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ErrorConfig(f"{campo}: debe ser un número entero") from exc
    minimo, maximo = rango
    if not minimo <= numero <= maximo:
        raise ErrorConfig(f"{campo}: {detalle} (entre {minimo} y {maximo})")
    return numero


def _validar_reglas(valor: object) -> dict | None:
    """Valida las reglas y devuelve una forma canónica serializable a JSON."""
    if valor in (None, ""):
        return None
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except json.JSONDecodeError as exc:
            raise ErrorConfig(
                f"Reglas: JSON inválido en la línea {exc.lineno}, "
                f"columna {exc.colno}: {exc.msg}"
            ) from exc
    try:
        return serializar(valor)  # type: ignore[arg-type]
    except ErrorReglas as exc:
        raise ErrorConfig(f"Reglas: {exc}") from exc


def valores_actuales() -> dict:
    """Configuración efectiva (guardada o, si falta, la de entorno)."""
    s = get_settings()
    filas = _leer_todo()
    reglas = None
    crudo = filas.get("classifier_rules")
    if crudo:
        try:
            reglas = json.loads(crudo)
        except json.JSONDecodeError:
            reglas = None
    return {
        "inbox": s.nas_inbox_rel,
        "inbox_dir": s.nas_inbox_dir,
        "upload_max_mb": _entero(filas.get("upload_max_mb"), s.upload_max_mb),
        "upload_rate_per_min": _entero(
            filas.get("upload_rate_per_min"), s.upload_rate_per_min
        ),
        "classifier_rules": reglas,
    }


def guardar(cambios: dict) -> dict:
    permitidos = {"upload_max_mb", "upload_rate_per_min", "classifier_rules"}
    desconocidas = set(cambios) - permitidos
    if desconocidas:
        raise ErrorConfig(
            "Clave no editable: " + ", ".join(sorted(desconocidas))
        )

    filas: list[tuple[str, str, str]] = []
    if "upload_max_mb" in cambios:
        valor = _validar_entero(
            cambios["upload_max_mb"], RANGO_MB, "Límite por archivo", "MB"
        )
        filas.append(("upload_max_mb", str(valor), _ahora()))
    if "upload_rate_per_min" in cambios:
        valor = _validar_entero(
            cambios["upload_rate_per_min"],
            RANGO_RATE,
            "Subidas por minuto",
            "0 significa sin límite",
        )
        filas.append(("upload_rate_per_min", str(valor), _ahora()))
    if "classifier_rules" in cambios:
        reglas = _validar_reglas(cambios["classifier_rules"])
        texto = json.dumps(reglas, ensure_ascii=False) if reglas else ""
        filas.append(("classifier_rules", texto, _ahora()))

    if not filas:
        return valores_actuales()

    with session() as conn:
        conn.executemany(
            "INSERT INTO app_config (clave, valor, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET "
            "valor = excluded.valor, updated_at = excluded.updated_at",
            filas,
        )
    return valores_actuales()


def ajustes_subida() -> tuple[int, int, dict | None]:
    """(límite MB, subidas/minuto, reglas) efectivos, en una sola consulta."""
    s = get_settings()
    filas = _leer_todo()
    reglas = None
    crudo = filas.get("classifier_rules")
    if crudo:
        try:
            reglas = json.loads(crudo)
        except json.JSONDecodeError:
            reglas = None
    return (
        _entero(filas.get("upload_max_mb"), s.upload_max_mb),
        _entero(filas.get("upload_rate_per_min"), s.upload_rate_per_min),
        reglas,
    )
