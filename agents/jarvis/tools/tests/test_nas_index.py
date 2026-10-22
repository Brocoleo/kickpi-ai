"""Pruebas del índice de la NAS (pieza A de la iteración NAS).

Se prueba el módulo puro, sin FastAPI ni Docker: el árbol de archivos se crea
en un directorio temporal. Lo importante es que la búsqueda sea útil de
verdad: sin acentos, con contenido y con un tope de entradas.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from app.nas_index import IndiceNAS


def _escribir(ruta: Path, contenido: bytes, mtime: float | None = None) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)
    if mtime is not None:
        os.utime(ruta, (mtime, mtime))


def _arbol(tmp_path: Path) -> Path:
    raiz = tmp_path / "nas"
    _escribir(raiz / "Contratos" / "Factura Orange 2026.pdf", b"%PDF-1.4 ...")
    _escribir(
        raiz / "notas" / "reunion.md",
        "Hoy hablamos del router y de la factura de Orange.\nOtra línea.\n".encode(),
    )
    _escribir(raiz / "fotos" / "IMG_0001.jpg", b"\xff\xd8\xff\xe0")
    _escribir(raiz / "viejos" / "a.txt", b"nada\n", mtime=time.time() - 86_400 * 30)
    # Ruido que no debe entrar en el índice.
    _escribir(raiz / "node_modules" / "paquete.js", b"x")
    _escribir(raiz / ".git" / "config", b"x")
    _escribir(raiz / "@eaDir" / "thumb.jpg", b"x")
    return raiz


def _indice(raiz: Path, **kw) -> IndiceNAS:
    indice = IndiceNAS(raiz, **kw)
    assert indice.asegurar(5.0) is True
    return indice


def test_indice_ignora_ruido_y_cuenta(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    estado = indice.estado()
    assert estado["ready"] is True
    assert estado["files"] == 4  # pdf + md + jpg + txt
    assert estado["error"] is None
    assert estado["truncated"] is False


def test_busca_por_nombre_sin_acentos_ni_mayusculas(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    salida = indice.buscar("REUNIÓN", contenido=False)
    assert salida["ready"] is True
    assert salida["results"], salida
    assert salida["results"][0]["path"] == "notas/reunion.md"
    assert salida["results"][0]["match"] == "nombre"


def test_busca_dentro_del_contenido_con_fragmento(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    salida = indice.buscar("router")
    assert len(salida["results"]) == 1
    item = salida["results"][0]
    assert item["path"] == "notas/reunion.md"
    assert item["match"] == "contenido"
    assert "router" in item["snippet"]


def test_el_nombre_gana_al_contenido(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    salida = indice.buscar("orange")
    rutas = [r["path"] for r in salida["results"]]
    assert rutas[0] == "Contratos/Factura Orange 2026.pdf"
    assert "notas/reunion.md" in rutas
    assert salida["total_matches"] == 2


def test_sin_contenido_solo_mira_nombre_y_ruta(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    salida = indice.buscar("router", contenido=False)
    assert salida["results"] == []


def test_respeta_el_limite(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    salida = indice.buscar("orange", limite=1)
    assert salida["count"] == 1
    assert len(salida["results"]) == 1
    assert salida["total_matches"] == 2


def test_recientes_por_fecha_y_subcarpeta(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path))
    todo = indice.recientes(limite=10)
    rutas = [f["path"] for f in todo["files"]]
    # El archivo de hace un mes queda al final.
    assert rutas[-1] == "viejos/a.txt"

    solo_notas = indice.recientes(limite=10, bajo="notas")
    assert [f["path"] for f in solo_notas["files"]] == ["notas/reunion.md"]


def test_tope_de_entradas_se_declara(tmp_path) -> None:
    indice = _indice(_arbol(tmp_path), max_files=2)
    estado = indice.estado()
    assert estado["files"] == 2
    assert estado["truncated"] is True


def test_raiz_inexistente_no_rompe(tmp_path) -> None:
    indice = IndiceNAS(tmp_path / "no-existe")
    assert indice.asegurar(5.0) is False
    estado = indice.estado()
    assert estado["ready"] is False
    assert "no existe" in (estado["error"] or "")
    salida = indice.buscar("x")
    assert salida["results"] == []
    assert salida["ready"] is False
