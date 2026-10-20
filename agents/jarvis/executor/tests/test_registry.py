"""Pruebas del registro de hosts y del catalogo.

Incluye la recarga en caliente: si cambia hosts.yml o catalog.yml, el
registro se vuelve a parsear sin reiniciar el contenedor (y por tanto sin
necesitar sudo para aplicar un cambio de configuracion).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app import registry as registry_module
from app.config import Settings
from app.registry import load_registry

HOSTS_YAML = """
version: 1
hosts:
  - id: {hid}
    label: "Host de prueba"
    transport: {transport}
    enabled: {enabled}
    address: {address}
    user: leandro
    roots:
      - /tmp/raiz
projects:
  - id: {pid}
    label: "Proyecto"
    host: {hid}
    path: "/tmp/raiz/proyecto"
    test_command: ["echo", "ok"]
    enabled: true
"""

CATALOG_YAML = """
version: 1
operations:
  - id: {op}
    title: "Operacion"
    command: ["echo", "hola"]
    auto: {auto}
"""


@pytest.fixture
def files(tmp_path: Path, monkeypatch):
    hosts = tmp_path / "hosts.yml"
    catalog = tmp_path / "catalog.yml"
    hosts.write_text(
        HOSTS_YML_TEMPLATE.format(
            hid="uno", transport="local", enabled="true", address="null", pid="proj"
        ),
        encoding="utf-8",
    )
    catalog.write_text(
        CATALOG_YML_TEMPLATE.format(op="op_uno", auto="true"), encoding="utf-8"
    )

    settings = Settings(
        hosts_file=str(hosts),
        catalog_file=str(catalog),
        ssh_dir=str(tmp_path / "ssh"),
        audit_db=str(tmp_path / "audit.db"),
    )
    monkeypatch.setattr(registry_module, "get_settings", lambda: settings)
    monkeypatch.setattr(registry_module, "load_registry", load_registry)
    registry_module._CACHE = None
    return hosts, catalog


HOSTS_YML_TEMPLATE = HOSTS_YAML
CATALOG_YML_TEMPLATE = CATALOG_YAML


def test_registry_loads(files):
    hosts, _ = files
    registry = load_registry()
    assert "uno" in registry.hosts
    assert registry.hosts["uno"].transport == "local"
    assert "op_uno" in registry.operations
    assert "proj" in registry.projects


def test_registry_hot_reload_on_change(files):
    """Cambiar hosts.yml recarga el registro sin reiniciar nada."""
    hosts, _ = files
    registry = load_registry()
    assert "uno" in registry.hosts
    assert "dos" not in registry.hosts

    # Reescribimos con un host mas y advanced mtime (evita la granularidad).
    hosts.write_text(
        HOSTS_YML_TEMPLATE.format(
            hid="dos", transport="ssh", enabled="true", address="100.64.0.9", pid="proj2"
        ),
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(hosts, (future, future))

    registry = load_registry()
    assert "dos" in registry.hosts, "el registro deberia haberse recargado"
    assert "uno" not in registry.hosts
    assert registry.hosts["dos"].address == "100.64.0.9"


def test_registry_hot_reload_on_catalog_change(files):
    _, catalog = files
    assert "op_uno" in load_registry().operations

    catalog.write_text(
        CATALOG_YML_TEMPLATE.format(op="op_dos", auto="false"), encoding="utf-8"
    )
    future = time.time() + 5
    os.utime(catalog, (future, future))

    registry = load_registry()
    assert "op_dos" in registry.operations
    assert registry.operations["op_dos"].auto is False


def test_registry_cache_avoids_reparse(files):
    """Sin cambios, no se reparsea (la segunda llamada usa la cache)."""
    hosts, _ = files
    first = load_registry()
    mtime_before = os.stat(hosts).st_mtime
    second = load_registry()
    assert first is second
    assert os.stat(hosts).st_mtime == mtime_before
