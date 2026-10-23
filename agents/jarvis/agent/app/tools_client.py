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
    {
        "type": "function",
        "function": {
            "name": "nas_search",
            "description": (
                "Busca archivos en la NAS por nombre, ruta o CONTENIDO de "
                "texto (insensible a mayúsculas y acentos). Úsala para "
                "«¿dónde está la factura de…?», «busca lo que hablé de…». "
                "Devuelve ruta, tamaño, fecha y, si coincide dentro de un "
                "archivo, un fragmento de contexto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Palabras a buscar (2 o más)",
                    },
                    "content": {
                        "type": "boolean",
                        "description": (
                            "Buscar también dentro de archivos de texto "
                            "(por defecto true). Desactívalo para ir más rápido."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de resultados (1-50, por defecto 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nas_recent",
            "description": (
                "Últimos archivos modificados en la NAS, opcionalmente bajo "
                "una subcarpeta. Para «¿qué llegó hoy?» o «¿qué hay de nuevo "
                "en Descargas?»."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de archivos (1-50, por defecto 10)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Subcarpeta dentro de la NAS (opcional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nas_list",
            "description": (
                "Lista una carpeta de la NAS (carpetas primero, luego "
                "archivos). Lectura VIVA del filesystem (sin índice), así que "
                "aparecen archivos recién subidos y carpetas vacías. Úsala "
                "para ver el contenido de una carpeta concreta o confirmar "
                "metadatos de un adjunto reciente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Carpeta relativa dentro de la NAS (vacío = raíz)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de entradas (1-1000, por defecto 300)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nas_refresh",
            "description": (
                "Invalida y re-escanea parte del índice de la NAS. Úsala "
                "tras subir un archivo al chat (ya confirmado por el backend) "
                "para que búsquedas posteriores lo vean al instante. "
                "Por defecto refresca 'files/Entrada' (la carpeta de adjuntos)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subruta": {
                        "type": "string",
                        "description": "Subcarpeta a refrescar (por defecto 'files/Entrada')",
                    },
                },
                "required": [],
            },
        },
    },
]

_ENDPOINTS = {
    "system_stats": "/system/stats",
    "disk_usage": "/disk/usage",
    "docker_ps": "/docker/ps",
    "container_logs": "/docker/logs/{name}",
    "nas_search": "/nas/search",
    "nas_recent": "/nas/recent",
    "nas_list": "/nas/list",
    "nas_refresh": "/nas/refresh",
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
    elif name == "nas_search":
        params["q"] = arguments.get("query", "")
        if "content" in arguments:
            params["content"] = arguments["content"]
        if "limit" in arguments:
            params["limit"] = arguments["limit"]
    elif name == "nas_recent":
        if "limit" in arguments:
            params["limit"] = arguments["limit"]
        if arguments.get("path"):
            params["path"] = arguments["path"]
    elif name == "nas_list":
        if "limit" in arguments:
            params["limit"] = arguments["limit"]
        if arguments.get("path"):
            params["path"] = arguments["path"]
    elif name == "nas_refresh":
        params["subruta"] = arguments.get("subruta", "files/Entrada")

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
