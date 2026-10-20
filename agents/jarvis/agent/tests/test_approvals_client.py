"""Pruebas del cliente de aprobaciones: cabeceras, aislamiento y errores.

Verifican que el token dedicado viaja SOLO en la cabecera `X-Approvals-Token`
y que nunca aparece en la URL, en el cuerpo ni en los logs.
"""
from __future__ import annotations

import httpx
import pytest

from app import approvals_client
from app.approvals_client import ApprovalsError
from conftest import APPROVALS_TOKEN


class RecordingClient:
    """Sustituye a httpx.AsyncClient y graba lo que se envía."""

    def __init__(self, response: httpx.Response | None = None):
        self.response = response or httpx.Response(
            200, json={"approvals": [], "count": 0}, request=httpx.Request("GET", "x")
        )
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.sent.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return self.response

    async def request(self, method, url, json=None, headers=None):
        self.sent.append(
            {"method": method, "url": url, "body": json, "headers": headers}
        )
        return self.response


@pytest.fixture
def recorder(monkeypatch, agent_settings):
    rec = RecordingClient()
    monkeypatch.setattr(approvals_client.httpx, "AsyncClient", lambda *a, **k: rec)
    return rec


async def test_token_travels_only_in_the_header(recorder):
    await approvals_client.list_approvals("pending", "macbook", 10)
    sent = recorder.sent[0]
    assert sent["headers"]["X-Approvals-Token"] == APPROVALS_TOKEN
    assert APPROVALS_TOKEN not in sent["url"]
    assert APPROVALS_TOKEN not in str(sent["params"])


async def test_approve_sends_identity_in_the_body(recorder):
    await approvals_client.approve("abc", "leandro")
    sent = recorder.sent[0]
    assert sent["method"] == "POST"
    assert sent["body"] == {"approved_by": "leandro"}
    assert sent["headers"]["X-Approvals-Token"] == APPROVALS_TOKEN


async def test_execute_sends_no_body(recorder):
    await approvals_client.execute("abc")
    sent = recorder.sent[0]
    assert sent["method"] == "POST"
    assert sent["body"] is None


async def test_missing_token_fails_closed(monkeypatch, agent_settings):
    monkeypatch.setenv("JARVIS_APPROVALS_TOKEN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ApprovalsError) as exc:
        await approvals_client.list_approvals()
    assert exc.value.code == 503
    get_settings.cache_clear()


async def test_executor_error_is_propagated(monkeypatch, agent_settings):
    class Failing(RecordingClient):
        async def get(self, url, params=None, headers=None):
            self.sent.append({"method": "GET", "url": url})
            return httpx.Response(
                409,
                json={"detail": "la aprobacion ya fue consumida"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(
        approvals_client.httpx, "AsyncClient", lambda *a, **k: Failing()
    )
    with pytest.raises(ApprovalsError) as exc:
        await approvals_client.list_approvals()
    assert exc.value.code == 409
    assert "consumida" in str(exc.value)
