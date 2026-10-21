"""Pruebas del cierre de turno tras una aprobación humana.

Cubre:
- extracción del id de aprobación de un tool-message pendiente,
- vínculo aprobación ↔ conversación,
- reanudación del grafo con el resultado ya ejecutado,
- que el turno viejo no quede a medias (se recorta y se reemplaza el tool).
"""
from __future__ import annotations

import json

import pytest

from app import main, memory


def _base_turn(conv_id: str) -> list[dict]:
    memory.add_message(conv_id, "user", "muéstrame los cambios del repo")
    history = memory.get_messages(conv_id, None)
    return [{"role": "system", "content": "eres jarvis"}] + history


def _pending_turn(base: list[dict]) -> dict:
    return {
        "messages": base
        + [
            {
                "role": "assistant",
                "content": "Voy a pedir el diff.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "host_git_diff_stat",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "host_git_diff_stat",
                "content": json.dumps(
                    {
                        "ok": False,
                        "state": "pending_approval",
                        "approval": {"id": "AP1"},
                    }
                ),
            },
            {
                "role": "assistant",
                "content": "Pendiente de tu aprobación.",
            },
        ],
        "tool_trace": [],
        "iterations": 3,
        "conversation_id": "",
    }


def test_pending_approval_id_solo_para_estados_pendientes() -> None:
    assert (
        main._pending_approval_id(
            json.dumps({"state": "pending_approval", "approval": {"id": "A1"}})
        )
        == "A1"
    )
    assert main._pending_approval_id(json.dumps({"state": "executed"})) is None
    assert main._pending_approval_id("no es json") is None
    assert main._pending_approval_id(None) is None


def test_persistencia_enlaza_la_aprobacion(client) -> None:
    conv_id = memory.create_conversation("prueba")
    base = _base_turn(conv_id)
    result = _pending_turn(base)
    result["conversation_id"] = conv_id

    reply = main._persist_turn_sync(conv_id, len(base), result)

    assert reply == "Pendiente de tu aprobación."
    link = memory.get_approval_context("AP1")
    assert link is not None
    assert link["conversation_id"] == conv_id
    assert link["tool_call_id"] == "call_1"

    stored = memory.get_messages(conv_id, None)
    assert [m["role"] for m in stored] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_reanudar_reemplaza_el_tool_y_cierra_el_turno(
    client, monkeypatch
) -> None:
    conv_id = memory.create_conversation("prueba")
    base = _base_turn(conv_id)
    result = _pending_turn(base)
    result["conversation_id"] = conv_id
    main._persist_turn_sync(conv_id, len(base), result)

    seen: dict = {}

    class FakeAgent:
        async def ainvoke(self, state: dict) -> dict:
            seen["last"] = state["messages"][-1]
            return {
                "messages": state["messages"]
                + [
                    {
                        "role": "assistant",
                        "content": "El diff tiene dos archivos modificados.",
                    }
                ],
                "tool_trace": [],
                "iterations": 1,
            }

    monkeypatch.setattr(main, "AGENT", FakeAgent())

    await main._resume_after_decision(
        "AP1",
        {
            "ok": True,
            "state": "executed",
            "exit_code": 0,
            "stdout": " App.tsx | 4 +-",
        },
    )

    # El agente recibio el resultado real, no el "pending_approval".
    assert seen["last"]["role"] == "tool"
    assert "App.tsx" in seen["last"]["content"]

    stored = memory.get_messages(conv_id, None)
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == "El diff tiene dos archivos modificados."
    # El historial conserva el contexto previo, pero el agente no lo recibió
    # como último mensaje (se recortó en el estado que se le pasó).
    assert any("Pendiente de tu aprobación" in (m.get("content") or "") for m in stored)
    assert sum(1 for m in stored if m["role"] == "tool") == 1


@pytest.mark.asyncio
async def test_sin_vinculo_no_hace_nada(client, monkeypatch) -> None:
    called = {"value": False}

    class FakeAgent:
        async def ainvoke(self, state: dict) -> dict:  # pragma: no cover
            called["value"] = True
            return state

    monkeypatch.setattr(main, "AGENT", FakeAgent())
    await main._resume_after_decision("NO-EXISTE", {"ok": True})
    assert called["value"] is False


def test_stream_sse_persiste_y_emite_eventos(browser, monkeypatch) -> None:
    """El endpoint SSE emite segment/token/done y persiste una sola vez."""

    class FakeAgent:
        async def astream(self, state: dict, stream_mode: str = "values"):
            emit = main.STREAM_EMIT.get()
            assert emit is not None
            emit({"event": "segment"})
            emit({"event": "token", "text": "Hola "})
            emit({"event": "token", "text": "mundo"})
            emit(
                {
                    "event": "tool",
                    "tool": "system_stats",
                    "ok": True,
                    "duration_ms": 5,
                    "summary": "ok",
                }
            )
            yield {
                "messages": state["messages"]
                + [{"role": "assistant", "content": "Hola mundo"}],
                "tool_trace": [],
                "iterations": 1,
                "conversation_id": state["conversation_id"],
                "model_used": "modelo-falso",
            }

    monkeypatch.setattr(main, "AGENT", FakeAgent())

    with browser.stream(
        "POST", "/api/chat/stream", json={"message": "hola"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    assert "event: segment" in body
    assert "event: token" in body
    assert "event: tool" in body
    assert "event: done" in body
    assert "Hola mundo" in body

    # El id viaja en el evento done y la conversacion quedo persistida.
    import re

    match = re.search(r'"conversation_id": "([a-f0-9]+)"', body)
    assert match, body
    conv_id = match.group(1)
    stored = memory.get_messages(conv_id, None)
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == "Hola mundo"
    assert sum(1 for m in stored if m["role"] == "assistant") == 1
