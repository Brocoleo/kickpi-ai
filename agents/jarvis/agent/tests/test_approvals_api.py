"""Pruebas del proxy de aprobaciones del agente (Fase C, lado agente).

Objetivo: comprobar que las rutas de aprobación
- exigen SESIÓN HUMANA (el token de API no vale),
- exigen Origin estricto y token CSRF ligado a la sesión en las rutas que
  cambian estado,
- derivan la identidad del navegador de la SESIÓN (nunca del cuerpo),
- no permiten ejecutar una aprobación con otros parámetros,
- y no dejan pasar el token de aprobaciones en ninguna respuesta.
"""
from __future__ import annotations

import asyncio

from conftest import API_TOKEN, APPROVALS_TOKEN as CONFTEST_APPROVALS_TOKEN


# --------------------------------------------------------------------------- #
# Autenticación
# --------------------------------------------------------------------------- #


def test_list_requires_a_session(client, fake_executor):
    assert client.get("/api/aprobaciones").status_code == 401


def test_api_token_is_not_enough_for_approvals(client, fake_executor):
    """El token de API es para scripts: NO puede decidir aprobaciones."""
    resp = client.get("/api/aprobaciones", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 403
    resp = client.post(
        "/api/aprobaciones/abc123/aprobar", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code == 403


def test_list_with_session(browser, fake_executor):
    resp = browser.get("/api/aprobaciones")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["approvals"][0]["id"] == "abc123"


def test_detail_with_session(browser, fake_executor):
    resp = browser.get("/api/aprobaciones/abc123")
    assert resp.status_code == 200
    assert resp.json()["approval"]["operation"] == "host_git_diff_stat"


# --------------------------------------------------------------------------- #
# CSRF y Origin
# --------------------------------------------------------------------------- #


def test_mutation_without_csrf_is_forbidden(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar", headers={"X-CSRF-Token": ""}
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_mutation_with_wrong_csrf_is_forbidden(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar", headers={"X-CSRF-Token": "0" * 64}
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_mutation_without_origin_is_forbidden(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar", headers={"Origin": ""}
    )
    assert resp.status_code == 403
    assert "Origin" in resp.json()["detail"]


def test_mutation_from_foreign_origin_is_forbidden(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar",
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403
    assert "no autorizado" in resp.json()["detail"]


def test_csrf_from_another_session_is_forbidden(client, fake_executor):
    """El token CSRF de una sesión no vale en otra (está ligado a ella)."""
    client.post(
        "/api/auth/login",
        json={"password": "clave-de-prueba"},
        headers={"Origin": "http://testserver"},
    )
    other = client.get("/api/auth/me").json()["csrf_token"]

    # Se cierra sesión y se abre otra: la huella cambia.
    client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    client.post(
        "/api/auth/login",
        json={"password": "clave-de-prueba"},
        headers={"Origin": "http://testserver"},
    )
    resp = client.post(
        "/api/aprobaciones/abc123/aprobar",
        headers={"Origin": "http://testserver", "X-CSRF-Token": other},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# Identidad derivada de la sesión
# --------------------------------------------------------------------------- #


def test_approved_by_comes_from_the_session(browser, fake_executor):
    resp = browser.post("/api/aprobaciones/abc123/aprobar")
    assert resp.status_code == 200
    assert resp.json()["approval"]["approved_by"] == "leandro"
    # Y se lo pasó al ejecutor tal cual.
    approve_calls = [c for c in fake_executor.calls if c[1].endswith("/approve")]
    assert len(approve_calls) == 1
    assert approve_calls[0][2] == {"approved_by": "leandro"}


def test_browser_cannot_send_approved_by(browser, fake_executor):
    """El navegador no puede decidir QUIEN aprueba."""
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar",
        json={"approved_by": "attacker"},
    )
    assert resp.status_code == 422
    assert not [c for c in fake_executor.calls if c[1].endswith("/approve")]


def test_reject_derives_identity_from_the_session(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/rechazar", json={"reason": "ahora no"}
    )
    assert resp.status_code == 200
    reject_calls = [c for c in fake_executor.calls if c[1].endswith("/reject")]
    assert reject_calls[0][2]["approved_by"] == "leandro"
    assert reject_calls[0][2]["reason"] == "ahora no"


def test_reject_rejects_unknown_fields(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/rechazar", json={"approved_by": "attacker"}
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #


def test_execute_requires_approved_state(browser, fake_executor):
    """Ejecutar sin aprobar no ejecuta nada."""
    resp = browser.post("/api/aprobaciones/abc123/ejecutar")
    assert resp.status_code == 409
    assert "pending" in resp.json()["detail"]


def test_execute_after_approve_runs_once(browser, fake_executor):
    browser.post("/api/aprobaciones/abc123/aprobar")
    resp = browser.post("/api/aprobaciones/abc123/ejecutar")
    assert resp.status_code == 200
    assert resp.json()["state"] == "consumed"

    # Segundo intento: el ejecutor lo rechaza (un solo uso).
    resp2 = browser.post("/api/aprobaciones/abc123/ejecutar")
    assert resp2.status_code == 409
    execute_calls = [c for c in fake_executor.calls if c[1].endswith("/execute")]
    assert len(execute_calls) == 2  # ambas intentos llegaron; el 2o fue rechazado


def test_execute_cannot_carry_parameters(browser, fake_executor):
    """Ejecutar con parámetros distintos se rechaza en la frontera."""
    resp = browser.post(
        "/api/aprobaciones/abc123/ejecutar", json={"params": {"path": "/etc"}}
    )
    assert resp.status_code == 422
    assert not [c for c in fake_executor.calls if c[1].endswith("/execute")]


def test_execute_is_forbidden_without_csrf(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/ejecutar", headers={"X-CSRF-Token": "x"}
    )
    assert resp.status_code == 403


def test_concurrent_execute_executes_only_once(browser, fake_executor):
    """Dos pulsaciones simultaneas: una ejecuta, la otra recibe 409."""
    browser.post("/api/aprobaciones/abc123/aprobar")

    def hit():
        return browser.post("/api/aprobaciones/abc123/ejecutar")

    results = asyncio.run(_gather(hit, hit))

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]
    assert len(fake_executor.consumed) == 1


async def _gather(*functions):
    """Ejecuta funciones síncronas concurrentes en hilos aparte."""
    import functools

    loop = asyncio.get_running_loop()
    return await asyncio.gather(
        *[loop.run_in_executor(None, functools.partial(f)) for f in functions]
    )


# --------------------------------------------------------------------------- #
# Aislamiento del token
# --------------------------------------------------------------------------- #


def test_approvals_token_never_appears_in_responses(browser, fake_executor):
    token = CONFTEST_APPROVALS_TOKEN
    responses = [
        browser.get("/api/aprobaciones"),
        browser.get("/api/aprobaciones/abc123"),
        browser.post("/api/aprobaciones/abc123/aprobar"),
        browser.get("/api/auth/me"),
    ]
    for resp in responses:
        assert token not in resp.text
        assert token not in str(resp.json())


def test_approvals_token_is_not_reachable_from_the_llm_tools():
    """El LLM no ve el token de aprobaciones en sus especificaciones."""
    from app.config import SYSTEM_PROMPT
    from app.executor_client import TOOL_SPECS

    for spec in TOOL_SPECS:
        assert CONFTEST_APPROVALS_TOKEN not in str(spec)
    assert CONFTEST_APPROVALS_TOKEN not in SYSTEM_PROMPT

    # El cliente del LLM (executor_client) no menciona el token de
    # aprobaciones: son credenciales distintas para superficies distintas.
    import app.executor_client as executor_module

    source = open(executor_module.__file__, encoding="utf-8").read()
    assert "approvals_token" not in source
    assert "X-Approvals-Token" not in source
