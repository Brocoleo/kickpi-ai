"""Cliente HTTP hacia el servicio de ejecutor multi-host.

El agente NO habla SSH: solo pide al ejecutor una operacion del catalogo.
El ejecutor aplica su Policy Engine (deny by default) y devuelve el resultado.

Las herramientas del ejecutor viven en un espacio de nombres aparte
(`equipos_*`) para que el LLM no las confunda con las de lectura local.
"""
from __future__ import annotations

import httpx

from .config import get_settings


class ExecutorError(RuntimeError):
    """Fallo al invocar el ejecutor."""


# Esquema que ve el LLM. `host` admite los ids del registro.
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "equipos_list",
            "description": (
                "Lista los equipos registrados (KICKPI, MacBook, PC de escritorio) "
                "con su transporte, direccion y si estan habilitados. "
                "No ejecuta nada en los equipos."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipos_status",
            "description": (
                "Comprueba si un equipo responde ahora mismo (online/offline) y "
                "devuelve su kernel. Solo hace una consulta de lectura."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Id del equipo: kickpi, macbook o desktop",
                    }
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipos_ejecutar",
            "description": (
                "Ejecuta una operacion AUTORIZADA del catalogo en un equipo. "
                "No acepta comandos de shell: solo el identificador de una "
                "operacion del catalogo y sus parametros. Operaciones disponibles: "
                "host_uname, host_uptime, host_disk_free, host_mem_free, "
                "host_list_dir (parametro 'path'), host_git_status (parametro 'path'), "
                "host_git_log (parametros 'path' y 'count'), "
                "host_project_test (parametro 'project_id'). "
                "Las rutas deben estar dentro de las raices autorizadas del equipo. "
                "Si la operacion exige aprobacion humana, la herramienta NO la ejecuta: "
                "crea una solicitud pendiente y te devuelve su identificador. "
                "Tu no puedes aprobar nada: eso lo hace el usuario en la app."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Id del equipo: kickpi, macbook o desktop",
                    },
                    "operation": {
                        "type": "string",
                        "description": "Id de la operacion del catalogo",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parametros tipados de la operacion",
                    },
                },
                "required": ["host", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipos_historial",
            "description": (
                "Ultimas ejecuciones registradas (equipo, operacion, resultado y "
                "duracion). Solo lectura de la auditoria."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Filtra por id de equipo (opcional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Numero de entradas (1-100, por defecto 20)",
                    },
                },
                "required": [],
            },
        },
    },
]

TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}


def _base() -> tuple[str, dict, float]:
    settings = get_settings()
    headers = {"X-Executor-Token": settings.executor_token}
    return settings.executor_url, headers, settings.executor_timeout


async def call_executor(name: str, arguments: dict | None = None) -> dict:
    """Invoca una herramienta del ejecutor."""
    arguments = arguments or {}
    url, headers, timeout = _base()

    if name == "equipos_list":
        endpoint, method, params = "/hosts", "GET", None
    elif name == "equipos_status":
        endpoint, method, params = f"/hosts/{arguments.get('host')}/status", "GET", None
    elif name == "equipos_historial":
        endpoint, method = "/executions", "GET"
        params = {}
        if arguments.get("host"):
            params["host"] = arguments["host"]
        if arguments.get("limit"):
            params["limit"] = max(1, min(int(arguments["limit"]), 100))
    elif name == "equipos_ejecutar":
        endpoint, method, params = "/exec", "POST", None
    else:
        raise ExecutorError(f"Herramienta de ejecutor desconocida: {name}")

    body = None
    if method == "POST":
        body = {
            "host": arguments.get("host"),
            "operation": arguments.get("operation"),
            "params": arguments.get("params") or {},
        }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "POST":
                resp = await client.post(f"{url}{endpoint}", json=body, headers=headers)
            else:
                resp = await client.get(f"{url}{endpoint}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise ExecutorError(f"No se pudo contactar con el ejecutor: {exc}") from exc

    if resp.status_code == 401:
        raise ExecutorError("Token de ejecutor rechazado")
    if resp.status_code >= 400:
        raise ExecutorError(f"El ejecutor devolvio HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise ExecutorError("Respuesta no valida del ejecutor") from exc
