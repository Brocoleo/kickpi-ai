"""Pruebas de la capa de ejecucion: local, SSH, timeouts y reintentos.

Las pruebas SSH usan el host de autoprueba del propio board (127.0.0.1 por
Tailscale) con una clave DEDICADA. Si la clave o el known_hosts no estan
montados, las pruebas SSH se omiten (no fallan).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.registry import Host
from app.runner import run

SSH_DIR = Path(os.environ.get("JARVIS_EXECUTOR_SSH_DIR", "/app/ssh"))
SELFTEST_KEY = SSH_DIR / "id_ed25519_selftest"
SELFTEST_KNOWN = SSH_DIR / "known_hosts_selftest"
SELFTEST_ADDR = os.environ.get("JARVIS_EXECUTOR_SELFTEST_ADDR", "")

requires_ssh = pytest.mark.skipif(
    not (SELFTEST_KEY.is_file() and SELFTEST_KNOWN.is_file() and SELFTEST_ADDR),
    reason="host de autoprueba SSH no configurado",
)


def local_host() -> Host:
    return Host(id="local", label="local", transport="local", enabled=True, roots=("/tmp",))


def ssh_host(**kwargs) -> Host:
    base = dict(
        id="selftest",
        label="selftest",
        transport="ssh",
        enabled=True,
        address=SELFTEST_ADDR,
        port=22,
        user="leandro",
        identity_file=str(SELFTEST_KEY),
        known_hosts=str(SELFTEST_KNOWN),
        roots=("/home/leandro",),
    )
    base.update(kwargs)
    return Host(**base)


# --------------------------------------------------------------------------
# Ejecucion local
# --------------------------------------------------------------------------

async def test_local_ok():
    result = await run(local_host(), ["echo", "hola"])
    assert result.ok
    assert result.stdout.strip() == "hola"
    assert result.exit_code == 0


async def test_local_nonzero_exit():
    result = await run(local_host(), ["sh", "-c", "exit 3"])
    assert not result.ok
    assert result.exit_code == 3


async def test_local_command_not_found():
    result = await run(local_host(), ["comando-que-no-existe-xyz"])
    assert not result.ok
    assert "no encontrado" in (result.error or "")


async def test_local_timeout():
    started = time.monotonic()
    result = await run(local_host(), ["sleep", "10"], timeout=1.0)
    elapsed = time.monotonic() - started
    assert not result.ok
    assert "timeout" in (result.error or "")
    assert elapsed < 5, "el timeout no debe esperar al comando completo"


async def test_local_no_shell_injection():
    """El argv no se interpola en una shell: ; no hace nada."""
    result = await run(local_host(), ["echo", "a; id"])
    assert result.stdout.strip() == "a; id"


# --------------------------------------------------------------------------
# SSH
# --------------------------------------------------------------------------

@requires_ssh
async def test_ssh_ok():
    result = await run(ssh_host(), ["echo", "hola-desde-ssh"])
    assert result.ok, result.error
    assert "hola-desde-ssh" in result.stdout


@requires_ssh
async def test_ssh_nonzero_exit():
    result = await run(ssh_host(), ["sh", "-c", "exit 7"])
    assert not result.ok
    assert result.exit_code == 7


@requires_ssh
async def test_ssh_timeout():
    started = time.monotonic()
    result = await run(ssh_host(), ["sleep", "20"], timeout=2.0)
    elapsed = time.monotonic() - started
    assert not result.ok
    assert elapsed < 10


async def test_ssh_host_disconnected():
    """Host inalcanzable: error claro, sin reintento infinito."""
    host = ssh_host(address="100.64.0.1", known_hosts=str(SELFTEST_KNOWN))
    if not SELFTEST_KNOWN.is_file():
        pytest.skip("sin known_hosts de autoprueba")
    started = time.monotonic()
    result = await run(host, ["echo", "nunca"], timeout=5)
    elapsed = time.monotonic() - started
    assert not result.ok
    assert result.error
    assert elapsed < 30, "no debe tardar tanto en fallar"


async def test_ssh_missing_known_hosts_denied():
    """Sin known_hosts no se conecta nunca (nunca TOFU)."""
    host = ssh_host(known_hosts="/tmp/no-existe-known-hosts")
    result = await run(host, ["echo", "nunca"])
    assert not result.ok
    assert "known_hosts" in (result.error or "")


async def test_ssh_missing_identity_denied():
    host = ssh_host(identity_file="/tmp/no-existe-clave")
    result = await run(host, ["echo", "nunca"])
    assert not result.ok
    assert "clave" in (result.error or "")


async def test_ssh_no_address_denied():
    host = ssh_host(address=None)
    result = await run(host, ["echo", "nunca"])
    assert not result.ok
    assert "direccion" in (result.error or "")


@requires_ssh
async def test_ssh_no_shell_injection():
    """El shell remoto NO debe poder reinterpretar los argumentos."""
    result = await run(ssh_host(), ["echo", "a; id"])
    assert result.ok, result.error
    assert result.stdout.strip() == "a; id", f"inyeccion: {result.stdout!r}"


@requires_ssh
async def test_ssh_command_not_retried(monkeypatch):
    """La orden remota se envia UNA sola vez.

    Aunque la conexion se reintente, la orden ya entregada no se repite.
    Contamos las ejecuciones con un wrapper remoto que escribe en un fichero.
    """
    marker = "/tmp/jarvis_exec_test_counter"
    host = ssh_host()
    # Limpieza previa
    await run(host, ["rm", "-f", marker])
    result = await run(host, ["sh", "-c", f"echo x >> {marker}; echo ok"])
    assert result.ok
    count = await run(host, ["sh", "-c", f"wc -l < {marker}"])
    assert int(count.stdout.strip()) == 1, "la orden se ejecuto mas de una vez"
    await run(host, ["rm", "-f", marker])
