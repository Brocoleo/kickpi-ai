"""Las herramientas NAS existen, están declaradas y viajan al modelo.

El riesgo real de esta pieza es silencioso: si `TOOL_SPECS` y `_ENDPOINTS` se
desincronizan, el modelo ve una herramienta que el cliente no sabe llamar (o
al contrario) y el turno falla en producción, no en el test.
"""
from __future__ import annotations

import asyncio

import pytest

from app import tools_client

NAS = {"nas_search", "nas_recent"}


def test_toda_herramienta_declarada_tiene_endpoint() -> None:
    declaradas = {spec["function"]["name"] for spec in tools_client.TOOL_SPECS}
    assert NAS <= declaradas
    assert declaradas == set(tools_client._ENDPOINTS)


def test_nas_search_es_obligatoria_y_con_tope() -> None:
    spec = next(
        s for s in tools_client.TOOL_SPECS if s["function"]["name"] == "nas_search"
    )
    parametros = spec["function"]["parameters"]
    assert parametros["required"] == ["query"]
    assert "content" in parametros["properties"]
    assert "limit" in parametros["properties"]


@pytest.mark.asyncio
async def test_call_tool_traduce_los_parametros(monkeypatch) -> None:
    capturado: dict = {}

    class Respuesta:
        status_code = 200

        def json(self) -> dict:
            return {"ok": True, "results": []}

    class Cliente:
        def __init__(self, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def get(self, url: str, params=None, headers=None) -> Respuesta:
            capturado["url"] = url
            capturado["params"] = params
            capturado["headers"] = headers
            return Respuesta()

    monkeypatch.setattr(tools_client.httpx, "AsyncClient", Cliente)
    monkeypatch.setattr(
        tools_client,
        "get_settings",
        lambda: type(
            "S",
            (),
            {"tools_url": "http://tools", "tools_token": "t", "tools_timeout": 5},
        )(),
    )

    await tools_client.call_tool(
        "nas_search", {"query": "factura orange", "content": False, "limit": 5}
    )
    assert capturado["url"] == "http://tools/nas/search"
    assert capturado["params"] == {
        "q": "factura orange",
        "content": False,
        "limit": 5,
    }

    await tools_client.call_tool("nas_recent", {"limit": 3, "path": "Descargas"})
    assert capturado["url"] == "http://tools/nas/recent"
    assert capturado["params"] == {"limit": 3, "path": "Descargas"}


@pytest.mark.asyncio
async def test_call_tool_rechaza_herramienta_desconocida() -> None:
    with pytest.raises(tools_client.ToolsError):
        await tools_client.call_tool("borrar_todo", {})
