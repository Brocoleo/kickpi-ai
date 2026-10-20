"""Fixtures compartidas de las pruebas del ejecutor.

Las pruebas NUNCA tocan la base de datos real: `exec_settings` redirige
`audit_db` a un temporal y recarga la cache de configuracion.
"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture
def exec_settings(tmp_path, monkeypatch):
    """Configuracion aislada: BD y tokens en el temporal."""
    monkeypatch.setenv("JARVIS_EXECUTOR_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_EXECUTOR_TOKEN", "token-ejecutor")
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVALS_TOKEN", "token-aprobaciones")
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVAL_TTL_SECONDS", "300")
    monkeypatch.setenv("JARVIS_EXECUTOR_MAX_PENDING_PER_HOST", "20")
    monkeypatch.setenv("JARVIS_EXECUTOR_STALE_EXECUTING_SECONDS", "600")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
