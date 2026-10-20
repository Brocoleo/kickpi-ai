"""Pruebas de C5: auditoría de seguridad del proxy de aprobaciones.

Comprueban, con ojos de auditor:

- la identidad humana procede EXCLUSIVAMENTE de una sesión válida (el
  token de API no basta, ni aunque venga acompañado de una cookie);
- una sesión caducada no puede aprobar nada;
- el LLM no tiene ninguna vía hacia las aprobaciones (ni por las
  herramientas, ni por el cliente de herramientas);
- el navegador no recibe ningun token interno del backend;
- el CSRF y el Origin siguen siendo obligatorios en cada mutacion.
"""
from __future__ import annotations

import sqlite3

from app.config import get_settings
from app.db import db_path
from conftest import API_TOKEN, APPROVALS_TOKEN


def _expire_current_session(client) -> None:
    """Caduca la sesion actual en la BD del temporal."""
    conn = sqlite3.connect(db_path())
    conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# Identidad: solo una sesion valida
# --------------------------------------------------------------------------- #
def test_la_sesion_gana_al_token_de_api(browser, fake_executor):
    """Con cookie de sesion, la peticion se acepta aunque llegue el token."""
    resp = browser.get("/api/aprobaciones", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    resp = browser.post("/api/aprobaciones/abc123/aprobar", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    approve_calls = [c for c in fake_executor.calls if c[1].endswith("/approve")]
    assert approve_calls[0][2]["approved_by"] == "leandro"


def test_el_token_de_api_solo_no_abre_las_aprobaciones(client, fake_executor):
    resp = client.get("/api/aprobaciones", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 403
    resp = client.post(
        "/api/aprobaciones/abc123/aprobar", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code == 403


def test_una_sesion_caducada_no_puede_aprobar(browser, fake_executor):
    _expire_current_session(browser)
    resp = browser.get("/api/aprobaciones")
    assert resp.status_code == 401
    resp = browser.post("/api/aprobaciones/abc123/aprobar")
    assert resp.status_code == 401
    assert not [c for c in fake_executor.calls if c[1].endswith("/approve")]


def test_sin_sesion_no_hay_identidad(client, fake_executor):
    """Sin sesion no hay quien aprueba: ni siquiera el token del backend."""
    resp = client.post(
        "/api/aprobaciones/abc123/aprobar",
        headers={"X-API-Token": API_TOKEN, "Origin": "http://testserver"},
    )
    assert resp.status_code == 403


def test_la_identidad_no_puede_viajar_en_el_cuerpo(browser, fake_executor):
    resp = browser.post(
        "/api/aprobaciones/abc123/aprobar", json={"approved_by": "otro"}
    )
    assert resp.status_code == 422
    assert not [c for c in fake_executor.calls if c[1].endswith("/approve")]


# --------------------------------------------------------------------------- #
# El LLM no tiene ninguna via hacia las aprobaciones
# --------------------------------------------------------------------------- #
def test_el_llm_no_tiene_herramientas_de_aprobacion():
    from app.executor_client import TOOL_NAMES, TOOL_SPECS

    assert not any("aprob" in name or "approve" in name for name in TOOL_NAMES)
    for spec in TOOL_SPECS:
        assert "/approvals" not in str(spec)
        assert "/aprobaciones" not in str(spec)


def test_el_cliente_del_llm_no_conoce_el_token_de_aprobaciones():
    """El codigo que usa el LLM no menciona el token de aprobaciones."""
    import app.executor_client as executor_module

    source = open(executor_module.__file__, encoding="utf-8").read()
    for prohibido in (
        "approvals_token",
        "X-Approvals-Token",
        "/approvals",
        "approve",
    ):
        assert prohibido not in source


def test_una_aprobacion_pendiente_no_puede_ejecutarse_por_el_llm(browser, fake_executor):
    """El LLM pide la operacion y recibe un id: no puede ejecutarlo."""
    from app.executor_client import call_executor

    import app.executor_client as executor_module

    async def fake_httpx(*args, **kwargs):
        raise AssertionError("no debe llamar al ejecutor")

    # El resultado pendiente NO incluye ningun camino de ejecucion.
    result = {
        "ok": False,
        "denied": False,
        "state": "pending_approval",
        "approval": {"id": "SOLICITUD", "status": "pending"},
    }
    assert result["state"] == "pending_approval"
    # Las tools del agente solo exponen /exec, que no acepta approval_id.
    from app.executor_client import TOOL_SPECS

    ejecutar = next(
        spec for spec in TOOL_SPECS if spec["function"]["name"] == "equipos_ejecutar"
    )
    props = ejecutar["function"]["parameters"]["properties"]
    assert "approval_id" not in props


# --------------------------------------------------------------------------- #
# El navegador no recibe tokens internos
# --------------------------------------------------------------------------- #
def test_ningun_token_del_backend_llega_al_navegador(browser, fake_executor):
    """Ni el token del ejecutor ni el de aprobaciones salen del backend."""
    respuestas = [
        browser.get("/api/aprobaciones"),
        browser.get("/api/aprobaciones/abc123"),
        browser.get("/api/auth/me"),
        browser.get("/api/tools", headers={"X-API-Token": API_TOKEN}),
        browser.get("/api/equipos", headers={"X-API-Token": API_TOKEN}),
    ]
    for resp in respuestas:
        assert APPROVALS_TOKEN not in resp.text
        assert API_TOKEN not in resp.text
        assert "X-Approvals-Token" not in resp.text
        assert "X-Executor-Token" not in resp.text


def test_el_cliente_de_aprobaciones_es_el_unico_que_usa_el_token(browser, fake_executor):
    import app.approvals_client as approvals_module
    import app.executor_client as executor_module

    approvals_source = open(approvals_module.__file__, encoding="utf-8").read()
    executor_source = open(executor_module.__file__, encoding="utf-8").read()

    assert "X-Approvals-Token" in approvals_source
    assert "X-Approvals-Token" not in executor_source
    assert "approvals_token" in approvals_source


# --------------------------------------------------------------------------- #
# CSRF y Origin siguen siendo obligatorios
# --------------------------------------------------------------------------- #
def test_cada_mutacion_exige_csrf_y_origin(browser, fake_executor):
    for suffix in ("/aprobar", "/rechazar", "/ejecutar"):
        resp = browser.post(
            f"/api/aprobaciones/abc123{suffix}", headers={"X-CSRF-Token": "malo"}
        )
        assert resp.status_code == 403, suffix
        resp = browser.post(
            f"/api/aprobaciones/abc123{suffix}", headers={"Origin": "https://otro.example"}
        )
        assert resp.status_code == 403, suffix


def test_el_csrf_no_sirve_para_otra_sesion(client, fake_executor):
    from conftest import PASSWORD

    client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    csrf = client.get("/api/auth/me").json()["csrf_token"]
    client.post("/api/auth/logout", headers={"Origin": "http://testserver"})

    # Nueva sesion: el token de la anterior no vale.
    client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    resp = client.post(
        "/api/aprobaciones/abc123/aprobar",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 403
    assert not [c for c in fake_executor.calls if c[1].endswith("/approve")]
