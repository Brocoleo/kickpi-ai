"""Operaciones de Docker — SOLO LECTURA.

Por qué NO se usa el cliente de alto nivel de docker-py:

    ContainerCollection.list()  ->  self.get(r['Id'])  ->  inspect_container()
    ContainerCollection.get()   ->  inspect_container()
    APIClient.logs()            ->  _get_result(...)   ->  inspect_container()

Es decir, el SDK inspecciona SIEMPRE. Y `GET /containers/{id}/json` (inspect)
devuelve `Config.Env` de TODOS los contenedores: tokens internos, API key del
LLM y hash de la contraseña de administración.

El docker-socket-proxy bloquea ese endpoint (ver deploy/docker-socket-proxy/
haproxy.cfg), así que aquí se habla la API de Docker directamente sobre los
tres únicos endpoints permitidos:

    GET /containers/json          -> listar contenedores
    GET /containers/{n}/logs      -> logs
    GET /containers/{n}/stats     -> stats

`GET /containers/{id}/json` NO se usa, y por eso `Config.Env` nunca sale del
daemon hacia este servicio.

El daemon acepta nombre o id en la ruta, así que no hace falta resolver ids
(que requeriría inspect).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from docker.errors import DockerException, NotFound

from .config import get_settings
from .sanitize import sanitize_text

# Nombres de contenedor válidos en Docker: [a-zA-Z0-9][a-zA-Z0-9_.-]*
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")

# "Up 3 minutes (healthy)" | "Up 5 seconds (health: starting)"
_HEALTH_RE = re.compile(r"\((health: starting|healthy|unhealthy)\)")
# "Exited (137) 2 minutes ago"
_EXIT_RE = re.compile(r"Exited \((\d+)\)")


def _api_get(path: str, params: dict | None = None) -> bytes:
    """GET contra la API de Docker a través del proxy.

    Solo se admite tcp:// (el proxy). El socket local queda inaccesible a
    propósito: este servicio no debe poder hablar con el daemon sin filtro.
    """
    settings = get_settings()
    host = settings.docker_host
    if not host.startswith("tcp://"):
        raise DockerException(
            "El socket de Docker no está disponible para este servicio. "
            "Se requiere el docker-socket-proxy (tcp://host:2375)."
        )

    base = "http://" + host[len("tcp://"):].rstrip("/")
    version = settings.docker_api_version or "1.45"
    url = f"{base}/v{version}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(f"no encontrado: {path}") from exc
        raise DockerException(f"HTTP {exc.code} en {path}") from exc
    except urllib.error.URLError as exc:
        raise DockerException(f"proxy inaccesible: {exc.reason}") from exc


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise NotFound(f"Contenedor no encontrado: {name}")


def _health_from_status(status: str) -> str | None:
    match = _HEALTH_RE.search(status or "")
    if not match:
        return None
    value = match.group(1)
    # "health: starting" -> "starting"
    return value.split(":", 1)[-1].strip() or None


def _exit_code_from_status(status: str) -> int | None:
    match = _EXIT_RE.search(status or "")
    return int(match.group(1)) if match else None


def _demux_logs(raw: bytes) -> str:
    """El daemon multiplexa stdout/stderr en tramas con cabecera de 8 bytes.

    Cabecera: [tipo_stream(1), 0, 0, 0, tamaño(4, big endian)] + payload.
    Los contenedores con TTY no se multiplexan: se devuelve tal cual.
    """
    if not raw:
        return ""

    chunks: list[str] = []
    offset = 0
    total = len(raw)
    while offset + 8 <= total:
        size = int.from_bytes(raw[offset + 4:offset + 8], "big")
        if size <= 0 or offset + 8 + size > total:
            break
        chunks.append(raw[offset + 8:offset + 8 + size].decode("utf-8", "replace"))
        offset += 8 + size

    if not chunks:
        return raw.decode("utf-8", "replace")
    return "".join(chunks)


def _cpu_percent(raw: dict) -> float | None:
    try:
        cpu = raw["cpu_stats"]
        pre = raw["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", [])) or 1
        if sys_delta <= 0 or cpu_delta < 0:
            return None
        return round((cpu_delta / sys_delta) * online * 100, 2)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def list_containers(all_containers: bool = True) -> dict:
    try:
        payload = _api_get("/containers/json", {"all": 1 if all_containers else 0})
        resp = json.loads(payload)
    except DockerException as exc:
        return {"error": f"Docker no disponible: {exc}"}

    items = []
    for c in resp:
        names = c.get("Names") or []
        name = names[0].lstrip("/") if names else (c.get("Id") or "")[:12]
        status = c.get("Status") or ""
        state = c.get("State") or ""

        ports = []
        for binding in c.get("Ports") or []:
            private = binding.get("PrivatePort")
            if private is None:
                continue
            public = binding.get("PublicPort")
            host_ip = binding.get("IP") or "0.0.0.0"
            ptype = binding.get("Type") or "tcp"
            if public:
                ports.append(f"{host_ip}:{public}->{private}/{ptype}")
            else:
                ports.append(f"{private}/{ptype}")

        items.append(
            {
                "name": name,
                "image": c.get("Image"),
                "state": state,
                "health": _health_from_status(status),
                "exit_code": _exit_code_from_status(status),
                # Requieren GET /containers/{id}/json, que el proxy bloquea.
                "started_at": None,
                "finished_at": None,
                "restart_count": None,
                "ports": sorted(set(ports)),
            }
        )
    return {"containers": items, "count": len(items)}


def container_logs(name: str, tail: int | None = None) -> dict:
    settings = get_settings()
    if tail is None:
        tail = 200
    tail = max(1, min(tail, settings.log_tail_max))

    try:
        _check_name(name)
        raw = _api_get(
            f"/containers/{urllib.parse.quote(name, safe='')}/logs",
            {"stdout": 1, "stderr": 1, "timestamps": 0, "follow": 0, "tail": tail},
        )
    except NotFound:
        return {"error": f"Contenedor no encontrado: {name}"}
    except DockerException as exc:
        return {"error": f"Error al leer logs: {exc}"}

    # Doble límite: el `tail` de Docker ya acota líneas; aquí además
    # recortamos bytes y redactamos secretos.
    text = sanitize_text(_demux_logs(raw), tail=tail, max_bytes=settings.log_bytes_max)
    return {"container": name, "tail": tail, "logs": text}


def container_stats(name: str) -> dict:
    try:
        _check_name(name)
        payload = _api_get(
            f"/containers/{urllib.parse.quote(name, safe='')}/stats",
            {"stream": 0},
        )
        raw = json.loads(payload)
    except NotFound:
        return {"error": f"Contenedor no encontrado: {name}"}
    except DockerException as exc:
        return {"error": f"Error al obtener stats: {exc}"}

    mem = raw.get("memory_stats", {}) or {}
    cpu = raw.get("cpu_stats", {}) or {}
    return {
        "container": name,
        "cpu_percent": _cpu_percent(raw),
        "memory_used_bytes": mem.get("usage"),
        "memory_limit_bytes": mem.get("limit"),
        "pids": (raw.get("pids_stats") or {}).get("current"),
        "read_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def ping() -> bool:
    try:
        _api_get("/_ping")
        return True
    except DockerException:
        return False
