"""Endpoints NAS de la PWA: autenticados y con el token de tools en el medio.

La PWA nunca habla con jarvis-tools: el agente reenvía y añade el token. Estas
pruebas fijan las dos cosas que podrían romperse en silencio: que sin sesión
no se lea nada y que la ruta/parámetros viajen tal cual.
"""
from __future__ import annotations

import httpx

from app import main
from conftest import API_TOKEN


def _install_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", factory)


def test_nas_exige_autenticacion(client) -> None:
    for ruta in (
        "/api/nas/list",
        "/api/nas/recent",
        "/api/nas/search?q=factura",
        "/api/nas/status",
    ):
        assert client.get(ruta).status_code == 401, ruta


def test_nas_list_reenvia_ruta_y_parametros(client, monkeypatch) -> None:
    visto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["url"] = str(request.url)
        visto["token"] = request.headers.get("x-tools-token")
        visto["tiene_header"] = "x-tools-token" in request.headers
        return httpx.Response(
            200,
            json={
                "ready": True,
                "path": "Entrada",
                "parent": "",
                "dirs": [],
                "files": [{"name": "foto.jpg", "path": "Entrada/foto.jpg"}],
                "error": None,
            },
        )

    _install_transport(monkeypatch, handler)
    resp = client.get(
        "/api/nas/list?path=Entrada&limit=10",
        headers={"X-API-Token": API_TOKEN},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["files"][0]["path"] == "Entrada/foto.jpg"
    assert visto["url"].startswith("http://jarvis-tools:8000/nas/list?")
    assert "path=Entrada" in visto["url"]
    assert "limit=10" in visto["url"]
    assert visto["tiene_header"]


def test_nas_search_usa_q_y_content(client, monkeypatch) -> None:
    visto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "ready": True,
                "query": "router",
                "results": [],
                "total_matches": 0,
                "error": None,
            },
        )

    _install_transport(monkeypatch, handler)
    resp = client.get(
        "/api/nas/search?q=router&content=false&limit=5",
        headers={"X-API-Token": API_TOKEN},
    )

    assert resp.status_code == 200, resp.text
    assert "q=router" in visto["url"]
    assert "content=false" in visto["url"]
    assert "limit=5" in visto["url"]


def test_nas_propaga_404_de_tools(client, monkeypatch) -> None:
    _install_transport(
        monkeypatch, lambda request: httpx.Response(404, json={"detail": "nope"})
    )
    resp = client.get(
        "/api/nas/list?path=no-existe", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code == 404
