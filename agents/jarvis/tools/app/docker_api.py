"""Operaciones de Docker — SOLO LECTURA.

Cualquier función que modifique estado está deliberadamente ausente.
El cliente se crea contra el socket montado (por defecto /var/run/docker.sock).
"""
from datetime import datetime, timezone

import docker
from docker.errors import DockerException, NotFound

from .config import get_settings
from .sanitize import sanitize_text

_client: docker.DockerClient | None = None


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.DockerClient(base_url=get_settings().docker_host, timeout=10)
    return _client


def _iso(ts) -> str | None:
    """Normaliza timestamps de Docker (str ISO o epoch) a ISO-8601 UTC."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    text = str(ts).strip()
    # Docker devuelve p.ej. "2026-10-19T04:13:00.123456789Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except ValueError:
        return str(ts)


def list_containers(all_containers: bool = True) -> dict:
    try:
        containers = _get_client().containers.list(all=all_containers)
    except DockerException as exc:
        return {"error": f"Docker no disponible: {exc}"}

    items = []
    for c in sorted(containers, key=lambda x: x.name):
        try:
            attrs = c.attrs
        except Exception:  # contenedor desaparecido a mitad
            continue
        state = attrs.get("State", {})

        ports = []
        network_ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
        for private_port, bindings in network_ports.items():
            for binding in bindings or []:
                host = binding.get("HostIp") or "0.0.0.0"
                ports.append(f"{host}:{binding.get('HostPort')}->{private_port}")

        items.append(
            {
                "name": c.name,
                "image": attrs.get("Config", {}).get("Image"),
                "state": state.get("Status"),
                "health": (state.get("Health") or {}).get("Status"),
                "exit_code": state.get("ExitCode"),
                "started_at": _iso(state.get("StartedAt")),
                "finished_at": _iso(state.get("FinishedAt")),
                "restart_count": attrs.get("RestartCount"),
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
        container = _get_client().containers.get(name)
        raw = container.logs(tail=tail, timestamps=False).decode("utf-8", "replace")
    except NotFound:
        return {"error": f"Contenedor no encontrado: {name}"}
    except DockerException as exc:
        return {"error": f"Error al leer logs: {exc}"}

    # Doble límite: el `tail` de Docker ya acota líneas; aquí además
    # recortamos bytes y redactamos secretos.
    text = sanitize_text(raw, tail=tail, max_bytes=settings.log_bytes_max)
    return {"container": name, "tail": tail, "logs": text}


def container_stats(name: str) -> dict:
    try:
        container = _get_client().containers.get(name)
        # stream=False toma una única muestra.
        raw = container.stats(stream=False)
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


def ping() -> bool:
    try:
        return _get_client().ping()
    except DockerException:
        return False
