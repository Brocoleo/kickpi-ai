"""Policy Engine: deniega por defecto.

Una operacion solo se ejecuta si TODAS estas reglas se cumplen:

1. El host existe y esta habilitado.
2. La operacion existe en el catalogo (lo que no esta, no existe).
3. La operacion esta marcada como auto-aprobable (en V0.3 no hay UI de
   aprobacion: lo demas se deniega).
4. La operacion esta permitida en ese host.
5. Los parametros de ruta caen dentro de las raices autorizadas del host,
   RESUELTAS en el propio host (symlinks incluidos).
6. Los proyectos referenciados existen, estan habilitados y pertenecen al host.

El LLM nunca envia una linea de shell: solo el id de una operacion del
catalogo y sus parametros tipados.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .registry import Host, Operation, Registry
from .runner import resolve_path, run


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    argv: list[str] | None = None
    resolved_path: str | None = None
    timeout: int | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "argv": self.argv,
            "resolved_path": self.resolved_path,
            "timeout": self.timeout,
        }


def _deny(reason: str) -> Decision:
    return Decision(allowed=False, reason=reason)


def _within(path: str, root: str) -> bool:
    """path esta dentro de root (o es root)."""
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


# Cache de las raices resueltas: abrir una conexion SSH por cada raiz y en
# cada operacion era un desperdicio (y ralentizaba las comprobaciones).
# La clave incluye las raices del host: si cambian, la cache se invalida
# (importante en pruebas, donde cada caso usa un tmp_path distinto).
_ROOTS_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, list[str]]] = {}
_ROOTS_TTL = 300.0


async def _resolved_roots(host: Host) -> list[str]:
    """Raices autorizadas, resueltas en el host (sigue symlinks)."""
    now = time.monotonic()
    key = (host.id, host.roots)
    cached = _ROOTS_CACHE.get(key)
    if cached is not None and now - cached[0] < _ROOTS_TTL:
        return cached[1]

    roots: list[str] = []
    for root in host.roots:
        resolved, _error = await resolve_path(host, root)
        roots.append(resolved if resolved else root)
    _ROOTS_CACHE[key] = (now, roots)
    return roots


async def check(
    registry: Registry,
    host_id: str,
    operation_id: str,
    params: dict,
) -> Decision:
    """Evalua una peticion. No ejecuta nada."""
    host = registry.host(host_id)
    if host is None:
        return _deny(f"host desconocido: {host_id}")
    if not host.enabled:
        return _deny(f"host no habilitado: {host_id}")

    operation = registry.operation(operation_id)
    if operation is None:
        # Deny by default: lo que no esta en el catalogo no se ejecuta.
        return _deny(f"operacion no permitida: {operation_id}")
    if not operation.auto:
        return _deny(
            f"la operacion {operation_id} no es auto-aprobable; requiere "
            "aprobacion explicita (no disponible en V0.3)"
        )
    if operation.hosts and "*" not in operation.hosts and host.id not in operation.hosts:
        return _deny(f"la operacion {operation_id} no aplica a {host_id}")

    argv = list(operation.command)
    resolved_path: str | None = None
    timeout = operation.timeout

    # --- Parametro de proyecto ---
    if operation.project_param:
        project_id = str(params.get(operation.project_param) or "").strip()
        if not project_id:
            return _deny(f"falta el parametro '{operation.project_param}'")
        project = registry.project(project_id)
        if project is None:
            return _deny(f"proyecto desconocido: {project_id}")
        if not project.enabled:
            return _deny(f"proyecto deshabilitado: {project_id}")
        if project.host != host.id:
            return _deny(f"el proyecto {project_id} pertenece a {project.host}, no a {host_id}")
        if not project.test_command:
            return _deny(f"el proyecto {project_id} no define suite de tests")

        path = project.path
        resolved_path, path_error = await resolve_path(host, path)
        if resolved_path is None:
            return _deny(
                f"no se pudo resolver la ruta del proyecto {project_id} "
                f"({path}): {path_error}"
            )
        roots = await _resolved_roots(host)
        if not any(_within(resolved_path, root) for root in roots):
            return _deny(
                f"la ruta del proyecto {project_id} ({resolved_path}) esta fuera "
                "de las raices autorizadas"
            )
        argv = list(project.test_command)
        # `env -C <dir>` cambia el directorio de trabajo SIN usar una shell
        # (el canal exec de SSH no admite cwd). Necesario porque las suites
        # de tests usan rutas relativas a la raiz del proyecto.
        argv = ["env", "-C", resolved_path] + argv
        timeout = timeout or 180

    # --- Parametro de ruta libre ---
    elif operation.path_param:
        raw = str(params.get(operation.path_param) or "").strip()
        if not raw:
            return _deny(f"falta el parametro '{operation.path_param}'")
        if ".." in raw.split("/"):
            return _deny("la ruta no puede contener '..'")
        if not raw.startswith("/"):
            return _deny("la ruta debe ser absoluta")

        resolved_path, path_error = await resolve_path(host, raw)
        if resolved_path is None:
            return _deny(f"no se pudo resolver la ruta {raw}: {path_error}")
        roots = await _resolved_roots(host)
        if not roots:
            return _deny(f"el host {host_id} no tiene raices autorizadas")
        if not any(_within(resolved_path, root) for root in roots):
            return _deny(
                f"ruta fuera de las raices autorizadas: {resolved_path} "
                f"(permitidas: {', '.join(roots)})"
            )
        argv = [part.replace("{path}", resolved_path) for part in argv]

    # --- Parametro entero acotado ---
    if operation.int_param:
        raw_int = params.get(operation.int_param)
        if raw_int is None:
            return _deny(f"falta el parametro '{operation.int_param}'")
        try:
            value = int(raw_int)
        except (TypeError, ValueError):
            return _deny(f"'{operation.int_param}' debe ser un entero")
        if value < 1 or value > operation.int_param_max:
            return _deny(
                f"'{operation.int_param}' fuera de rango (1..{operation.int_param_max})"
            )
        argv = [part.replace("{" + operation.int_param + "}", str(value)) for part in argv]

    if not argv:
        return _deny("la operacion no define comando")
    if any("{" in part and "}" in part for part in argv):
        # Quedo un marcador sin resolver: no se ejecuta a medias.
        return _deny("la operacion tiene parametros sin resolver")

    return Decision(
        allowed=True,
        reason="ok",
        argv=argv,
        resolved_path=resolved_path,
        timeout=timeout,
    )
