"""Pruebas de la iteración «chat mejor» (0.11.0).

Cubre las tres piezas:
- detener un turno conserva el texto parcial y lo marca como interrumpido,
- la lista de conversaciones trae vista previa y número de mensajes,
- el aviso `tool_start` se emite ANTES de ejecutar la herramienta (la PWA
  puede mostrar «usando X…» mientras trabaja, no después).
"""
from __future__ import annotations

import asyncio

import pytest

from app import graph, main, memory


def _base(conv_id: str) -> list[dict]:
    memory.add_message(conv_id, "user", "hola")
    return [{"role": "system", "content": "eres jarvis"}] + memory.get_messages(
        conv_id, None
    )


@pytest.mark.asyncio
async def test_detener_conserva_el_texto_parcial(client, monkeypatch) -> None:
    conv_id = memory.create_conversation("detener")
    base = _base(conv_id)

    class FakeAgent:
        async def astream(self, state: dict, stream_mode: str = "values"):
            emit = main.STREAM_EMIT.get()
            assert emit is not None
            emit({"event": "segment"})
            emit({"event": "token", "text": "Estoy mirando"})
            await asyncio.sleep(30)  # el «modelo» se queda colgado
            yield {}  # pragma: no cover - nunca se alcanza

    monkeypatch.setattr(main, "AGENT", FakeAgent())

    stream = main._turn_stream(conv_id, base, None)
    assert "event: meta" in await anext(stream)
    assert "event: segment" in await anext(stream)
    assert "Estoy mirando" in await anext(stream)

    # El cliente pulsa detener: el endpoint marca el evento del turno.
    respuesta = await main.chat_stop(main.StopRequest(conversation_id=conv_id))
    assert respuesta == {"ok": True, "stopped": True}

    cierre = await asyncio.wait_for(anext(stream), timeout=3)
    assert "event: done" in cierre
    assert '"interrupted": true' in cierre
    assert "Estoy mirando" in cierre

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    # Persistido como mensaje normal, pero marcado: al recargar no parece
    # una respuesta completa.
    guardado = memory.get_messages(conv_id, None)[-1]
    assert guardado["role"] == "assistant"
    assert guardado["content"] == "Estoy mirando"
    timeline = memory.get_timeline(conv_id)
    assert timeline[-1]["interrupted"] is True

    # El turno ya no está activo: otro detener es inofensivo.
    assert await main.chat_stop(
        main.StopRequest(conversation_id=conv_id)
    ) == {"ok": True, "stopped": False}


@pytest.mark.asyncio
async def test_detener_antes_del_primer_token_deja_nota(client, monkeypatch) -> None:
    conv_id = memory.create_conversation("detener pronto")
    base = _base(conv_id)

    class FakeAgent:
        async def astream(self, state: dict, stream_mode: str = "values"):
            await asyncio.sleep(30)
            yield {}  # pragma: no cover

    monkeypatch.setattr(main, "AGENT", FakeAgent())

    stream = main._turn_stream(conv_id, base, None)
    assert "event: meta" in await anext(stream)
    await main.chat_stop(main.StopRequest(conversation_id=conv_id))
    cierre = await asyncio.wait_for(anext(stream), timeout=3)
    assert "Detenido antes de recibir respuesta" in cierre

    assert memory.get_messages(conv_id, None)[-1]["content"].startswith(
        "(Detenido antes"
    )


def test_lista_de_conversaciones_trae_vista_previa_y_recuento(client) -> None:
    conv = memory.create_conversation("primera")
    memory.add_message(conv, "user", "¿cómo está el sistema?\nsegunda línea")
    memory.add_message(conv, "assistant", "Todo bien.")

    item = memory.list_conversations(10)[0]
    assert item["id"] == conv
    assert item["message_count"] == 2
    # Una sola línea: la lista no se rompe con saltos del mensaje original.
    assert item["preview"] == "¿cómo está el sistema? segunda línea"

    resp = client.get("/api/conversations", headers={"X-API-Token": "token-api-secreto"})
    assert resp.status_code == 200
    fila = resp.json()["conversations"][0]
    assert fila["message_count"] == 2
    assert fila["preview"].startswith("¿cómo está")


@pytest.mark.asyncio
async def test_tool_start_se_emite_antes_de_ejecutar(client, monkeypatch) -> None:
    eventos: list[dict] = []

    async def fake_call(name: str, args: dict) -> dict:
        # En el momento de ejecutar, el aviso de inicio YA salió.
        assert any(e.get("event") == "tool_start" for e in eventos)
        return {"ok": True}

    monkeypatch.setattr(graph, "call_tool", fake_call)
    monkeypatch.setattr(graph, "add_audit", lambda *a, **k: None)

    token = main.STREAM_EMIT.set(eventos.append)
    try:
        estado = {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "system_stats",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            ],
            "tool_trace": [],
            "conversation_id": "c1",
        }
        await graph._run_tools(estado)
    finally:
        main.STREAM_EMIT.reset(token)

    assert [e["event"] for e in eventos] == ["tool_start", "tool"]
    assert eventos[0]["tool"] == "system_stats"
