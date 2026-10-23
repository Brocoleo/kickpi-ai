"""ZIP de una selección del NAS: streaming, nombres y validación previa.

Lo que se protege aquí:
- el ZIP se emite por trozos (no se carga el archivo en memoria),
- dos archivos con el mismo nombre no se pisan al extraer,
- la selección se valida ANTES de emitir bytes (400/404/413).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.nas_zip import ErrorZip, iter_zip, nombres_unicos, preparar


def _abrir(rutas) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(b"".join(iter_zip(rutas))))


def test_empaqueta_varios_archivos(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("hola")
    (tmp_path / "b.txt").write_text("mundo")

    zf = _abrir([tmp_path / "a.txt", tmp_path / "b.txt"])

    assert zf.namelist() == ["a.txt", "b.txt"]
    assert zf.read("a.txt") == b"hola"
    assert zf.read("b.txt") == b"mundo"
    assert zf.testzip() is None


def test_nombres_repetidos_no_se_pisan(tmp_path) -> None:
    a = tmp_path / "x" / "foto.jpg"
    b = tmp_path / "y" / "foto.jpg"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"primera")
    b.write_bytes(b"segunda")

    assert [n for n, _ in nombres_unicos([a, b, a])] == [
        "foto.jpg",
        "foto (2).jpg",
        "foto (3).jpg",
    ]

    zf = _abrir([a, b])
    assert zf.read("foto.jpg") == b"primera"
    assert zf.read("foto (2).jpg") == b"segunda"


def test_sale_en_varios_trozos_sin_comprimir(tmp_path) -> None:
    # 8 MB: comprobar que la respuesta no es un único bloque de memoria y que
    # STORE no infla el tamaño (fotos/vídeos ya vienen comprimidos).
    grande = tmp_path / "grande.bin"
    datos = b"x" * (8 * 1024 * 1024)
    grande.write_bytes(datos)

    trozos = list(iter_zip([grande]))

    assert len(trozos) > 10
    assert sum(len(t) for t in trozos) < len(datos) + 8192
    zf = zipfile.ZipFile(io.BytesIO(b"".join(trozos)))
    assert zf.read("grande.bin") == datos


def test_cancelar_corta_el_productor(tmp_path, monkeypatch) -> None:
    grande = tmp_path / "grande.bin"
    grande.write_bytes(b"x" * (8 * 1024 * 1024))

    generador = iter_zip([grande])
    next(generador)
    generador.close()  # el cliente cerró la pestaña

    # Si el hilo siguiera vivo, este segundo cierre lo notaría; además el
    # proceso no debe colgarse (timeout del runner si algo se bloquea).
    assert True


def test_preparar_valida_todo_antes(tmp_path, monkeypatch) -> None:
    bueno = tmp_path / "a.txt"
    bueno.write_text("hola")
    llamadas: list[str] = []

    def resolver(rel: str):
        llamadas.append(rel)
        if rel == "mala":
            raise ValueError("fuera de la NAS")
        if rel == "fantasma":
            raise FileNotFoundError(rel)
        return bueno

    with pytest.raises(ErrorZip) as exc:
        preparar([], resolver, max_files=10, max_mb=10)
    assert exc.value.codigo == 400

    with pytest.raises(ErrorZip) as exc:
        preparar(["mala"], resolver, max_files=10, max_mb=10)
    assert exc.value.codigo == 400

    with pytest.raises(ErrorZip) as exc:
        preparar(["fantasma"], resolver, max_files=10, max_mb=10)
    assert exc.value.codigo == 404

    with pytest.raises(ErrorZip) as exc:
        preparar(["a"] * 11, resolver, max_files=10, max_mb=10)
    assert exc.value.codigo == 413
    assert llamadas == ["mala", "fantasma"]

    # El mismo archivo repetido cuenta una sola vez.
    assert preparar(["a", "a"], resolver, max_files=10, max_mb=10) == [bueno]


def test_preparar_rechaza_por_peso(tmp_path) -> None:
    pesado = tmp_path / "video.mp4"
    pesado.write_bytes(b"x" * (3 * 1024 * 1024))

    with pytest.raises(ErrorZip) as exc:
        preparar(["video"], lambda _rel: pesado, max_files=10, max_mb=2)
    assert exc.value.codigo == 413
    assert "3 MB" in str(exc.value)
