"""Cliente HTTP hacia el servicio de herramientas (solo lectura).

El agente NO conoce Docker ni el sistema directamente: todo pasa por aquí,
y aquí solo se permiten las operaciones declaradas en `_ENDPOINTS`.
"""
import httpx

from .config import get_settings


class ToolsError(RuntimeError):
    """Fallo al invocar una herramienta."""


# Esquema que ve el LLM (formato OpenAI "tools").
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "system_stats",
            "description": (
                "Estado real del sistema: uso de CPU (total y por núcleo), "
                "carga media, temperatura, memoria RAM, swap y uptime."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disk_usage",
            "description": "Uso de disco de los puntos de montaje relevantes (raíz y datos).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_ps",
            "description": (
                "Lista los contenedores Docker con su estado, salud, puertos, "
                "imagen y número de reinicios. Incluye los parados."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "container_logs",
            "description": (
                "Últimas líneas de los logs de un contenedor, ya saneadas "
                "(sin secretos evidentes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del contenedor"},
                    "tail": {
                        "type": "integer",
                        "description": "Número de líneas (1-500, por defecto 200)",
                    },
                },
                "required": ["name"],
            },
        },
    },
]

_ENDPOINTS = {
    "system_stats": "/system/stats",
    "disk_usage": "/disk/usage",
    "docker_ps": "/docker/ps",
    "container_logs": "/docker/logs/{name}",
}


async def call_tool(name: str, arguments: dict | None = None) -> dict:
    arguments = arguments or {}
    endpoint = _ENDPOINTS.get(name)
    if endpoint is None:
        raise ToolsError(f"Herramienta desconocida: {name}")

    if "{name}" in endpoint:
        container = arguments.get("name")
        if not container:
            raise ToolsError("Falta el parámetro 'name'")
        endpoint = endpoint.format(name=container)

    params: dict = {}
    if name == "container_logs" and "tail" in arguments:
        params["tail"] = arguments["tail"]
    elif name == "docker_ps" and "all" in arguments:
        params["all"] = arguments["all"]

    settings = get_settings()
    headers = {"X-Tools-Token": settings.tools_token}
    try:
        async with httpx.AsyncClient(timeout=settings.tools_timeout) as client:
            resp = await client.get(f"{settings.tools_url}{endpoint}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise ToolsError(f"No se pudo contactar con el servicio de herramientas: {exc}") from exc

    if resp.status_code == 401:
        raise ToolsError("Token de herramientas rechazado")
    if resp.status_code >= 400:
        raise ToolsError(f"Herramienta {name} devolvió HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise ToolsError(f"Respuesta no válida de {name}") from exc
