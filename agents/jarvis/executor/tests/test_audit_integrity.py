"""Integridad de la auditoría (hallazgos de revisión de código).

1. El `approval_id` vive en su propia columna: los parámetros largos no
   pueden hacerlo desaparecer ni dejar `params_json` truncado/inválido.
2. Cerrar la aprobación y registrar la auditoría ocurre en UNA transacción:
   si algo falla, no queda ni una cosa ni la otra.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import approvals, audit
from app.config import get_settings


@pytest.fixture
def isolated(exec_settings):
    return exec_settings


def _rows(db: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM executions ORDER BY id").fetchall()
    finally:
        conn.close()


def test_approval_id_column_survives_long_params(isolated):
    long_value = "x" * 5_000
    audit.record(
        "host1",
        "op",
        {"path": "/tmp", "nota": long_value},
        allowed=True,
        ok=True,
        approval_id="APROB-123",
    )

    rows = _rows(isolated.audit_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["approval_id"] == "APROB-123"
    # JSON válido y completo (no se recorta a 1000 caracteres).
    params = json.loads(row["params_json"])
    assert params["nota"] == long_value


def test_params_over_limit_stay_valid_json(isolated, monkeypatch):
    monkeypatch.setattr(audit, "_MAX_PARAMS_JSON", 50, raising=False)
    audit.record("host1", "op", {"nota": "y" * 200}, allowed=True, ok=True)

    row = _rows(isolated.audit_db)[0]
    params = json.loads(row["params_json"])  # no debe lanzar
    assert params["_truncated"] is True
    assert params["_preview"].startswith('{"nota": "yyy')


def test_finish_and_audit_are_atomic(isolated):
    """Si la transacción se deshace, no queda ni consumo ni auditoría."""
    approval = approvals.ensure_pending(
        requested_by="agente",
        host="host1",
        operation="op",
        params={"path": "/tmp"},
        decision=_decision(),
        risk="bajo",
        risk_description="prueba",
    )
    aid = approval["id"]
    approvals.decide(aid, True, "humano", "ok")
    approvals.claim(aid)

    with pytest.raises(RuntimeError):
        with audit.transaction() as conn:
            approvals.finish(aid, executed=True, exit_code=0, conn=conn)
            audit.record(
                "host1", "op", {"path": "/tmp"}, allowed=True, ok=True,
                approval_id=aid, conn=conn,
            )
            # fallo simulado entre el cierre y el COMMIT
            raise RuntimeError("corte simulado")

    # Rollback: la aprobación sigue reclamada (executing) y no hay auditoría.
    current = approvals.get(aid)
    assert current["status"] == approvals.EXECUTING
    assert _rows(isolated.audit_db) == []


def _decision():
    from app.policy import Decision

    return Decision(
        allowed=True,
        requires_approval=True,
        reason="requiere aprobacion",
        argv=["echo", "hola"],
        resolved_path="/tmp",
        timeout=5,
    )
