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
                "path": "files/Entrada",
                "parent": "",
                "dirs": [],
                "files": [
                    {"name": "foto.jpg", "path": "files/Entrada/Fotos/foto.jpg"},
                    {"name": "informe.pdf", "path": "files/Documentos/informe.pdf"},
                ],
                "error": None,
            },
        )

    _install_transport(monkeypatch, handler)
    resp = client.get(
        "/api/nas/list?path=files/Entrada&limit=10",
        headers={"X-API-Token": API_TOKEN},
    )

    assert resp.status_code == 200, resp.text
    archivos = resp.json()["files"]
    assert archivos[0]["path"] == "files/Entrada/Fotos/foto.jpg"
    assert archivos[0]["deletable"] is True
    assert archivos[1]["deletable"] is False
    assert visto["url"].startswith("http://jarvis-tools:8000/nas/list?")
    assert "path=files%2FEntrada" in visto["url"]
    assert "limit=10" in visto["url"]
    assert visto["tiene_header"]


def test_nas_status_declara_el_inbox(client, monkeypatch) -> None:
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"ready": True, "root": "/mnt/data/nas", "files": 3}
        ),
    )
    resp = client.get("/api/nas/status", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json()["inbox"] == "files/Entrada"


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


# --------------------------------------------------------------------------- #
# Descarga ZIP de una selección (ticket de un solo uso)
# --------------------------------------------------------------------------- #
def test_nas_zip_exige_autenticacion(client) -> None:
    assert client.post("/api/nas/zip", json={"paths": ["files/a.jpg"]}).status_code == 401
    assert client.get("/api/nas/zip/cualquier-cosa").status_code == 401


def test_nas_zip_prepara_y_sirve_una_vez(browser, monkeypatch) -> None:
    visto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["paths"] = request.url.params.get_list("path")
        visto["ruta"] = request.url.path
        # El ZIP se sirve en streaming: el mock debe devolver un stream (con
        # `content=` httpx lo marca como ya consumido al usar `stream=True`).
        return httpx.Response(
            200,
            headers={
                "content-type": "application/zip",
                "content-disposition": 'attachment; filename="jarvis.zip"',
            },
            stream=httpx.ByteStream(b"PK\x03\x04datos"),
        )

    _install_transport(monkeypatch, handler)
    rutas = [
        "files/Entrada/Fotos/a.jpg",
        "files/Entrada/Fotos/b.jpg",
    ]

    alta = browser.post("/api/nas/zip", json={"paths": rutas})
    assert alta.status_code == 200, alta.text
    ticket = alta.json()
    assert ticket["count"] == 2
    assert ticket["expires_in"] > 0

    descarga = browser.get(f"/api/nas/zip/{ticket['id']}")
    assert descarga.status_code == 200, descarga.text
    assert descarga.content == b"PK\x03\x04datos"
    assert "attachment" in descarga.headers["content-disposition"]
    assert visto["ruta"] == "/nas/zip"
    assert visto["paths"] == rutas
    assert descarga.headers["cache-control"] == "no-store"

    # Un ticket se consume: el enlace no se puede compartir ni repetir.
    assert browser.get(f"/api/nas/zip/{ticket['id']}").status_code == 404


def test_nas_zip_rechaza_seleccion_vacia(browser) -> None:
    assert browser.post("/api/nas/zip", json={"paths": []}).status_code == 422
    vacia = browser.post("/api/nas/zip", json={"paths": ["  ", ""]})
    assert vacia.status_code == 400
    assert "al menos un archivo" in vacia.json()["detail"]


def test_nas_zip_ticket_caducado(browser) -> None:
    ticket = browser.post(
        "/api/nas/zip", json={"paths": ["files/a.jpg"]}
    ).json()["id"]
    # Forzar la caducidad sin esperar dos minutos.
    main._zip_tickets[ticket] = (0.0, ("files/a.jpg",))

    resp = browser.get(f"/api/nas/zip/{ticket}")
    assert resp.status_code == 404
    assert "caducó" in resp.json()["detail"]


def test_nas_zip_propaga_el_rechazo_de_tools(browser, monkeypatch) -> None:
    _install_transport(
        monkeypatch, lambda request: httpx.Response(413, json={"detail": "pesa"})
    )
    ticket = browser.post(
        "/api/nas/zip", json={"paths": ["files/a.jpg"]}
    ).json()["id"]
    resp = browser.get(f"/api/nas/zip/{ticket}")
    assert resp.status_code == 413
    assert "demasiado grande" in resp.json()["detail"].lower()
