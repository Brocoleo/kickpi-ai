"""Fixtures de las pruebas del agente.

Todo va a un directorio temporal: la BD real (data_dir) no se toca.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402

API_TOKEN = "token-api-secreto"
APPROVALS_TOKEN = "token-aprobaciones-secreto"
PASSWORD = "clave-de-prueba"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Agente aislado, sin sesion, con token de API."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("JARVIS_ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("JARVIS_APPROVALS_TOKEN", APPROVALS_TOKEN)
    monkeypatch.setenv("JARVIS_ADMIN_USER", "leandro")
    # TestClient usa http://testserver: con Secure=True la cookie no viaja.
    monkeypatch.setenv("JARVIS_COOKIE_SECURE", "false")
    monkeypatch.setenv("JARVIS_STATIC_DIR", str(tmp_path / "static"))
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def browser(client):
    """Cliente con sesion de navegador, Origin y token CSRF validos."""
    response = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    csrf_token = response.json()["csrf_token"]
    assert csrf_token
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}
    )
    client.csrf_token = csrf_token  # type: ignore[attr-defined]
    return client


class FakeExecutor:
    """Doble del servicio de aprobaciones del ejecutor.

    Reproduce lo esencial de sus garantias: el token se exige, la identidad
    la pone el servidor y cada aprobacion se ejecuta como mucho una vez.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.consumed: set[str] = set()
        self.approvals: dict[str, dict] = {
            "abc123": {
                "id": "abc123",
                "status": "pending",
                "host": "macbook",
                "operation": "host_git_diff_stat",
                "params": {"path": "/home/leandro/kickpi-ai"},
                "argv": ["git", "-C", "/home/leandro/kickpi-ai", "diff", "--stat"],
                "risk": "read",
                "risk_description": "Resumen de cambios",
                "requested_by": "agente",
                "approved_by": None,
                "expires_at": "2026-10-20T04:00:00+00:00",
                "created_at": "2026-10-20T03:55:00+00:00",
            }
        }

    async def __call__(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        self.calls.append((method, path, body, params))

        if path == "/approvals" and method == "GET":
            items = list(self.approvals.values())
            if params and params.get("status"):
                items = [a for a in items if a["status"] == params["status"]]
            return {"approvals": items, "count": len(items)}

        if path.startswith("/approvals/"):
            approval_id = path.split("/")[2]
            action = path.split("/")[3] if len(path.split("/")) > 3 else ""
            record = self.approvals.get(approval_id)
            if record is None:
                from app.approvals_client import ApprovalsError

                raise ApprovalsError("aprobacion desconocida", code=404)

            if action == "":
                return {"approval": dict(record)}

            if action == "approve":
                if record["status"] != "pending":
                    from app.approvals_client import ApprovalsError

                    raise ApprovalsError("la solicitud ya no esta pendiente", code=409)
                record["status"] = "approved"
                record["approved_by"] = (body or {}).get("approved_by")
                return {"approval": dict(record)}

            if action == "reject":
                if record["status"] != "pending":
                    from app.approvals_client import ApprovalsError

                    raise ApprovalsError("la solicitud ya no esta pendiente", code=409)
                record["status"] = "rejected"
                return {"approval": dict(record)}

            if action == "execute":
                if record["status"] != "approved":
                    from app.approvals_client import ApprovalsError

                    raise ApprovalsError(
                        f"la aprobacion no esta lista (estado: {record['status']})",
                        code=409,
                    )
                if approval_id in self.consumed:
                    from app.approvals_client import ApprovalsError

                    raise ApprovalsError("la aprobacion ya fue consumida", code=409)
                self.consumed.add(approval_id)
                record["status"] = "consumed"
                return {
                    "ok": True,
                    "state": "consumed",
                    "exit_code": 0,
                    "stdout": "cambios",
                    "duration_ms": 12,
                    "approval_id": approval_id,
                }

        from app.approvals_client import ApprovalsError

        raise ApprovalsError(f"ruta no simulada: {method} {path}", code=404)


@pytest.fixture
def fake_executor(monkeypatch):
    """Instala el doble del ejecutor en el modulo de aprobaciones."""
    import app.approvals_client as module

    fake = FakeExecutor()
    monkeypatch.setattr(module, "_request", fake)
    return fake


@pytest.fixture
def agent_settings(monkeypatch):
    """Configuracion minima para probar el cliente de aprobaciones."""
    from app.config import get_settings

    monkeypatch.setenv("JARVIS_APPROVALS_TOKEN", APPROVALS_TOKEN)
    monkeypatch.setenv("JARVIS_EXECUTOR_URL", "http://ejecutor-falso:8000")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
