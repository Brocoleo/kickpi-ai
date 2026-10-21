"""Ejecucion de comandos: local o por SSH.

Reglas duras:
- Nunca se construye una linea de shell: se pasan argv. Sin inyeccion.
- La clave de host se verifica de forma estricta contra known_hosts.
- Solo se reintenta la FASE DE CONEXION. Una orden ya enviada no se repite,
  para no ejecutar dos veces la misma accion.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from dataclasses import dataclass

import asyncssh

from .config import get_settings
from .registry import Host


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class RunnerError(RuntimeError):
    """Error al ejecutar (conexion, timeout o remoto)."""


async def _run_local(argv: list[str], timeout: float) -> RunResult:
    """Ejecucion local sin shell."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
        )
    except FileNotFoundError as exc:
        return RunResult(127, "", "", 0, error=f"comando no encontrado: {argv[0]}")
    except OSError as exc:
        return RunResult(127, "", "", 0, error=f"no se pudo ejecutar: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RunResult(
            124, "", "", int((loop.time() - started) * 1000),
            error=f"timeout tras {timeout:g}s",
        )

    duration = int((loop.time() - started) * 1000)
    return RunResult(
        proc.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        duration,
    )


async def _run_ssh_probe(host: Host, argv: list[str], timeout: float) -> RunResult:
    """Ejecución SSH SOLO para health-check: timeout agresivo, SIN reintentos.
    Si el host no responde en 3 s, se marca offline y no bloquea /equipos.
    """
    settings = get_settings()
    loop = asyncio.get_running_loop()
    started = loop.time()

    if not host.address:
        return RunResult(255, "", "", 0, error="host sin direccion configurada")
    if not host.identity_file or not os.path.isfile(host.identity_file):
        return RunResult(255, "", "", 0, error="clave SSH del host no disponible")
    if not host.known_hosts or not os.path.isfile(host.known_hosts):
        return RunResult(255, "", "", 0, error="known_hosts del host no disponible")

    command = " ".join(shlex.quote(arg) for arg in argv)

    try:
        async with asyncssh.connect(
            host.address,
            port=host.port,
            username=host.user,
            client_keys=[host.identity_file],
            known_hosts=host.known_hosts,
            connect_timeout=timeout,
            login_timeout=timeout,
            agent_path=None,
        ) as conn:
            result = await conn.run(command, timeout=timeout, check=False)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            return RunResult(
                result.exit_status if result.exit_status is not None else 0,
                stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout,
                stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr,
                int((loop.time() - started) * 1000),
            )
    except asyncssh.PermissionDenied:
        return RunResult(255, "", "", int((loop.time() - started) * 1000), error="SSH: autenticacion rechazada")
    except asyncssh.HostKeyNotVerifiable as exc:
        return RunResult(255, "", "", int((loop.time() - started) * 1000), error=f"SSH: host key no verificada ({exc})")
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        return RunResult(255, "", "", int((loop.time() - started) * 1000), error=f"SSH: {type(exc).__name__}: {exc}")
    except Exception as exc:
        return RunResult(255, "", "", int((loop.time() - started) * 1000), error=f"SSH: error inesperado {type(exc).__name__}: {exc}")


async def _run_ssh(host: Host, argv: list[str], timeout: float) -> RunResult:
    """Ejecucion remota por SSH con verificacion estricta de host key."""
    settings = get_settings()
    loop = asyncio.get_running_loop()
    started = loop.time()

    if not host.address:
        return RunResult(255, "", "", 0, error="host sin direccion configurada")
    if not host.identity_file or not os.path.isfile(host.identity_file):
        return RunResult(255, "", "", 0, error="clave SSH del host no disponible")
    if not host.known_hosts or not os.path.isfile(host.known_hosts):
        return RunResult(255, "", "", 0, error="known_hosts del host no disponible")

    last_error: str | None = None
    # El canal "exec" de SSH envia UNA CADENA que el shell del usuario remoto
    # interpreta. Por eso cada argumento se cita con shlex.quote: el shell
    # remoto ve exactamente el argv que queremos y no puede reinterpretar
    # nada (ni ';' ni '&&' ni espaciosInjection).
    command = " ".join(shlex.quote(arg) for arg in argv)

    # Solo se reintenta la conexion. Despues, jamas.
    for attempt in range(settings.connect_retries + 1):
        try:
            async with asyncssh.connect(
                host.address,
                port=host.port,
                username=host.user,
                client_keys=[host.identity_file],
                known_hosts=host.known_hosts,
                connect_timeout=settings.connect_timeout,
                login_timeout=settings.connect_timeout,
                # Sin forwarding, sin PTY, sin agente.
                agent_path=None,
            ) as conn:
                result = await conn.run(command, timeout=timeout, check=False)
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                return RunResult(
                    result.exit_status if result.exit_status is not None else 0,
                    stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout,
                    stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr,
                    int((loop.time() - started) * 1000),
                )
        except asyncssh.PermissionDenied:
            last_error = "SSH: autenticacion rechazada"
            break
        except asyncssh.HostKeyNotVerifiable as exc:
            # Nunca caemos a TOFU: la clave debe estar en known_hosts.
            last_error = f"SSH: host key no verificada ({exc})"
            break
        except asyncssh.ConnectionLost as exc:
            last_error = f"SSH: conexion perdida ({exc})"
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
            last_error = f"SSH: {type(exc).__name__}: {exc}"
        except ValueError as exc:
            # p.ej. asyncssh sin USER/LOGNAME en el entorno.
            last_error = f"SSH: {exc}"
        except Exception as exc:  # nunca debe colgarse ni propagar un 500
            last_error = f"SSH: error inesperado {type(exc).__name__}: {exc}"
        if attempt < settings.connect_retries:
            await asyncio.sleep(1.5 * (attempt + 1))

    return RunResult(
        255, "", "", int((loop.time() - started) * 1000), error=last_error or "SSH: fallo"
    )


async def run(host: Host, argv: list[str], timeout: float | None = None) -> RunResult:
    """Ejecuta argv en el host indicado (local o SSH)."""
    settings = get_settings()
    limit = float(timeout or settings.command_timeout)
    if host.is_local:
        return await _run_local(argv, limit)
    return await _run_ssh(host, argv, limit)


async def resolve_path(host: Host, path: str) -> tuple[str | None, str | None]:
    """Resuelve la ruta REAL en el host (sigue symlinks).

    Se calcula en el host destino: el executor no comparte filesystem con el
    equipo remoto, y un symlink dentro de una raiz autorizada puede apuntar
    fuera de ella.

    Devuelve (ruta_resuelta, error). El error distingue "el filtro remoto
    lo ha rechazado" de "no existe", para poder explicar el motivo.
    """
    if host.is_local:
        resolved = os.path.realpath(path)
        if not os.path.exists(resolved):
            return None, f"la ruta no existe: {resolved}"
        return resolved, None

    result = await run(host, ["readlink", "-f", "--", path], timeout=10)
    if result.error:
        return None, result.error
    if not result.ok:
        # El forced command del equipo destino escribe el motivo en stderr.
        detail = (result.stderr or "").strip().splitlines()
        motivo = detail[-1] if detail else f"readlink devolvio {result.exit_code}"
        return None, motivo
    resolved = result.stdout.strip()
    if not resolved:
        return None, (
            f"no se pudo resolver la ruta {path}: no existe o no es "
            "alcanzable por el usuario de servicio"
        )
    return resolved, None


def command_exists_local(argv: list[str]) -> bool:
    return bool(argv) and shutil.which(argv[0]) is not None
