"""Clasificación de adjuntos por reglas declarativas."""
from __future__ import annotations

import pytest

from app.classifier import ErrorReglas, clasificar, reglas_por_defecto


@pytest.mark.parametrize(
    ("nombre", "mime", "esperado"),
    [
        ("foto.JPG", "application/octet-stream", "Fotos"),
        ("captura.heic", None, "Fotos"),
        ("clip.mov", "video/quicktime", "Videos"),
        ("nota.m4a", "audio/x-m4a", "Audio"),
        ("cancion.mp3", None, "Audio"),
        ("factura.pdf", "application/octet-stream", "Documentos"),
        ("notas.md", None, "Documentos"),
        ("raro.xyz", None, "Documentos"),
    ],
)
def test_clasifica_por_extension(nombre, mime, esperado) -> None:
    assert clasificar(nombre, mime) == esperado


def test_mime_es_el_respaldo_sin_extension() -> None:
    assert clasificar("buque", "image/png") == "Fotos"
    assert clasificar("grabacion", "audio/mpeg") == "Audio"
    assert clasificar("adjunto", "text/plain") == "Documentos"


def test_la_extension_manda_sobre_el_mime() -> None:
    # Los navegadores confunden el content_type; el nombre es más fiable.
    assert clasificar("factura.pdf", "image/jpeg") == "Documentos"
    assert clasificar("foto.jpg", "application/pdf") == "Fotos"


def test_nombre_sin_extension_ni_mime_cae_en_el_default() -> None:
    assert clasificar("LEEME") == "Documentos"


def test_reglas_inyectadas() -> None:
    reglas = {
        "default_folder": "Varios",
        "rules": [
            {"folder": "Imagenes", "extensions": [".raw"], "mimes": ["image/"]},
        ],
    }
    assert clasificar("sesion.raw", reglas=reglas) == "Imagenes"
    assert clasificar("otra.bin", reglas=reglas) == "Varios"


def test_regla_con_carpeta_insegura_se_ignora() -> None:
    reglas = {
        "default_folder": "Documentos",
        "rules": [
            {"folder": "../fuera", "extensions": [".txt"]},
            {"folder": "Segura", "extensions": [".ok"]},
        ],
    }
    # La regla insegura no se aplica; .txt cae al default.
    assert clasificar("nota.txt", reglas=reglas) == "Documentos"
    assert clasificar("bien.ok", reglas=reglas) == "Segura"


def test_reglas_sin_entradas_validas_falla() -> None:
    with pytest.raises(ErrorReglas):
        clasificar("x.txt", reglas={"rules": []})


def test_las_reglas_por_defecto_incluyen_audio_y_todas_las_carpetas() -> None:
    carpetas = {regla["folder"] for regla in reglas_por_defecto()["rules"]}
    assert {"Fotos", "Videos", "Audio", "Documentos"} <= carpetas
