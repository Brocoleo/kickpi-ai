"""Subida de adjuntos del chat a la carpeta Entrada del NAS.

Lo que se protege aquí:
- el archivo cae dentro de la carpeta de entrada y no en otra ruta,
- se clasifica en Fotos/Videos/Audio/Documentos según el tipo,
- un nombre con `../` no escapa,
- se exige sesión humana + CSRF (el token de API no sube archivos),
- el límite de tamaño se aplica ANTES de dejar basura.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app import main
from app.config import get_settings

_PREFIJO = "files/Entrada/"


@pytest.fixture(autouse=True)
def _limite_limpio():
    """Cada test parte con el contador de subidas por IP a cero."""
    from app.security import reset_upload_limit

    reset_upload_limit()
    yield
    reset_upload_limit()


def _inbox(tmp_path):
    return tmp_path / "nas" / "Entrada"


def _ruta_local(tmp_path, rel: str) -> Path:
    """Traduce la ruta relativa al NAS a la ruta real del inbox de prueba."""
    assert rel.startswith(_PREFIJO), rel
    return _inbox(tmp_path) / rel[len(_PREFIJO) :]


def _archivos(tmp_path) -> list[Path]:
    raiz = _inbox(tmp_path)
    if not raiz.exists():
        return []
    return [p for p in raiz.rglob("*") if p.is_file()]


def test_sube_un_adjunto_a_entrada(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("mi factura (1).pdf", b"%PDF-1.4 contenido", "application/pdf")},
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["ok"] is True
    assert datos["path"].startswith(f"{_PREFIJO}Documentos/")
    assert datos["path"].endswith(".pdf")
    assert datos["folder"] == "Documentos"
    assert datos["size"] == len(b"%PDF-1.4 contenido")

    guardado = _ruta_local(tmp_path, datos["path"])
    assert guardado.is_file()
    assert guardado.read_bytes() == b"%PDF-1.4 contenido"


@pytest.mark.parametrize(
    ("nombre", "mime", "carpeta"),
    [
        ("foto.jpg", "image/jpeg", "Fotos"),
        ("captura.PNG", "application/octet-stream", "Fotos"),
        ("clip.MOV", "video/quicktime", "Videos"),
        ("informe.pdf", "application/pdf", "Documentos"),
        ("nota.txt", "text/plain", "Documentos"),
        ("voz.m4a", "audio/mp4", "Audio"),
        ("grabacion.mp3", "application/octet-stream", "Audio"),
        # Sin extensión conocida: manda el content_type como respaldo.
        ("adjunto-sin-extension", "image/png", "Fotos"),
        ("nota-de-voz", "audio/ogg", "Audio"),
    ],
)
def test_clasifica_adjuntos_por_tipo(browser, tmp_path, nombre, mime, carpeta) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": (nombre, b"contenido", mime)},
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["path"].startswith(f"{_PREFIJO}{carpeta}/"), datos["path"]
    assert _ruta_local(tmp_path, datos["path"]).is_file()


def test_un_nombre_con_ruta_no_escapa(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("../../evil.txt", b"x", "text/plain")},
    )
    assert respuesta.status_code == 200, respuesta.text
    ruta = respuesta.json()["path"]
    assert ruta.startswith(_PREFIJO)
    assert ".." not in ruta
    assert (tmp_path / "evil.txt").exists() is False
    assert (tmp_path / "nas" / "evil.txt").exists() is False


def test_exige_sesion_humana(client) -> None:
    respuesta = client.post(
        "/api/files/upload",
        files={"file": ("x.txt", b"x", "text/plain")},
    )
    assert respuesta.status_code == 401


def test_exige_csrf(browser) -> None:
    original = browser.headers.pop("X-CSRF-Token")
    try:
        respuesta = browser.post(
            "/api/files/upload",
            files={"file": ("x.txt", b"x", "text/plain")},
        )
        assert respuesta.status_code == 403
    finally:
        browser.headers["X-CSRF-Token"] = original


def test_limite_de_tamano_no_deja_restos(browser, tmp_path, monkeypatch) -> None:
    # El límite sale de la configuración en caliente (BD), no de get_settings.
    monkeypatch.setattr(main.config_store, "ajustes_subida", lambda: (0, 0, None))
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("grande.bin", b"1234567890", "application/octet-stream")},
    )
    assert respuesta.status_code == 413
    assert _archivos(tmp_path) == []


def test_rechaza_un_ejecutable_disfrazado(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("documento.pdf", b"MZ\x90\x00binario", "application/pdf")},
    )
    assert respuesta.status_code == 415
    assert _archivos(tmp_path) == []


def test_limite_de_subidas_por_ip(browser, tmp_path, monkeypatch) -> None:
    from app.security import reset_upload_limit

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_rate_per_min", 1)
    reset_upload_limit()
    headers = {"X-Forwarded-For": "100.64.0.9"}
    try:
        primera = browser.post(
            "/api/files/upload",
            files={"file": ("uno.txt", b"uno", "text/plain")},
            headers=headers,
        )
        assert primera.status_code == 200, primera.text

        segunda = browser.post(
            "/api/files/upload",
            files={"file": ("dos.txt", b"dos", "text/plain")},
            headers=headers,
        )
        assert segunda.status_code == 429
        assert int(segunda.headers["retry-after"]) >= 1
    finally:
        reset_upload_limit()


def test_borra_un_adjunto_de_entrada(browser, tmp_path) -> None:
    subido = browser.post(
        "/api/files/upload",
        files={"file": ("borrar.txt", b"temporal", "text/plain")},
    ).json()
    guardado = _ruta_local(tmp_path, subido["path"])
    assert guardado.is_file()

    respuesta = browser.delete(f"/api/files?path={subido['path']}")
    assert respuesta.status_code == 200, respuesta.text
    assert not guardado.exists()


def test_borra_un_adjunto_en_subcarpeta(browser, tmp_path) -> None:
    subido = browser.post(
        "/api/files/upload",
        files={"file": ("foto.jpg", b"\xff\xd8\xff\xe0jpeg", "image/jpeg")},
    ).json()
    assert subido["path"].startswith(f"{_PREFIJO}Fotos/")
    guardado = _ruta_local(tmp_path, subido["path"])
    assert guardado.is_file()

    respuesta = browser.delete(f"/api/files?path={subido['path']}")
    assert respuesta.status_code == 200, respuesta.text
    assert not guardado.exists()


def test_borrar_refresca_el_indice(browser, tmp_path, monkeypatch) -> None:
    """El DELETE invalida el índice antes de responder.

    Sin esto, la lista de recientes (que se sirve del índice en memoria)
    seguiría mostrando el archivo borrado hasta 15 minutos.
    """
    llamadas: list[tuple[str, str]] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append((request.method, str(request.url)))
        return httpx.Response(200, json={"ok": True})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", factory)

    subido = browser.post(
        "/api/files/upload",
        files={"file": ("borrar.txt", b"x", "text/plain")},
    ).json()
    llamadas.clear()

    respuesta = browser.delete(f"/api/files?path={subido['path']}")
    assert respuesta.status_code == 200, respuesta.text
    assert any(
        metodo == "POST"
        and "/nas/refresh" in url
        and "subruta=files%2FEntrada" in url
        for metodo, url in llamadas
    ), llamadas


def test_no_borra_fuera_de_entrada(browser, tmp_path) -> None:
    fuera = tmp_path / "nas" / "Documentos" / "importante.txt"
    fuera.parent.mkdir(parents=True, exist_ok=True)
    fuera.write_text("no tocar", encoding="utf-8")

    respuesta = browser.delete("/api/files?path=Documentos/importante.txt")
    assert respuesta.status_code == 403
    assert fuera.is_file()

    # La ruta absoluta del NAS tampoco cuela.
    otra = browser.delete("/api/files?path=files/Documentos/importante.txt")
    assert otra.status_code == 403
    assert fuera.is_file()

    escapar = browser.delete(
        "/api/files?path=files/Entrada/../Documentos/importante.txt"
    )
    assert escapar.status_code in (400, 403)
    assert fuera.is_file()


def test_el_token_api_no_borra(browser, tmp_path) -> None:
    subido = browser.post(
        "/api/files/upload",
        files={"file": ("token.txt", b"x", "text/plain")},
    ).json()
    # `browser` muta cabeceras compartidas; se quitan CSRF y cookie y se usa
    # el token de API, que no debe poder ejecutar una acción destructiva.
    from conftest import API_TOKEN

    guardadas = dict(browser.headers)
    for clave in ("X-CSRF-Token", "Origin"):
        browser.headers.pop(clave, None)
    browser.cookies.clear()
    try:
        respuesta = browser.delete(
            f"/api/files?path={subido['path']}",
            headers={"X-API-Token": API_TOKEN},
        )
        assert respuesta.status_code == 403
    finally:
        browser.headers.clear()
        browser.headers.update(guardadas)


# --------------------------------------------------------------------------- #
# Subida por lotes (F7)
# --------------------------------------------------------------------------- #
def _lote(*archivos: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """httpx espera una lista de (campo, (nombre, bytes, mime))."""
    return [("files", archivo) for archivo in archivos]


def test_lote_sube_varios_y_clasifica(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("foto.jpg", b"\xff\xd8\xff\xe0jpeg", "image/jpeg"),
            ("clip.mov", b"video", "video/quicktime"),
            ("voz.m4a", b"audio", "audio/mp4"),
            ("informe.pdf", b"%PDF-1.4", "application/pdf"),
        ),
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["ok"] is True
    assert datos["uploaded"] == 4
    assert datos["failed"] == 0

    carpetas = [item["folder"] for item in datos["results"]]
    assert carpetas == ["Fotos", "Videos", "Audio", "Documentos"]
    for item in datos["results"]:
        assert item["path"].startswith(f"{_PREFIJO}{item['folder']}/")
        assert _ruta_local(tmp_path, item["path"]).is_file()
    assert len(_archivos(tmp_path)) == 4


def test_lote_aisla_el_archivo_que_falla(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("bien.txt", b"ok", "text/plain"),
            ("malo.exe", b"MZ\x90\x00", "application/octet-stream"),
            ("otro.pdf", b"%PDF-1.4", "application/pdf"),
        ),
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    # El lote entero NO falla: solo el archivo rechazado.
    assert datos["ok"] is False
    assert datos["uploaded"] == 2
    assert datos["failed"] == 1
    assert [item["ok"] for item in datos["results"]] == [True, False, True]
    assert "no se admite" in datos["results"][1]["error"]
    # No queda rastro del .exe rechazado.
    assert not any(p.suffix == ".exe" for p in _archivos(tmp_path))


def test_lote_demasiado_grande_se_rechaza_entero(browser, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "MAX_LOTE_ARCHIVOS", 2)
    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("a.txt", b"a", "text/plain"),
            ("b.txt", b"b", "text/plain"),
            ("c.txt", b"c", "text/plain"),
        ),
    )
    assert respuesta.status_code == 413
    assert "Máximo 2 archivos por lote" in respuesta.json()["detail"]
    assert _archivos(tmp_path) == []


def test_lote_no_cabe_en_el_limite_por_ip(browser, tmp_path, monkeypatch) -> None:
    """El freno por IP se reserva para todo el lote ANTES de escribir."""
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_rate_per_min", 2)
    headers = {"X-Forwarded-For": "100.64.0.77"}

    # El primer lote agota la ventana (2 de 2).
    primero = browser.post(
        "/api/files/upload-batch",
        files=_lote(("a.txt", b"a", "text/plain"), ("b.txt", b"b", "text/plain")),
        headers=headers,
    )
    assert primero.status_code == 200, primero.text

    # El siguiente no cabe: 429 con cuánto esperar, y no escribe nada.
    segundo = browser.post(
        "/api/files/upload-batch",
        files=_lote(("c.txt", b"c", "text/plain")),
        headers=headers,
    )
    assert segundo.status_code == 429
    assert int(segundo.headers["retry-after"]) >= 1
    assert len(_archivos(tmp_path)) == 2


def test_lote_mayor_que_el_limite_entero_se_rechaza(browser, tmp_path, monkeypatch) -> None:
    """Un lote de 3 con límite 2 no se arregla esperando: hay que dividirlo."""
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_rate_per_min", 2)
    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("a.txt", b"a", "text/plain"),
            ("b.txt", b"b", "text/plain"),
            ("c.txt", b"c", "text/plain"),
        ),
        headers={"X-Forwarded-For": "100.64.0.78"},
    )
    assert respuesta.status_code == 413
    detalle = respuesta.json()["detail"]
    assert "no cabe" in detalle and "Ajustes" in detalle
    assert _archivos(tmp_path) == []


def test_lote_supera_el_peso_y_no_deja_restos(browser, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "MAX_LOTE_BYTES", 5)
    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("uno.txt", b"1234", "text/plain"),
            ("dos.txt", b"5678", "text/plain"),
        ),
    )
    assert respuesta.status_code == 413
    detalle = respuesta.json()["detail"]
    assert "supera" in detalle and "divide" in detalle
    # Lo ya escrito en este lote se deshace: no queda un lote a medias.
    assert _archivos(tmp_path) == []


def test_lote_refresca_el_indice_una_vez(browser, tmp_path, monkeypatch) -> None:
    llamadas: list[str] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", factory)

    respuesta = browser.post(
        "/api/files/upload-batch",
        files=_lote(
            ("a.txt", b"a", "text/plain"),
            ("b.txt", b"b", "text/plain"),
            ("c.txt", b"c", "text/plain"),
        ),
    )
    assert respuesta.status_code == 200, respuesta.text
    refrescos = [url for url in llamadas if "/nas/refresh" in url]
    assert len(refrescos) == 1, llamadas


def test_reservar_uploads_es_atomico() -> None:
    from app.security import reservar_uploads, reset_upload_limit

    reset_upload_limit()
    try:
        assert reservar_uploads("ip-test", 2, 2)[0] is True
        # Ya no cabe un lote de 3, ni siquiera uno de 1: la ventana está llena.
        assert reservar_uploads("ip-test", 3, 2)[0] is False
        assert reservar_uploads("ip-test", 1, 2)[0] is False
        # Otra IP tiene su propia ventana.
        assert reservar_uploads("ip-otra", 2, 2)[0] is True
        # Sin límite (0), todo pasa.
        assert reservar_uploads("ip-test", 50, 0)[0] is True
    finally:
        reset_upload_limit()
