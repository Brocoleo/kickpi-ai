"""Regresión: los endpoints y herramientas existentes siguen funcionando."""
from __future__ import annotations

from conftest import API_TOKEN, PASSWORD


def test_health_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "jarvis-agent"


def test_login_returns_csrf_token(client):
    resp = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["expires_days"] > 0
    assert body["csrf_token"]


def test_login_with_wrong_password(client):
    resp = client.post(
        "/api/auth/login",
        json={"password": "mala"},
        headers={"Origin": "http://testserver"},
    )
    assert resp.status_code == 401


def test_login_without_origin_is_forbidden(client):
    """Login CSRF: una pagina ajena no puede iniciar sesion."""
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 403
    assert "Origin" in resp.json()["detail"]


def test_logout_without_origin_is_forbidden(client):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 403


def test_me_without_session(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


def test_me_with_session_exposes_csrf(browser):
    resp = browser.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["method"] == "session"
    assert body["csrf_token"] == browser.csrf_token
    assert body["user"] == "leandro"


def test_logout_invalidates_the_session(client):
    client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    resp = client.get("/api/aprobaciones")
    assert resp.status_code == 401


def test_tools_endpoint_still_works_with_api_token(client):
    resp = client.get("/api/tools", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    assert resp.json()["read_only"] is True


def test_tools_endpoint_requires_auth(client):
    assert client.get("/api/tools").status_code == 401


def test_audit_endpoint_still_works(client):
    resp = client.get("/api/audit", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    assert "audit" in resp.json()


def test_conversations_endpoint_still_works(client):
    resp = client.get("/api/conversations", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_equipos_endpoint_still_works(client, monkeypatch):
    import app.main as main_module

    async def fake_call(name, arguments=None):
        if name == "equipos_list":
            return {"hosts": [{"id": "macbook", "label": "MacBook", "enabled": True}]}
        if name == "equipos_status":
            return {"online": True, "kernel": "Linux 6.8.0"}
        return {"executions": []}

    monkeypatch.setattr(main_module, "call_executor", fake_call)
    resp = client.get("/api/equipos", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    body = resp.json()
    assert body["hosts"][0]["online"] is True


def test_llm_tool_specs_do_not_mention_approvals_endpoints():
    """Las herramientas del LLM no exponen el flujo de aprobación."""
    from app.executor_client import TOOL_SPECS

    names = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert not any("aprob" in name for name in names)
    for spec in TOOL_SPECS:
        assert "/approvals" not in str(spec)


def test_executions_endpoint_no_sondea_hosts(client, monkeypatch):
    """Historial para Aprobaciones: una sola llamada, sin sondas por host."""
    import app.main as main_module

    llamadas: list[str] = []

    async def fake_call(name, arguments=None):
        llamadas.append(name)
        return {
            "executions": [
                {
                    "id": 1,
                    "created_at": "2026-10-21T10:00:00+00:00",
                    "host_id": "macbook",
                    "operation": "host_git_diff_stat",
                    "allowed": 1,
                    "ok": 1,
                    "duration_ms": 42,
                }
            ]
        }

    monkeypatch.setattr(main_module, "call_executor", fake_call)
    resp = client.get(
        "/api/executions?limit=5", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert llamadas == ["equipos_historial"]
