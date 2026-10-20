"""Pruebas de API del ejecutor con el sistema de aprobaciones (Fase C).

Verifican el contrato HTTP y, sobre todo, que el LLM NO puede:
- ejecutar una operacion que exige aprobacion;
- aprobar su propia peticion;
- reutilizar un `approval_id` para ejecutar algo distinto.

Requieren FastAPI: si no esta disponible, se saltan.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app import approvals  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

EXEC_HEADER = {"X-Executor-Token": "token-ejecutor"}
APPROVALS_HEADER = {"X-Approvals-Token": "token-aprobaciones"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Ejecutor con un registro temporal y un host local."""
    root = tmp_path / "raiz"
    root.mkdir()

    hosts_yml = tmp_path / "hosts.yml"
    hosts_yml.write_text(
        "hosts:\n"
        "  - id: localtest\n"
        "    label: Local de pruebas\n"
        "    transport: local\n"
        "    enabled: true\n"
        f'    roots: ["{root}"]\n'
        "projects: []\n",
        encoding="utf-8",
    )
    catalog_yml = tmp_path / "catalog.yml"
    catalog_yml.write_text(
        "version: 1\n"
        "operations:\n"
        "  - id: op_auto\n"
        "    title: Auto\n"
        "    description: Operacion de solo lectura.\n"
        "    risk: read\n"
        "    auto: true\n"
        '    command: ["echo", "auto-ok"]\n'
        "  - id: op_approval\n"
        "    title: Requiere aprobacion\n"
        "    description: Operacion que exige aprobacion humana.\n"
        "    risk: write\n"
        "    auto: false\n"
        '    command: ["echo", "aprobado"]\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("JARVIS_EXECUTOR_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_EXECUTOR_TOKEN", "token-ejecutor")
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVALS_TOKEN", "token-aprobaciones")
    monkeypatch.setenv("JARVIS_EXECUTOR_HOSTS_FILE", str(hosts_yml))
    monkeypatch.setenv("JARVIS_EXECUTOR_CATALOG_FILE", str(catalog_yml))
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_approvals_endpoints_require_their_own_token(api):
    assert api.get("/approvals").status_code == 401                 # sin token
    assert api.get("/approvals", headers=EXEC_HEADER).status_code == 401  # token del LLM
    assert api.get("/approvals", headers={"X-Approvals-Token": "malo"}).status_code == 401
    assert api.get("/approvals", headers=APPROVALS_HEADER).status_code == 200


def test_exec_of_non_auto_op_creates_pending_and_does_not_run(api, monkeypatch):
    """El LLM pide: no se ejecuta, se abre solicitud."""
    calls = []
    import app.main as main_module

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "EJECUTADO", "", 1)

    monkeypatch.setattr(main_module, "run", fake_run)

    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "pending_approval"
    assert data["denied"] is False
    assert data["approval"]["status"] == "pending"
    assert data["approval"]["requested_by"] == "agente"
    assert data["approval"]["approved_by"] is None
    assert calls == [], "la operacion NO debe ejecutarse sin humano"


def test_exec_still_runs_auto_operations(api, monkeypatch):
    calls = []

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "ok", "", 1)

    monkeypatch.setattr("app.main.run", fake_run)
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": "op_auto", "params": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls == [["echo", "auto-ok"]]


