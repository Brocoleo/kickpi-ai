"""Subida de adjuntos del chat a la carpeta Entrada del NAS.

Lo que se protege aquí:
- el archivo cae dentro de la carpeta de entrada y no en otra ruta,
- se clasifica en Fotos/Videos/Documentos según el tipo,
- un nombre con `../` no escapa,
- se exige sesión humana + CSRF (el token de API no sube archivos),
- el límite de tamaño se aplica ANTES de dejar basura.
"""
from __future__ import annotations

import types
from pathlib import Path

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
        ("voz.m4a", "audio/mp4", "Documentos"),
        # Sin extensión conocida: manda el content_type como respaldo.
        ("adjunto-sin-extension", "image/png", "Fotos"),
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
    real = get_settings()
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: types.SimpleNamespace(
            nas_inbox_dir=real.nas_inbox_dir,
            nas_inbox_rel=real.nas_inbox_rel,
            upload_max_mb=0,  # cualquier byte supera el límite
            upload_rate_per_min=real.upload_rate_per_min,
            nas_inbox_uid=real.nas_inbox_uid,
            nas_inbox_gid=real.nas_inbox_gid,
        ),
    )
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
