"""Pruebas de `/hosts/{id}/status` — la sonda que alimenta `/equipos`.

Este endpoint devolvía **HTTP 500** para el host local: la rama
`transport: local` calculaba el resultado de `uname` pero nunca lo asignaba
a `result`, así que al guardar la caché saltaba un `UnboundLocalError`. Como
`kickpi` es precisamente el host local, el panel de equipos daba error
siempre y la causa real (el nombre del host en los logs) quedaba oculta.

Se cubre: host local online, host ssh online, host ssh con error, host
deshabilitado, host desconocido y reutilización de la caché corta.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.runner import RunResult  # noqa: E402

TOKEN = "token-ejecutor"
HOSTS_YAML = """
version: 1
hosts:
  - id: local
    label: "Local"
    transport: local
    enabled: true
    user: leandro
    roots: [/tmp/raiz]
  - id: remoto
    label: "Remoto"
    transport: ssh
    enabled: true
    address: 100.64.0.9
    user: leandro
    roots: [/tmp/raiz]
  - id: dormido
    label: "Dormido"
    transport: ssh
    enabled: false
    address: 100.64.0.8
    user: leandro
    roots: [/tmp/raiz]
projects: []
"""


def _ok(stdout: str = "Linux 6.1") -> RunResult:
    return RunResult(exit_code=0, stdout=stdout, stderr="", duration_ms=5)


def _ko(error: str) -> RunResult:
    return RunResult(exit_code=255, stdout="", stderr="", duration_ms=5, error=error)


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    hosts_file = tmp_path / "hosts.yml"
    hosts_file.write_text(HOSTS_YAML, encoding="utf-8")

    monkeypatch.setenv("JARVIS_EXECUTOR_HOSTS_FILE", str(hosts_file))
    monkeypatch.setenv("JARVIS_EXECUTOR_CATALOG_FILE", str(tmp_path / "catalog.yml"))
    monkeypatch.setenv("JARVIS_EXECUTOR_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_EXECUTOR_TOKEN", TOKEN)
    get_settings.cache_clear()
    main._STATUS_CACHE.clear()

    with TestClient(main.app) as c:
        yield c

    main._STATUS_CACHE.clear()
    get_settings.cache_clear()


def _status(cliente, host_id: str) -> dict:
    resp = cliente.get(
        f"/hosts/{host_id}/status", headers={"X-Executor-Token": TOKEN}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_host_local_responde(cliente, monkeypatch):
    """El caso que fallaba: host local (mismo contenedor, sin SSH)."""

    async def falso_local(argv, timeout):
        assert argv == ["uname", "-sr"]
        return _ok("Linux 6.1.0-kickpi")

    monkeypatch.setattr(main, "_run_local", falso_local)

    cuerpo = _status(cliente, "local")
    assert cuerpo["online"] is True
    assert cuerpo["reason"] == "ok"
    assert cuerpo["kernel"] == "Linux 6.1.0-kickpi"
    assert isinstance(cuerpo["latency_ms"], int)


def test_host_local_con_error(cliente, monkeypatch):
    async def falso_local(argv, timeout):
        return _ko("OSError: no se pudo ejecutar uname")

    monkeypatch.setattr(main, "_run_local", falso_local)

    cuerpo = _status(cliente, "local")
    assert cuerpo["online"] is False
    assert "OSError" in cuerpo["reason"]


def test_host_remoto_online(cliente, monkeypatch):
    async def falso_ssh(host, argv, timeout):
        assert host.id == "remoto"
        return _ok("Linux 6.1.0-desktop")

    monkeypatch.setattr(main, "_run_ssh_probe", falso_ssh)

    cuerpo = _status(cliente, "remoto")
    assert cuerpo["online"] is True
    assert cuerpo["kernel"] == "Linux 6.1.0-desktop"


def test_host_remoto_inaccesible(cliente, monkeypatch):
    async def falso_ssh(host, argv, timeout):
        return _ko("SSH: TimeoutError: ")

    monkeypatch.setattr(main, "_run_ssh_probe", falso_ssh)

    cuerpo = _status(cliente, "remoto")
    assert cuerpo["online"] is False
    assert "TimeoutError" in cuerpo["reason"]


def test_host_deshabilitado_no_sonda(cliente, monkeypatch):
    async def explota(*args, **kwargs):
        raise AssertionError("un host deshabilitado no debe sondearse")

    monkeypatch.setattr(main, "_run_ssh_probe", explota)

    cuerpo = _status(cliente, "dormido")
    assert cuerpo["online"] is False
    assert "deshabilitado" in cuerpo["reason"]


def test_host_desconocido_da_404(cliente):
    resp = cliente.get(
        "/hosts/inexistente/status", headers={"X-Executor-Token": TOKEN}
    )
    assert resp.status_code == 404


def test_sin_token_da_401(cliente):
    assert cliente.get("/hosts/local/status").status_code == 401


def test_cache_evita_repetir_la_sonda(cliente, monkeypatch):
    llamadas = {"n": 0}

    async def falso_local(argv, timeout):
        llamadas["n"] += 1
        return _ok()

    monkeypatch.setattr(main, "_run_local", falso_local)

    _status(cliente, "local")
    _status(cliente, "local")
    _status(cliente, "local")
    assert llamadas["n"] == 1
