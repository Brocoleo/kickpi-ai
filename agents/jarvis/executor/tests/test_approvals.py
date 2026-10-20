"""Pruebas del sistema de aprobaciones humanas (Fase C).

Cubre el requisito completo:
- la operacion no auto-aprobable NO se ejecuta sin humano;
- la aprobacion esta vinculada a host, operacion, argumentos y hash;
- un solo uso, con consumo atomico;
- expiracion, rechazo, host deshabilitado, operacion fuera de catalogo;
- nada se reintenta si el resultado es incierto.

No requieren FastAPI: se ejecutan en el contenedor y tambien en el host.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app import approvals
from app.config import get_settings
from app.policy import check
from app.registry import Host, Operation, Project, Registry


@pytest.fixture
def roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "repo").mkdir()
    for name in ("r0", "r1", "r9"):
        (allowed / name).mkdir()
    return allowed


@pytest.fixture
def registry(roots) -> Registry:
    host = Host(
        id="testhost",
        label="Test",
        transport="local",
        enabled=True,
        roots=(str(roots),),
    )
    ops = {
        "read_ok": Operation(
            id="read_ok",
            title="lectura",
            command=("ls", "-la", "{path}"),
            auto=True,
            path_param="path",
        ),
        "needs_approval": Operation(
            id="needs_approval",
            title="requiere aprobacion",
            description="Operacion que exige aprobacion humana.",
            command=("echo", "ejecutado", "{path}"),
            auto=False,
            path_param="path",
        ),
        "needs_approval_no_path": Operation(
            id="needs_approval_no_path",
            title="sin ruta",
            command=("uname", "-a"),
            auto=False,
        ),
        "only_other_host": Operation(
            id="only_other_host",
            title="otro host",
            command=("ls",),
            auto=True,
            hosts=("otro",),
        ),
    }
    return Registry(hosts={"testhost": host}, operations=ops, projects={})


@pytest.fixture
def pending(registry, roots):
    """Decision valida que requiere aprobacion, ya materializada."""
    decision = check_sync(registry, "needs_approval", {"path": str(roots)})
    return decision


def check_sync(registry, operation_id, params):
    """Atajo sincrono para el policy engine (las pruebas son sync)."""
    return asyncio.run(check(registry, "testhost", operation_id, params))


# ---------------------------------------------------------------------------
# Policy Engine: tercer estado
# ---------------------------------------------------------------------------


def test_non_auto_requires_approval_not_plain_deny(registry, roots):
    """Regla 3 revisada: valida, pero NO se ejecuta sola."""
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    assert d.allowed is False
    assert d.requires_approval is True
    assert "aprobacion" in d.reason
    # La decision trae el argv NORMALIZADO (lo que se aprobara y ejecutara).
    assert d.argv is not None
    assert d.argv[-1] == str(roots)


def test_non_auto_with_invalid_path_is_plain_deny(registry, tmp_path):
    """Una operacion prohibida NO genera nada aprobable."""
    outside = tmp_path / "outside"
    outside.mkdir()
    d = check_sync(registry, "needs_approval", {"path": str(outside)})
    assert d.allowed is False
    assert d.requires_approval is False
    assert "raices" in d.reason


def test_unknown_operation_never_requires_approval(registry):
    d = check_sync(registry, "rm_rf", {})
    assert d.allowed is False
    assert d.requires_approval is False


def test_non_auto_on_wrong_host_is_plain_deny(registry):
    registry.operations["only_other_host"] = Operation(
        id="only_other_host", title="otro", command=("ls",), auto=False, hosts=("otro",)
    )
    d = check_sync(registry, "only_other_host", {})
    assert d.allowed is False
    assert d.requires_approval is False
    assert "no aplica" in d.reason


def test_auto_operation_still_executes(registry, roots):
    d = check_sync(registry, "read_ok", {"path": str(roots)})
    assert d.allowed is True
    assert d.requires_approval is False


# ---------------------------------------------------------------------------
# Ciclo de vida de la solicitud
# ---------------------------------------------------------------------------


def test_create_pending_separates_requested_and_approved(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente",
        host="testhost",
        operation="needs_approval",
        params={"path": str(roots)},
        decision=d,
        risk="write",
        risk_description="Operacion que exige aprobacion humana.",
    )
    assert a["status"] == "pending"
    assert a["requested_by"] == "agente"
    assert a["approved_by"] is None          # el humano aun no ha decidido
    assert a["host"] == "testhost"
    assert a["operation"] == "needs_approval"
    assert a["params"]["path"] == str(roots)
    assert a["argv"] == d.argv               # argv normalizado persistido
    assert a["risk"] == "write"
    assert a["expires_at"] > a["created_at"]


def test_pending_is_idempotent_per_request(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    kwargs = dict(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    first = approvals.ensure_pending(**kwargs)
    second = approvals.ensure_pending(**kwargs)
    assert first["id"] == second["id"]       # el chat no duplica solicitudes
    assert len(approvals.list_approvals()) == 1


def test_max_pending_per_host(registry, roots, exec_settings, monkeypatch):
    monkeypatch.setenv("JARVIS_EXECUTOR_MAX_PENDING_PER_HOST", "2")
    get_settings.cache_clear()
    for index in range(2):
        d = check_sync(registry, "needs_approval", {"path": str(roots / f"r{index}")})
        approvals.ensure_pending(
            requested_by="agente", host="testhost", operation="needs_approval",
            params={"path": str(roots / f"r{index}")}, decision=d,
        )
    d = check_sync(registry, "needs_approval", {"path": str(roots / "r9")})
    with pytest.raises(RuntimeError) as exc:
        approvals.ensure_pending(
            requested_by="agente", host="testhost", operation="needs_approval",
            params={"path": str(roots / "r9")}, decision=d,
        )
    assert "demasiadas" in str(exc.value)


def test_approve_requires_human_identity(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    with pytest.raises(ValueError):
        approvals.decide(a["id"], approve=True, approved_by="")
    # Sin humano no hay aprobacion: sigue pendiente.
    assert approvals.get(a["id"])["status"] == "pending"


def test_approve_records_human(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    decided = approvals.decide(a["id"], approve=True, approved_by="leandro")
    assert decided["status"] == "approved"
    assert decided["approved_by"] == "leandro"
    assert decided["requested_by"] == "agente"


def test_reject_closes_request(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    rejected = approvals.decide(a["id"], approve=False, approved_by="leandro", reason="no ahora")
    assert rejected["status"] == "rejected"
    assert rejected["approved_by"] is None
    with pytest.raises(RuntimeError):
        approvals.claim(a["id"])


# ---------------------------------------------------------------------------
# Un solo uso y consumo atomico
# ---------------------------------------------------------------------------


def test_claim_is_single_use(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    claimed = approvals.claim(a["id"])
    assert claimed["status"] == "executing"
    with pytest.raises(RuntimeError) as exc:
        approvals.claim(a["id"])
    assert "en ejecucion" in str(exc.value)


def test_consumed_cannot_be_claimed_again(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    approvals.claim(a["id"])
    approvals.finish(a["id"], executed=True, exit_code=0, duration_ms=12)
    assert approvals.get(a["id"])["status"] == "consumed"
    with pytest.raises(RuntimeError) as exc:
        approvals.claim(a["id"])
    assert "consumida" in str(exc.value)


def test_concurrent_claim_has_exactly_one_winner(registry, roots, exec_settings):
    """Carrera: varias peticiones simultaneas -> una sola gana."""
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")

    async def race():
        return await asyncio.gather(
            *[asyncio.to_thread(approvals.claim, a["id"]) for _ in range(6)],
            return_exceptions=True,
        )

    results = asyncio.run(race())
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, Exception)]
    assert len(winners) == 1, f"esperaba 1 ganador, hubo {len(winners)}"
    assert len(losers) == 5, f"esperaba 5 perdedores, hubo {len(losers)}"
    for loser in losers:
        assert isinstance(loser, RuntimeError), f"excepcion inesperada: {loser!r}"
    # Y el estado final es 'executing': solo una ejecucion en curso.
    assert approvals.get(a["id"])["status"] == "executing"


def test_finish_requires_executing_state(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    with pytest.raises(RuntimeError):
        approvals.finish(a["id"], executed=True)


# ---------------------------------------------------------------------------
# Expiracion
# ---------------------------------------------------------------------------


def test_expired_request_cannot_be_approved(registry, roots, exec_settings, monkeypatch):
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVAL_TTL_SECONDS", "1")
    get_settings.cache_clear()
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    import time

    time.sleep(1.2)
    with pytest.raises(RuntimeError) as exc:
        approvals.decide(a["id"], approve=True, approved_by="leandro")
    assert "expired" in str(exc.value) or "expirado" in str(exc.value)
    assert approvals.get(a["id"])["status"] == "expired"


def test_expired_approval_cannot_be_claimed(registry, roots, exec_settings, monkeypatch):
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVAL_TTL_SECONDS", "1")
    get_settings.cache_clear()
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    import time

    time.sleep(1.2)
    with pytest.raises(RuntimeError) as exc:
        approvals.claim(a["id"])
    assert "expired" in str(exc.value) or "expirado" in str(exc.value)
    assert approvals.get(a["id"])["status"] == "expired"


# ---------------------------------------------------------------------------
# Vinculo exacto: hash, argumentos, host y catalogo
# ---------------------------------------------------------------------------


def test_verify_detects_changed_normalized_arguments(registry, roots, exec_settings):
    """Si el argv normalizado cambio tras aprobar, la aprobacion caduca."""
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")

    # Se manipula la fila (simula un cambio de entorno o una manipulacion).
    conn = sqlite3.connect(get_settings().audit_db)
    conn.execute(
        "UPDATE approvals SET argv_json=? WHERE id=?",
        (json.dumps(["echo", "EJECUTADO", "/otro/lugar"]), a["id"]),
    )
    conn.commit()
    conn.close()

    stored = approvals.get(a["id"])
    fresh = check_sync(registry, "needs_approval", {"path": str(roots)})
    reason = approvals.verify_against_registry(stored, fresh)
    assert reason is not None
    assert "cambiaron" in reason or "alterada" in reason


def test_verify_detects_disabled_host(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    registry.hosts["testhost"] = Host(
        id="testhost", label="Test", transport="local", enabled=False,
        roots=(str(roots),),
    )
    fresh = check_sync(registry, "needs_approval", {"path": str(roots)})
    reason = approvals.verify_against_registry(approvals.get(a["id"]), fresh)
    assert reason is not None
    assert "politica" in reason


def test_verify_detects_catalog_category_change(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    registry.operations["needs_approval"] = Operation(
        id="needs_approval", title="ahora auto", command=("echo", "x", "{path}"),
        auto=True, path_param="path",
    )
    fresh = check_sync(registry, "needs_approval", {"path": str(roots)})
    reason = approvals.verify_against_registry(approvals.get(a["id"]), fresh)
    assert reason is not None
    assert "categoria" in reason


def test_hash_covers_params_and_argv():
    h1 = approvals.request_hash("h", "op", {"path": "/a"})
    h2 = approvals.request_hash("h", "op", {"path": "/b"})
    assert h1 != h2
    d1 = approvals.decision_hash(["ls", "/a"], "/a", 30)
    d2 = approvals.decision_hash(["ls", "/b"], "/a", 30)
    assert d1 != d2


# ---------------------------------------------------------------------------
# Reinicio e resultados inciertos
# ---------------------------------------------------------------------------


def test_pending_survives_restart(registry, roots, exec_settings):
    """Una solicitud pendiente no se pierde al reiniciar el agente."""
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    # Simula el reinicio: se relee desde el disco con conexiones nuevas.
    get_settings.cache_clear()
    recovered = approvals.get(a["id"])
    assert recovered is not None
    assert recovered["status"] == "pending"
    assert recovered["argv"] == d.argv


def test_interrupted_execution_is_failed_and_never_retried(registry, roots, exec_settings):
    """Reinicio en mitad de una ejecucion: resultado incierto, sin reintento."""
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    approvals.claim(a["id"])

    # Se simula el paso del tiempo (el proceso murio antes de cerrar).
    conn = sqlite3.connect(get_settings().audit_db)
    conn.execute(
        "UPDATE approvals SET executed_at='2020-01-01T00:00:00+00:00' WHERE id=?",
        (a["id"],),
    )
    conn.commit()
    conn.close()

    recovered = approvals.get(a["id"])
    assert recovered["status"] == "failed"
    assert "incierto" in (recovered["result_error"] or "")
    with pytest.raises(RuntimeError):
        approvals.claim(a["id"])


def test_mark_failed_invalidates_approved_request(registry, roots, exec_settings):
    d = check_sync(registry, "needs_approval", {"path": str(roots)})
    a = approvals.ensure_pending(
        requested_by="agente", host="testhost", operation="needs_approval",
        params={"path": str(roots)}, decision=d,
    )
    approvals.decide(a["id"], approve=True, approved_by="leandro")
    approvals.mark_failed(a["id"], "el registro cambio")
    assert approvals.get(a["id"])["status"] == "failed"
    with pytest.raises(RuntimeError):
        approvals.claim(a["id"])
