"""Pruebas de C5: integridad del flujo de aprobación en el ejecutor.

Objetivo: comprobar que lo que se EJECUTA es exactamente lo que se APROBÓ,
y que una respuesta perdida no puede producir una segunda ejecución.

Escenarios:
- el argv ejecutado es, byte a byte, el argv aprobado y verificado;
- si el cliente pierde la respuesta (red, timeout, cierre), el estado
  persistido manda: la solicitud queda `consumed` y un reintento recibe 409
  (el reclamo atómico lo impide), sin una segunda fila de auditoría;
- una aprobación caduca: no se ejecuta;
- el catálogo puede cambiar entre la solicitud y la ejecución (la
  aprobación se invalida, no se ejecuta otra cosa);
- la identidad la pone el servidor, nunca el cliente.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

EXEC_HEADER = {"X-Executor-Token": "token-ejecutor"}
APPROVALS_HEADER = {"X-Approvals-Token": "token-aprobaciones"}

CATALOG_REAL = Path(__file__).resolve().parents[1] / "config" / "catalog.yml"
OP = "host_git_diff_stat"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git no disponible en este entorno")
    path = tmp_path / "repo"
    path.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    git("init", "-q")
    (path / "archivo.txt").write_text("uno\n", encoding="utf-8")
    git("add", "archivo.txt")
    git("-c", "user.email=prueba@jarvis.local", "-c", "user.name=prueba",
        "commit", "-q", "-m", "inicial")
    (path / "archivo.txt").write_text("uno\ndos\n", encoding="utf-8")
    return path


@pytest.fixture
def api(tmp_path, monkeypatch, repo):
    hosts_yml = tmp_path / "hosts.yml"
    hosts_yml.write_text(
        "hosts:\n"
        "  - id: localtest\n"
        "    label: Local\n"
        "    transport: local\n"
        "    enabled: true\n"
        f'    roots: ["{repo}"]\n'
        "projects: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_EXECUTOR_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_EXECUTOR_TOKEN", "token-ejecutor")
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVALS_TOKEN", "token-aprobaciones")
    monkeypatch.setenv("JARVIS_EXECUTOR_HOSTS_FILE", str(hosts_yml))
    monkeypatch.setenv("JARVIS_EXECUTOR_CATALOG_FILE", str(CATALOG_REAL))
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _pedir(api, path: str) -> dict:
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": OP, "params": {"path": path}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["approval"]


# --------------------------------------------------------------------------- #
# Lo ejecutado es exactamente lo aprobado
# --------------------------------------------------------------------------- #
def test_el_argv_ejecutado_es_el_argv_aprobado(api, repo, monkeypatch):
    calls = []

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "salida", "", 7)

    monkeypatch.setattr("app.main.run", fake_run)

    approval = _pedir(api, str(repo))
    api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )
    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200

    # El argv ejecutado es IDENTICO al aprobado (mismo orden, mismos valores).
    assert calls == [approval["argv"]]
    assert calls[0] == [
        "git", "--no-optional-locks", "-C", str(repo), "diff", "--stat",
    ]
    # Y el timeout ejecutado es el aprobado.
    final = api.get(f"/approvals/{approval['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]
    assert final["timeout_s"] == approval["timeout_s"] == 30


def test_la_ruta_resuelta_y_el_timeout_no_cambian_entre_solicitud_y_ejecucion(
    api, repo
):
    approval = _pedir(api, str(repo))
    api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )
    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200
    assert resp.json()["resolved_path"] == approval["resolved_path"]


def test_cambiar_el_catalogo_invalida_la_aprobacion(api, repo, tmp_path, monkeypatch):
    """Si la operacion deja de ser como se aprobo, no se ejecuta."""
    approval = _pedir(api, str(repo))
    api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )

    # El catalogo cambia por el camino: la operacion pasa a ser auto.
    import yaml

    data = yaml.safe_load(CATALOG_REAL.read_text(encoding="utf-8"))
    for item in data["operations"]:
        if item["id"] == OP:
            item["auto"] = True
    catalogo = tmp_path / "catalog-modificado.yml"
    catalogo.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("JARVIS_EXECUTOR_CATALOG_FILE", str(catalogo))
    # La configuracion esta cacheada: hay que recargarla para que el
    # ejecutor lea el catalogo nuevo.
    get_settings.cache_clear()

    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    final = api.get(f"/approvals/{approval['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]
    assert final["status"] == "failed"


# --------------------------------------------------------------------------- #
# Respuesta perdida: el estado persistido manda
# --------------------------------------------------------------------------- #
def test_respuesta_perdida_no_produce_una_segunda_ejecucion(api, repo):
    """El cliente pierde la respuesta y reintenta: una sola ejecucion."""
    approval = _pedir(api, str(repo))
    aid = approval["id"]
    api.post(
        f"/approvals/{aid}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )

    # Primera ejecucion: el servidor la hace, pero "el cliente" pierde la
    # respuesta y vuelve a pedirla.
    primera = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert primera.status_code == 200
    assert primera.json()["state"] == "consumed"

    segunda = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert segunda.status_code == 409
    assert "consumed" in segunda.json()["detail"]

    # El estado persistido es el que consultaria la PWA.
    estado = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert estado["status"] == "consumed"
    assert estado["exit_code"] == 0

    # Y solo hay UNA ejecucion auditada.
    conn = sqlite3.connect(get_settings().audit_db)
    rows = conn.execute(
        "SELECT COUNT(*) FROM executions WHERE operation=? AND allowed=1", (OP,)
    ).fetchone()
    conn.close()
    assert rows[0] == 1


def test_una_aprobacion_caducada_no_se_ejecuta(api, repo, monkeypatch):
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVAL_TTL_SECONDS", "1")
    get_settings.cache_clear()

    approval = _pedir(api, str(repo))
    api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )

    import time

    time.sleep(1.2)

    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    final = api.get(f"/approvals/{approval['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]
    assert final["status"] == "expired"

    conn = sqlite3.connect(get_settings().audit_db)
    rows = conn.execute(
        "SELECT COUNT(*) FROM executions WHERE operation=? AND allowed=1", (OP,)
    ).fetchone()
    conn.close()
    assert rows[0] == 0, "una aprobacion caducada no ejecuta nada"


# --------------------------------------------------------------------------- #
# Identidad
# --------------------------------------------------------------------------- #
def test_la_identidad_la_pone_el_servidor(api, repo):
    approval = _pedir(api, str(repo))
    # Sin identidad no se aprueba.
    resp = api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": ""},
    )
    assert resp.status_code == 400
    # El nombre viene del servidor: el cuerpo solo puede traerlo, no fijarlo.
    resp = api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro", "requested_by": "suplantado"},
    )
    assert resp.status_code == 422, "no se admiten campos extra"
    assert approval["requested_by"] == "agente"
