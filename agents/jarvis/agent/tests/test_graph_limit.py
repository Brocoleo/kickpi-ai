"""Regresión del cierre por límite de iteraciones (graph.py).

Caso real: el modelo agotaba `max_tool_iterations` pidiendo herramientas y el
grafo terminaba con la última llamada SIN resolver y sin texto: la PWA
mostraba una burbuja vacía y el historial quedaba con un `tool_call` colgando.

Estas pruebas no llaman al LLM: ejercitan las funciones puras del grafo.
"""
from __future__ import annotations

import asyncio

from app import graph as graph_module


def _state(iterations: int, tool_calls: list[dict] | None = None) -> dict:
    last = {"role": "assistant", "content": None}
    if tool_calls:
        last["tool_calls"] = tool_calls
    return {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hola"},
            last,
        ],
        "iterations": iterations,
        "tool_trace": [],
    }


def test_limit_with_pending_calls_routes_to_limit(monkeypatch):
    monkeypatch.setattr(
        graph_module.get_settings(), "max_tool_iterations", 2, raising=False
    )
    state = _state(2, [{"id": "call-1", "function": {"name": "host_uname"}}])
    assert graph_module._should_continue(state) == "limit"


def test_limit_without_pending_calls_ends(monkeypatch):
    monkeypatch.setattr(
        graph_module.get_settings(), "max_tool_iterations", 2, raising=False
    )
    assert graph_module._should_continue(_state(2)) == "end"


def test_below_limit_still_runs_tools(monkeypatch):
    monkeypatch.setattr(
        graph_module.get_settings(), "max_tool_iterations", 2, raising=False
    )
    state = _state(1, [{"id": "call-1", "function": {"name": "host_uname"}}])
    assert graph_module._should_continue(state) == "tools"


def test_close_on_limit_resolves_calls_and_adds_text(monkeypatch):
    """Cada tool_call pendiente se responde y el último mensaje TIENE texto."""
    monkeypatch.setattr(
        graph_module.get_settings(), "max_tool_iterations", 3, raising=False
    )
    calls = [
        {"id": "call-1", "function": {"name": "host_uname"}},
        {"id": "call-2", "function": {"name": "host_uptime"}},
    ]
    result = asyncio.run(graph_module._close_on_limit(_state(3, calls)))
    new = result["messages"][3:]

    tool_msgs = [m for m in new if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"call-1", "call-2"}
    for msg in tool_msgs:
        assert "cancelado" in msg["content"]

    last = new[-1]
    assert last["role"] == "assistant"
    assert last["content"].strip()
    assert "3" in last["content"]
