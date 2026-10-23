"""API del servicio de herramientas Jarvis (solo lectura).

Todos los endpoints (salvo /health) exigen el token interno
`JARVIS_TOOLS_TOKEN`, que solo conoce el agente.
"""
import hmac
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

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
    cache_path=get_settings().nas_index_cache,
)

# Extensiones que se pueden mostrar dentro de la PWA. Todo lo demás se
# descarga como binario: un HTML/SVG subido a la NAS nunca debe ejecutarse
# en el origen de Jarvis.
_INLINE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}

app = FastAPI(
    title="Jarvis Tools",
    version="0.3.0",
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
            "GET /nas/list",
            "GET /nas/file",
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


@app.get("/nas/list", tags=["nas"], dependencies=[Depends(require_token)])
def nas_list(
    path: str = Query(default="", max_length=500, description="Carpeta relativa"),
    limit: int = Query(default=300, ge=1, le=1000),
) -> dict:
    """Lista una carpeta del NAS: subcarpetas primero, luego archivos."""
    return _INDEX.listar(path, limite=limit)


@app.get("/nas/file", tags=["nas"], dependencies=[Depends(require_token)])
def nas_file(
    path: str = Query(min_length=1, max_length=500, description="Archivo relativo"),
    download: bool = Query(default=False),
) -> FileResponse:
    """Sirve un archivo de la NAS (solo lectura) para miniaturas o descarga.

    Imágenes y PDF se muestran en línea; cualquier otro tipo se fuerza como
    descarga. Un HTML con scripts servido desde este origen sería XSS.
    """
    settings = get_settings()
    try:
        destino = _INDEX.archivo(path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ruta no válida") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archivo no encontrado") from exc

    tamano = destino.stat().st_size
    if tamano > settings.nas_file_max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"El archivo supera {settings.nas_file_max_mb} MB",
        )

    inline = _INLINE_TYPES.get(destino.suffix.lower())
    if inline and not download:
        media_type = inline
        filename = None
    else:
        media_type = "application/octet-stream"
        filename = destino.name

    return FileResponse(
        destino,
        media_type=media_type,
        filename=filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )


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