def test_exec_ignores_approval_id_from_the_llm(api, monkeypatch):
    """El LLM no puede ejecutar una aprobacion pasando su id en /exec."""
    calls = []

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "EJECUTADO", "", 1)

    monkeypatch.setattr("app.main.run", fake_run)

    # Se crea y aprueba una solicitud (por el canal humano).
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    approved = api.post(
        f"/approvals/{created['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    ).json()["approval"]
    assert approved["status"] == "approved"

    # El LLM intenta colar el approval_id por /exec: se RECHAZA (422),
    # porque /exec no admite campos desconocidos.
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={
            "host": "localtest",
            "operation": "op_approval",
            "params": {},
            "approval_id": created["id"],
        },
    )
    assert resp.status_code == 422
    assert calls == [], "el LLM no puede ejecutar con un approval_id"
    # Y la aprobacion sigue sin gastarse.
    assert api.get(f"/approvals/{created['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]["status"] == "approved"


def test_approval_requires_human_identity(api):
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    resp = api.post(
        f"/approvals/{created['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": ""},
    )
    assert resp.status_code == 400
    assert api.get(f"/approvals/{created['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]["status"] == "pending"


def test_full_flow_approve_then_execute_once(api, monkeypatch):
    calls = []

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "salida real", "", 42)

    monkeypatch.setattr("app.main.run", fake_run)

    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    aid = created["id"]

    api.post(
        f"/approvals/{aid}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )

    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "consumed"
    assert body["stdout"] == "salida real"
    assert body["duration_ms"] == 42
    assert calls == [["echo", "aprobado"]]

    # Segunda ejecucion: rechazada (un solo uso).
    resp2 = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp2.status_code == 409
    assert len(calls) == 1, "no debe ejecutarse dos veces"

    # Estado final y auditoria.
    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "consumed"
    assert final["approved_by"] == "leandro"
    assert final["exit_code"] == 0

    conn = sqlite3.connect(get_settings().audit_db)
    rows = conn.execute(
        "SELECT operation, allowed, ok, exit_code, params_json FROM executions"
    ).fetchall()
    conn.close()
    audited = [r for r in rows if r[0] == "op_approval" and r[1] == 1]
    assert len(audited) == 1
    assert audited[0][3] == 0
    assert created["id"] in audited[0][4]


def test_execute_without_approval_is_refused(api):
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    resp = api.post(f"/approvals/{created['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    assert "pending" in resp.json()["detail"]


def test_rejected_request_cannot_be_executed(api):
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    api.post(
        f"/approvals/{created['id']}/reject",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro", "reason": "no es el momento"},
    )
    resp = api.post(f"/approvals/{created['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    assert "rejected" in resp.json()["detail"]


def test_execute_detects_tampered_row(api):
    """Si alguien altera el argv guardado, la ejecucion se invalida."""
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    aid = created["id"]
    api.post(
        f"/approvals/{aid}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )
    conn = sqlite3.connect(get_settings().audit_db)
    conn.execute(
        "UPDATE approvals SET argv_json=? WHERE id=?",
        (json.dumps(["echo", "OTRA COSA"]), aid),
    )
    conn.commit()
    conn.close()

    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    assert "alterada" in resp.json()["detail"]
    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "failed"


def test_unknown_operation_cannot_create_approval(api):
    resp = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "rm_rf", "params": {}},
    )
    assert resp.status_code == 403
    assert "no permitida" in resp.json()["detail"]


def test_auto_operation_cannot_create_approval(api):
    resp = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_auto", "params": {}},
    )
    assert resp.status_code == 409


def test_disabled_host_cannot_create_approval(api, tmp_path):
    resp = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "noexiste", "operation": "op_approval", "params": {}},
    )
    assert resp.status_code == 403
    assert "host desconocido" in resp.json()["detail"]


def test_ssh_error_leaves_request_failed_without_retry(api, monkeypatch):
    """Fallo de transporte: `failed`, nunca reintentado."""
    attempts = []

    async def failing_run(host, argv, timeout=None):
        attempts.append(argv)
        from app.runner import RunResult

        return RunResult(255, "", "", 5, error="SSH: conexion perdida")

    monkeypatch.setattr("app.main.run", failing_run)

    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    aid = created["id"]
    api.post(
        f"/approvals/{aid}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro"},
    )
    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200
    assert resp.json()["state"] == "failed"
    assert len(attempts) == 1

    # Un segundo intento no ejecuta nada: la aprobacion esta gastada.
    resp2 = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp2.status_code == 409
    assert len(attempts) == 1
    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "failed"
    assert "conexion" in (final["result_error"] or "")


def test_list_and_filter_approvals(api):
    created = api.post(
        "/approvals",
        headers=APPROVALS_HEADER,
        json={"host": "localtest", "operation": "op_approval", "params": {}},
    ).json()["approval"]
    pending = api.get("/approvals?status=pending", headers=APPROVALS_HEADER).json()
    assert pending["count"] == 1
    assert pending["approvals"][0]["id"] == created["id"]
    empty = api.get("/approvals?status=consumed", headers=APPROVALS_HEADER).json()
    assert empty["count"] == 0
    by_host = api.get("/approvals?host=localtest", headers=APPROVALS_HEADER).json()
    assert by_host["count"] == 1
