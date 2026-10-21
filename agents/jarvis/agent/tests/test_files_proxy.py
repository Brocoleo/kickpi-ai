"""Pruebas del proxy same-origin de FileBrowser (`/files/*`).

Lo que se comprueba:
- exige autenticación (token de API o sesión de navegador),
- conserva el prefijo /files y la query string (la cookie de FileBrowser
  tiene Path=/files: sin el prefijo, el navegador no la enviaría),
- no propaga cabeceras hop-by-hop,
- conserva todas las cabeceras Set-Cookie (httpx las colapsa si se leen
  como un diccionario),
- reescribe Location absoluta hacia el origen del proxy,
- devuelve 502 (no 500) cuando FileBrowser no responde.
"""
from __future__ import annotations

import httpx
import pytest

from app import main
from conftest import API_TOKEN


def _install_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", factory)


def test_requires_auth(client):
    resp = client.get("/files/")
    assert resp.status_code == 401


def test_requires_auth_for_nested_paths(client):
    resp = client.get("/files/Files/carpeta/fichero.txt")
    assert resp.status_code == 401


def test_forwards_path_query_and_body(client, monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, stream=httpx.ByteStream(b"contenido"))

    _install_transport(monkeypatch, handler)

    resp = client.post(
        "/files/api/resources/torrents?action=sum&x=1",
        headers={"X-API-Token": API_TOKEN},
        content=b"payload",
    )

    assert resp.status_code == 200
    assert resp.content == b"contenido"
    assert seen["method"] == "POST"
    assert seen["url"] == (
        "http://filebrowser:8080/files/api/resources/torrents?action=sum&x=1"
    )
    # La cabecera host del cliente no debe filtrarse al contenedor.
    assert "testserver" not in seen["headers"].get("host", "")


def test_preserves_trailing_slash(client, monkeypatch):
    """FileBrowser responde 200 a /files/ y 307 a /files: no se normaliza."""
    vistos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    _install_transport(monkeypatch, handler)

    client.get("/files/", headers={"X-API-Token": API_TOKEN})
    client.get("/files", headers={"X-API-Token": API_TOKEN})
    client.get("/files/login", headers={"X-API-Token": API_TOKEN})

    assert vistos == [
        "http://filebrowser:8080/files/",
        "http://filebrowser:8080/files",
        "http://filebrowser:8080/files/login",
    ]


def test_keeps_every_set_cookie(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"ok"),
            headers=[
                ("set-cookie", "auth=abc; Path=/files; HttpOnly"),
                ("set-cookie", "pref=1; Path=/files"),
            ],
        )

    _install_transport(monkeypatch, handler)

    resp = client.get("/files/login", headers={"X-API-Token": API_TOKEN})

    cookies = resp.headers.get_list("set-cookie")
    assert cookies == [
        "auth=abc; Path=/files; HttpOnly",
        "pref=1; Path=/files",
    ]


def test_rewrites_absolute_location(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            stream=httpx.ByteStream(b""),
            headers={"location": "http://filebrowser:8080/files/login"},
        )

    _install_transport(monkeypatch, handler)

    resp = client.get(
        "/files/protected",
        headers={"X-API-Token": API_TOKEN},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/files/login"


def test_drops_hop_by_hop_headers(client, monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"ok"),
            headers={"transfer-encoding": "chunked", "x-app": "filebrowser"},
        )

    _install_transport(monkeypatch, handler)

    resp = client.get(
        "/files/",
        headers={"X-API-Token": API_TOKEN, "TE": "trailers"},
    )

    # "TE" es hop-by-hop: no debe llegar al contenedor. Lo que sí aparece es
    # la cabecera que añade el propio cliente httpx hacia el upstream.
    assert "te" not in seen["headers"]
    assert seen["headers"]["host"] == "filebrowser:8080"
    assert "transfer-encoding" not in resp.headers
    assert resp.headers["x-app"] == "filebrowser"


def test_does_not_leak_jarvis_session_cookie(client, monkeypatch):
    """La cookie de Jarvis no viaja al contenedor; la de FileBrowser sí."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    _install_transport(monkeypatch, handler)

    resp = client.get(
        "/files/",
        headers={
            "X-API-Token": API_TOKEN,
            "Cookie": "jarvis_session=secreto-de-jarvis; auth=jwt-de-filebrowser",
        },
    )

    assert resp.status_code == 200
    assert seen["cookie"] == "auth=jwt-de-filebrowser"


def test_upstream_down_returns_502(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin ruta al contenedor", request=request)

    _install_transport(monkeypatch, handler)

    resp = client.get("/files/", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 502


def test_disabled_proxy_returns_503(client, monkeypatch):
    monkeypatch.setenv("JARVIS_FILEBROWSER_URL", "")
    main.get_settings.cache_clear()
    try:
        resp = client.get("/files/", headers={"X-API-Token": API_TOKEN})
    finally:
        main.get_settings.cache_clear()
    assert resp.status_code == 503


def test_files_allows_same_origin_framing(client, monkeypatch):
    """El iframe de Archivos es del mismo origen: DENY lo dejaría en blanco."""
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, stream=httpx.ByteStream(b"<html>")),
    )
    resp = client.get("/files/", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"


def test_api_keeps_deny(client):
    """El resto del agente sigue sin poder enmarcarse."""
    resp = client.get("/health")
    assert resp.headers["x-frame-options"] == "DENY"


def test_reuses_one_client(client):
    """Un cliente por petición abriría una conexión por cada asset."""
    assert main._files_client() is main._files_client()


def test_retries_once_when_pooled_connection_died(client, monkeypatch):
    llamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    _install_transport(monkeypatch, handler)
    resp = client.get("/files/", headers={"X-API-Token": API_TOKEN})
    assert resp.status_code == 200
    assert resp.content == b"ok"
    assert llamadas["n"] == 2


def test_does_not_retry_a_post(client, monkeypatch):
    """Reintentar un POST podría duplicar una subida."""
    llamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas["n"] += 1
        raise httpx.RemoteProtocolError("Server disconnected")

    _install_transport(monkeypatch, handler)
    resp = client.post(
        "/files/api/resources/x", headers={"X-API-Token": API_TOKEN}, content=b"datos"
    )
    assert resp.status_code == 502
    assert llamadas["n"] == 1
