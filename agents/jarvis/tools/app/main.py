"""API del servicio de herramientas Jarvis (solo lectura).

Todos los endpoints (salvo /health) exigen el token interno
`JARVIS_TOOLS_TOKEN`, que solo conoce el agente.
"""
import hmac
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from . import docker_api, nas_index, system_api
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis-tools")

# Índice perezoso de la NAS: se construye en segundo plano al arrancar y se
# refresca solo. Ninguna consulta bloquea más de unos segundos.
_INDEX = nas_index.IndiceNAS(
    get_settings().nas_root,
    max_files=get_settings().nas_max_files,
    refresh_s=get_settings().nas_refresh_s,
)

app = FastAPI(
    title="Jarvis Tools",
    version="0.2.0",
    description="Operaciones de solo lectura sobre el sistema, Docker y la NAS.",
)


async def require_token(
    x_tools_token: str | None = Header(default=None, alias="X-Tools-Token"),
) -> None:
    settings = get_settings()
    if not settings.tools_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "JARVIS_TOOLS_TOKEN no configurado",
        )
    if not x_tools_token or not hmac.compare_digest(x_tools_token, settings.tools_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de herramientas inválido")


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Sin autenticación: solo indica que el proceso responde."""
    return {"status": "ok", "service": "jarvis-tools", "version": "0.2.0"}


@app.get("/ops", tags=["meta"], dependencies=[Depends(require_token)])
def list_ops() -> dict:
    """Declara explícitamente qué puede hacer este servicio (y qué no)."""
    return {
        "read_only": True,
        "operations": [
            "GET /system/stats",
            "GET /disk/usage",
            "GET /docker/ps",
            "GET /docker/logs/{name}",
            "GET /docker/stats/{name}",
            "GET /nas/search",
            "GET /nas/recent",
            "GET /nas/status",
        ],
        "denied": [
            "start/stop/restart de contenedores",
            "ejecución de comandos en contenedores",
            "crear/borrar contenedores, imágenes o volúmenes",
            "escritura en el sistema de ficheros",
        ],
    }


@app.get("/system/stats", tags=["system"], dependencies=[Depends(require_token)])
def system_stats() -> dict:
    return system_api.system_stats()


@app.get("/disk/usage", tags=["system"], dependencies=[Depends(require_token)])
def disk_usage() -> dict:
    return system_api.disk_usage()


@app.get("/docker/ps", tags=["docker"], dependencies=[Depends(require_token)])
def docker_ps(all: bool = Query(default=True, description="Incluir contenedores parados")) -> dict:
    return docker_api.list_containers(all_containers=all)


@app.get("/docker/logs/{name}", tags=["docker"], dependencies=[Depends(require_token)])
def docker_logs(
    name: str,
    tail: int = Query(default=200, ge=1, le=500, description="Últimas N líneas"),
) -> dict:
    return docker_api.container_logs(name, tail=tail)


@app.get("/docker/stats/{name}", tags=["docker"], dependencies=[Depends(require_token)])
def docker_stats(name: str) -> dict:
    return docker_api.container_stats(name)


@app.get("/nas/search", tags=["nas"], dependencies=[Depends(require_token)])
def nas_search(
    q: str = Query(min_length=1, max_length=200, description="Texto a buscar"),
    content: bool = Query(default=True, description="Buscar también dentro de archivos de texto"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Busca archivos por nombre, ruta o contenido (insensible a acentos)."""
    return _INDEX.buscar(q, limite=limit, contenido=content)


@app.get("/nas/recent", tags=["nas"], dependencies=[Depends(require_token)])
def nas_recent(
    limit: int = Query(default=10, ge=1, le=50),
    path: str = Query(default="", max_length=300, description="Subcarpeta opcional"),
) -> dict:
    """Últimos archivos modificados, opcionalmente bajo una subcarpeta."""
    return _INDEX.recientes(limite=limit, bajo=path)


@app.get("/nas/status", tags=["nas"], dependencies=[Depends(require_token)])
def nas_status() -> dict:
    """Estado del índice: archivos vistos, tamaño, antigüedad y errores."""
    return _INDEX.estado()


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    if not settings.tools_token:
        log.warning("JARVIS_TOOLS_TOKEN vacío: todos los endpoints protegidos darán 503")
    # Arranca el recorrido sin bloquear el arranque del contenedor.
    _INDEX.asegurar(0.0)
    log.info(
        "Jarvis Tools listo (docker_host=%s, nas_root=%s)",
        settings.docker_host,
        settings.nas_root,
    )
