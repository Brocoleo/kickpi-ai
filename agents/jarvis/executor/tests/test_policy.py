"""Pruebas del Policy Engine — denegar por defecto.

Se ejecutan dentro del contenedor del ejecutor:
    docker compose exec jarvis-executor pytest -q
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.registry import Host, Operation, Project, Registry
from app.policy import check


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "repo").mkdir()
    (outside / "secret.txt").write_text("no_should_read")
    return allowed, outside


@pytest.fixture
def registry(roots: tuple[Path, Path]) -> Registry:
    allowed, _ = roots
    host = Host(
        id="testhost",
        label="Test",
        transport="local",
        enabled=True,
        roots=(str(allowed),),
    )
    ops = {
        "read_ok": Operation(
            id="read_ok",
            title="lectura",
            command=("ls", "-la", "{path}"),
            auto=True,
            path_param="path",
        ),
        "read_not_auto": Operation(
            id="read_not_auto",
            title="no auto",
            command=("ls",),
            auto=False,
        ),
        "only_other_host": Operation(
            id="only_other_host",
            title="otro host",
            command=("ls",),
            auto=True,
            hosts=("otro",),
        ),
        "with_count": Operation(
            id="with_count",
            title="con limite",
            command=("git", "log", "-n", "{count}"),
            auto=True,
            path_param="path",
            int_param="count",
            int_param_max=30,
        ),
        "project_op": Operation(
            id="project_op",
            title="tests",
            command=(),
            auto=True,
            project_param="project_id",
        ),
    }
    projects = {
        "proj_ok": Project(
            id="proj_ok",
            label="ok",
            host="testhost",
            path=str(allowed / "repo"),
            test_command=("echo", "tests"),
        ),
        "proj_no_tests": Project(
            id="proj_no_tests",
            label="sin tests",
            host="testhost",
            path=str(allowed / "repo"),
            test_command=None,
        ),
        "proj_other_host": Project(
            id="proj_other_host",
            label="otro host",
            host="otro",
            path=str(allowed / "repo"),
            test_command=("echo", "x"),
        ),
    }
    return Registry(hosts={"testhost": host}, operations=ops, projects=projects)


async def test_unknown_host_denied(registry):
    d = await check(registry, "noexiste", "read_ok", {})
    assert not d.allowed
    assert "host desconocido" in d.reason


async def test_disabled_host_denied(registry):
    disabled = Host(id="off", label="Off", transport="local", enabled=False)
    registry.hosts["off"] = disabled
    d = await check(registry, "off", "read_ok", {})
    assert not d.allowed
    assert "no habilitado" in d.reason


async def test_unknown_operation_denied(registry):
    """Regla 2: lo que no esta en el catalogo no existe."""
    d = await check(registry, "testhost", "rm_minus_rf_slash", {})
    assert not d.allowed
    assert "no permitida" in d.reason


async def test_non_auto_operation_denied(registry):
    d = await check(registry, "testhost", "read_not_auto", {})
    assert not d.allowed
    assert "aprobacion" in d.reason


async def test_operation_not_allowed_on_host(registry):
    d = await check(registry, "testhost", "only_other_host", {})
    assert not d.allowed
    assert "no aplica" in d.reason


async def test_path_outside_roots_denied(registry, roots):
    allowed, outside = roots
    d = await check(registry, "testhost", "read_ok", {"path": str(outside)})
    assert not d.allowed
    assert "fuera de las raices" in d.reason


async def test_path_with_dotdot_denied(registry, roots):
    allowed, outside = roots
    d = await check(
        registry, "testhost", "read_ok", {"path": f"{allowed}/../outside"}
    )
    assert not d.allowed
    assert ".." in d.reason


async def test_relative_path_denied(registry):
    d = await check(registry, "testhost", "read_ok", {"path": "repo"})
    assert not d.allowed
    assert "absoluta" in d.reason


async def test_missing_path_param_denied(registry):
    d = await check(registry, "testhost", "read_ok", {})
    assert not d.allowed
    assert "falta el parametro" in d.reason


async def test_symlink_escaping_root_denied(registry, roots):
    """Un symlink DENTRO de la raiz que apunta fuera debe denegarse."""
    allowed, outside = roots
    link = allowed / "escape"
    link.symlink_to(outside)
    d = await check(registry, "testhost", "read_ok", {"path": str(link)})
    assert not d.allowed
    assert "fuera de las raices" in d.reason


async def test_symlink_inside_root_allowed(registry, roots):
    """Un symlink que apunta DENTRO de la raiz si se permite."""
    allowed, _ = roots
    link = allowed / "alias"
    link.symlink_to(allowed / "repo")
    d = await check(registry, "testhost", "read_ok", {"path": str(link)})
    assert d.allowed
    assert str(link) not in (d.argv or [])  # se sustituye por la ruta RESUELTA
    assert d.argv[-1] == str(allowed / "repo")


async def test_int_param_out_of_range_denied(registry, roots):
    allowed, _ = roots
    d = await check(
        registry, "testhost", "with_count", {"path": str(allowed), "count": 9999}
    )
    assert not d.allowed
    assert "fuera de rango" in d.reason


async def test_int_param_not_int_denied(registry, roots):
    allowed, _ = roots
    d = await check(
        registry, "testhost", "with_count", {"path": str(allowed), "count": "abc"}
    )
    assert not d.allowed
    assert "entero" in d.reason


async def test_project_allowed(registry):
    d = await check(registry, "testhost", "project_op", {"project_id": "proj_ok"})
    assert d.allowed
    assert d.argv == ["echo", "tests"]


async def test_project_unknown_denied(registry):
    d = await check(registry, "testhost", "project_op", {"project_id": "nope"})
    assert not d.allowed
    assert "proyecto desconocido" in d.reason


async def test_project_without_tests_denied(registry):
    d = await check(registry, "testhost", "project_op", {"project_id": "proj_no_tests"})
    assert not d.allowed
    assert "no define suite" in d.reason


async def test_project_host_mismatch_denied(registry):
    d = await check(registry, "testhost", "project_op", {"project_id": "proj_other_host"})
    assert not d.allowed
    assert "pertenece a" in d.reason
