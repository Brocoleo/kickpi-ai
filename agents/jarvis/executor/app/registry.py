"""Carga del registro de hosts, del catalogo de operaciones y de los proyectos.

Todo es declarativo y vive en YAML de solo lectura: anadir un equipo o una
operacion no exige tocar codigo, y el Policy Engine trabaja sobre lo que hay
aqui (denegar por defecto).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import get_settings


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    transport: str  # "local" | "ssh"
    enabled: bool = False
    address: str | None = None
    port: int = 22
    user: str = "leandro"
    identity_file: str | None = None
    known_hosts: str | None = None
    roots: tuple[str, ...] = ()
    os: str = ""

    @property
    def is_local(self) -> bool:
        return self.transport == "local"


@dataclass(frozen=True)
class Operation:
    id: str
    title: str
    command: tuple[str, ...]
    hosts: tuple[str, ...] = ("*",)
    auto: bool = False
    risk: str = "read"  # read | execute
    path_param: str | None = None
    project_param: str | None = None
    int_param: str | None = None
    int_param_max: int = 50
    timeout: int | None = None
    description: str = ""


@dataclass(frozen=True)
class Project:
    id: str
    label: str
    host: str
    path: str
    test_command: tuple[str, ...] | None = None
    enabled: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class Registry:
    hosts: dict[str, Host]
    operations: dict[str, Operation]
    projects: dict[str, Project]

    def host(self, host_id: str) -> Host | None:
        return self.hosts.get(host_id)

    def operation(self, op_id: str) -> Operation | None:
        return self.operations.get(op_id)

    def project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)


def _read_yaml(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        return {}
    with file.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# Cache con clave de invalidacion por mtime: si alguien edita hosts.yml o
# catalog.yml (montados de solo lectura desde el repo), el cambio se aplica
# sin reiniciar el contenedor.
_CACHE: tuple[float, float, "Registry"] | None = None


def _sources_mtime(settings) -> float:
    newest = 0.0
    for path in (settings.hosts_file, settings.catalog_file):
        try:
            newest = max(newest, Path(path).stat().st_mtime)
        except OSError:
            continue
    return newest


def load_registry() -> Registry:
    global _CACHE
    settings = get_settings()
    mtime = _sources_mtime(settings)
    now = time.monotonic()
    if _CACHE is not None and _CACHE[0] == mtime and now - _CACHE[1] < 5.0:
        return _CACHE[2]

    hosts_raw = _read_yaml(settings.hosts_file)
    catalog_raw = _read_yaml(settings.catalog_file)

    hosts: dict[str, Host] = {}
    for item in hosts_raw.get("hosts") or []:
        host_id = str(item.get("id") or "").strip()
        if not host_id:
            continue
        hosts[host_id] = Host(
            id=host_id,
            label=str(item.get("label") or host_id),
            transport=str(item.get("transport") or "ssh"),
            enabled=bool(item.get("enabled", False)),
            address=item.get("address") or None,
            port=int(item.get("port") or 22),
            user=str(item.get("user") or "leandro"),
            identity_file=item.get("identity_file") or None,
            known_hosts=item.get("known_hosts") or None,
            roots=tuple(str(r) for r in (item.get("roots") or [])),
            os=str(item.get("os") or ""),
        )

    operations: dict[str, Operation] = {}
    for item in catalog_raw.get("operations") or []:
        op_id = str(item.get("id") or "").strip()
        if not op_id:
            continue
        operations[op_id] = Operation(
            id=op_id,
            title=str(item.get("title") or op_id),
            command=tuple(str(c) for c in (item.get("command") or [])),
            hosts=tuple(str(h) for h in (item.get("hosts") or ["*"])),
            auto=bool(item.get("auto", False)),
            risk=str(item.get("risk") or "read"),
            path_param=item.get("path_param") or None,
            project_param=item.get("project_param") or None,
            int_param=item.get("int_param") or None,
            int_param_max=int(item.get("int_param_max") or 50),
            timeout=int(item["timeout"]) if item.get("timeout") else None,
            description=str(item.get("description") or ""),
        )

    projects: dict[str, Project] = {}
    for item in (hosts_raw.get("projects") or []):
        proj_id = str(item.get("id") or "").strip()
        if not proj_id:
            continue
        test_cmd = item.get("test_command")
        projects[proj_id] = Project(
            id=proj_id,
            label=str(item.get("label") or proj_id),
            host=str(item.get("host") or ""),
            path=str(item.get("path") or ""),
            test_command=tuple(str(c) for c in test_cmd) if test_cmd else None,
            enabled=bool(item.get("enabled", True)),
            extra={k: v for k, v in item.items() if k not in {"id", "label", "host", "path", "test_command", "enabled"}},
        )

    registry = Registry(hosts=hosts, operations=operations, projects=projects)
    _CACHE = (mtime, now, registry)
    return registry
