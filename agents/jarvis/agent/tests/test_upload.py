"""Subida de adjuntos del chat a la carpeta Entrada del NAS.

Lo que se protege aquí:
- el archivo cae dentro de la carpeta de entrada y no en otra ruta,
- un nombre con `../` no escapa,
- se exige sesión humana + CSRF (el token de API no sube archivos),
- el límite de tamaño se aplica ANTES de dejar basura.
"""
from __future__ import annotations

import types

from app import main
from app.config import get_settings


def _inbox(tmp_path):
    return tmp_path / "nas" / "Entrada"


def test_sube_un_adjunto_a_entrada(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("mi factura (1).pdf", b"%PDF-1.4 contenido", "application/pdf")},
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["ok"] is True
    assert datos["path"].startswith("Entrada/")
    assert datos["path"].endswith(".pdf")
    assert datos["size"] == len(b"%PDF-1.4 contenido")

    guardado = _inbox(tmp_path) / datos["path"].split("/", 1)[1]
    assert guardado.is_file()
    assert guardado.read_bytes() == b"%PDF-1.4 contenido"


def test_un_nombre_con_ruta_no_escapa(browser, tmp_path) -> None:
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("../../evil.txt", b"x", "text/plain")},
    )
    assert respuesta.status_code == 200, respuesta.text
    ruta = respuesta.json()["path"]
    assert ruta.startswith("Entrada/")
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
            upload_max_mb=0,  # cualquier byte supera el límite
            nas_inbox_uid=real.nas_inbox_uid,
            nas_inbox_gid=real.nas_inbox_gid,
        ),
    )
    respuesta = browser.post(
        "/api/files/upload",
        files={"file": ("grande.bin", b"1234567890", "application/octet-stream")},
    )
    assert respuesta.status_code == 413
    assert list(_inbox(tmp_path).glob("*")) == []
