"""Clasificación de adjuntos en subcarpetas de `Entrada/`.

Las reglas viven en `classifier_rules.json` para poder añadir tipos (por
ejemplo `Audio/`) sin tocar código. Este módulo solo devuelve un nombre de
carpeta de **un único segmento**; `main.py` sigue siendo quien valida que el
destino final quede dentro del inbox.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REGLAS_POR_DEFECTO = Path(__file__).with_name("classifier_rules.json")


class ErrorReglas(ValueError):
    """Las reglas de clasificación no son válidas."""


def _carpeta_segura(valor: object) -> str | None:
    """Devuelve un nombre de carpeta de un solo segmento, o None."""
    if not isinstance(valor, str):
        return None
    carpeta = valor.strip()
    if not carpeta or len(carpeta) > 64:
        return None
    if carpeta in {".", ".."} or carpeta.startswith("."):
        return None
    if "/" in carpeta or "\\" in carpeta or "\x00" in carpeta:
        return None
    return carpeta


def normalizar(reglas: dict) -> dict:
    """Valida y normaliza un objeto de reglas (JSON de disco o de la DB)."""
    if not isinstance(reglas, dict):
        raise ErrorReglas("las reglas deben ser un objeto JSON")
    por_defecto = _carpeta_segura(reglas.get("default_folder")) or "Documentos"
    salida: list[dict] = []
    for regla in reglas.get("rules") or []:
        if not isinstance(regla, dict):
            continue
        carpeta = _carpeta_segura(regla.get("folder"))
        if not carpeta:
            # Regla con carpeta inválida (p. ej. "../fuera"): se ignora.
            continue
        extensiones = {
            str(ext).lower()
            for ext in regla.get("extensions") or []
            if str(ext).startswith(".")
        }
        mimes = {
            str(mime).lower().strip()
            for mime in regla.get("mimes") or []
            if str(mime).strip()
        }
        if extensiones or mimes:
            salida.append(
                {"folder": carpeta, "extensions": extensiones, "mimes": mimes}
            )
    if not salida:
        raise ErrorReglas("no hay reglas válidas")
    return {"default_folder": por_defecto, "rules": salida}


@lru_cache(maxsize=4)
def _cargar(ruta: str) -> dict:
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ErrorReglas(f"no se pudieron leer las reglas: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorReglas(f"reglas JSON inválidas: {exc}") from exc
    return normalizar(datos)


def reglas_por_defecto() -> dict:
    return _cargar(str(REGLAS_POR_DEFECTO))


def serializar(reglas: dict) -> dict:
    """Forma canónica y JSON-serializable de las reglas (para guardarlas)."""
    cfg = normalizar(reglas)
    return {
        "version": 1,
        "default_folder": cfg["default_folder"],
        "rules": [
            {
                "folder": regla["folder"],
                "extensions": sorted(regla["extensions"]),
                "mimes": sorted(regla["mimes"]),
            }
            for regla in cfg["rules"]
        ],
    }


def _mime_coincide(tipo: str, prefijos: set[str]) -> bool:
    for prefijo in prefijos:
        if prefijo.endswith("/"):
            if tipo.startswith(prefijo):
                return True
        elif tipo == prefijo:
            return True
    return False


def clasificar(nombre: str, mime: str | None = None, reglas: dict | None = None) -> str:
    """Devuelve la subcarpeta destino; la extensión manda sobre el MIME.

    `reglas` permite inyectar una configuración (más adelante, la de la DB).
    """
    cfg = normalizar(reglas) if reglas is not None else reglas_por_defecto()
    extension = Path(nombre or "").suffix.lower()
    if extension:
        for regla in cfg["rules"]:
            if extension in regla["extensions"]:
                return regla["folder"]
    tipo = (mime or "").lower().strip()
    if tipo:
        for regla in cfg["rules"]:
            if _mime_coincide(tipo, regla["mimes"]):
                return regla["folder"]
    return cfg["default_folder"]
