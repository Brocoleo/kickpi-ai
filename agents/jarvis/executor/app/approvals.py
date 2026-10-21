"""Solicitudes de aprobacion humana (Fase C).

Las aprobaciones viven en el EJECUTOR, que es el punto de coercion: solo
alli existe la Decision con el argv normalizado, y solo alli puede
comprobarse que "lo aprobado" y "lo ejecutado" son exactamente lo mismo.

Propiedades por diseno:

- **Deny by default**: solo se crea una solicitud para una operacion DEL
  CATALOGO que el Policy Engine valida por completo (con `auto: false`).
  Una operacion desconocida o prohibida nunca crea una aprobacion, y una
  aprobacion nunca convierte una operacion prohibida en permitida.
- **Vinculo exacto**: se persiste el argv NORMALIZADO (ruta resuelta en el
  host, `env -C`, limites acotados) y su hash. Al ejecutar se re-evalua la
  politica con los parametros ALMACENADOS y se compara el hash: si el
  registro, las raices o los parametros cambiaron, la aprobacion se
  invalida (estado `failed`); nunca se ejecuta otra cosa.
- **Un solo uso**: reclamar es un UPDATE atomico condicionado al estado
  `approved` y a la expiracion. En concurrencia, exactamente una llamada
  gana; las demas reciben 409.
- **Como maximo una ejecucion**: reclamar introduce el estado intermedio
  `executing`. Si el proceso muere tras reclamar, el barrido marca la
  solicitud `failed` con motivo "resultado incierto" y NO se reintenta.
  Un error SSH tampoco se reintenta: queda `failed`.
- **Separacion de identidades**: `requested_by` es el AGENTE (quien pide),
  `approved_by` es el HUMANO autenticado (quien decide). El LLM no puede
  escribir ninguno de los dos.

El LLM no alcanza estos endpoints: exigen un token dedicado
(`JARVIS_EXECUTOR_APPROVALS_TOKEN`) que el agente nunca inyecta en el
flujo de herramientas. Ni siquiera se acepta un `approval_id` en `/exec`:
la ejecucion aprobada se inicia por el identificador de la ruta
(`/approvals/{id}/execute`) y se consume SIN cuerpo, de modo que todo lo
que se ejecuta procede de lo persistido y verificado.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import get_settings

#: Estados posibles de una solicitud.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
EXECUTING = "executing"
CONSUMED = "consumed"
FAILED = "failed"

STATUSES = (PENDING, APPROVED, REJECTED, EXPIRED, EXECUTING, CONSUMED, FAILED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    requested_by     TEXT NOT NULL,          -- el AGENTE que la solicito
    approved_by      TEXT,                   -- el HUMANO que la aprobo
    host             TEXT NOT NULL,
    project_id       TEXT,
    operation        TEXT NOT NULL,
    params_json      TEXT NOT NULL,          -- parametros normalizados
    argv_json        TEXT NOT NULL,          -- argv NORMALIZADO por la politica
    resolved_path    TEXT,
    timeout_s        INTEGER,
    request_hash     TEXT NOT NULL,          -- hash(host|operacion|params)
    decision_hash    TEXT NOT NULL,          -- hash(argv|resolved_path|timeout)
    risk             TEXT,
    risk_description TEXT,
    expires_at       TEXT NOT NULL,
    status           TEXT NOT NULL,
    decided_at       TEXT,
    executed_at      TEXT,
    exit_code        INTEGER,
    duration_ms      INTEGER,
    result_error     TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_approvals_request ON approvals(request_hash);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def canonical(params: dict) -> str:
    """Serializacion estable de los parametros."""
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(host: str, operation: str, params: dict) -> str:
    """Huella de la peticion: host + operacion + parametros."""
    payload = json.dumps(
        {"host": host, "operation": operation, "params": params or {}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decision_hash(argv: list[str] | None, resolved_path: str | None, timeout: int | None) -> str:
    """Huella de la decision ejecutable: argv normalizado + ruta + timeout."""
    payload = json.dumps(
        {"argv": argv or [], "resolved_path": resolved_path, "timeout": timeout},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    path = get_settings().audit_db
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _maintenance(conn: sqlite3.Connection) -> None:
    """Barrido perezoso: expirar y recuperar ejecuciones interrumpidas.

    Nunca reintenta: lo que estaba en `executing` y no tiene resultado se
    marca `failed` como "resultado incierto" (puede haberse enviado por el
    cable). Regla de "como maximo una ejecucion".
    """
    now = _iso(_now())
    conn.execute(
        "UPDATE approvals SET status='expired', decided_at=? "
        "WHERE status IN ('pending','approved') AND expires_at <= ?",
        (now, now),
    )
    stale_limit = _iso(_now() - timedelta(seconds=get_settings().stale_executing_seconds))
    conn.execute(
        "UPDATE approvals SET status='failed', result_error=? "
        "WHERE status='executing' AND executed_at <= ?",
        (
            "ejecucion interrumpida (reinicio o corte); resultado incierto, no se reintenta",
            stale_limit,
        ),
    )


def _touch(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _maintenance(conn)


def _to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "requested_by": row["requested_by"],
        "approved_by": row["approved_by"],
        "host": row["host"],
        "project_id": row["project_id"],
        "operation": row["operation"],
        "params": json.loads(row["params_json"] or "{}"),
        "argv": json.loads(row["argv_json"] or "[]"),
        "resolved_path": row["resolved_path"],
        "timeout_s": row["timeout_s"],
        "request_hash": row["request_hash"],
        "decision_hash": row["decision_hash"],
        "risk": row["risk"],
        "risk_description": row["risk_description"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "decided_at": row["decided_at"],
        "executed_at": row["executed_at"],
        "exit_code": row["exit_code"],
        "duration_ms": row["duration_ms"],
        "result_error": row["result_error"],
    }


def public(approval: dict) -> dict:
    """Vista que puede recibir la PWA (no filtra nada sensible: el argv es
    justamente lo que el humano debe ver antes de aprobar)."""
    return dict(approval)


def ensure_pending(
    requested_by: str,
    host: str,
    operation: str,
    params: dict,
    decision: Any,
    risk: str = "",
    risk_description: str = "",
) -> dict:
    """Crea (o devuelve) la solicitud pendiente de una peticion ya validada.

    Idempotente por `request_hash`: repetir la misma peticion desde el chat
    no genera solicitudes duplicadas (anti-flood del LLM). Los datos
    sensibles (host, operacion, params, argv) los aporta la DECISION de la
    politica, no el cuerpo libre de la peticion.
    """
    settings = get_settings()
    r_hash = request_hash(host, operation, params)
    d_hash = decision_hash(decision.argv, decision.resolved_path, decision.timeout)

    conn = _connect()
    try:
        _touch(conn)
        existing = conn.execute(
            "SELECT * FROM approvals WHERE request_hash=? AND status IN ('pending','approved')",
            (r_hash,),
        ).fetchone()
        if existing is not None:
            return _to_dict(existing)

        pending_count = conn.execute(
            "SELECT COUNT(*) AS n FROM approvals WHERE host=? AND status='pending'",
            (host,),
        ).fetchone()["n"]
        if pending_count >= settings.max_pending_per_host:
            raise RuntimeError(
                f"demasiadas solicitudes pendientes para {host} "
                f"(maximo {settings.max_pending_per_host})"
            )

        approval_id = secrets.token_urlsafe(16)
        created = _now()
        expires = created + timedelta(seconds=settings.approval_ttl_seconds)
        conn.execute(
            "INSERT INTO approvals(id, created_at, requested_by, host, project_id, "
            "operation, params_json, argv_json, resolved_path, timeout_s, "
            "request_hash, decision_hash, risk, risk_description, expires_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                _iso(created),
                requested_by or "agente",
                host,
                str((params or {}).get("project_id") or "") or None,
                operation,
                canonical(params),
                json.dumps(decision.argv or [], ensure_ascii=False),
                decision.resolved_path,
                decision.timeout,
                r_hash,
                d_hash,
                risk,
                risk_description,
                _iso(expires),
                PENDING,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


def get(approval_id: str) -> dict | None:
    conn = _connect()
    try:
        _touch(conn)
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row) if row else None
    finally:
        conn.close()


def list_approvals(
    status: str | None = None, host: str | None = None, limit: int = 50
) -> list[dict]:
    conn = _connect()
    try:
        _touch(conn)
        sql = "SELECT * FROM approvals"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status=?")
            args.append(status)
        if host:
            clauses.append("host=?")
            args.append(host)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))
        return [_to_dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def decide(approval_id: str, approve: bool, approved_by: str, reason: str = "") -> dict:
    """Aprueba o rechaza una solicitud PENDIENTE.

    `approved_by` es el HUMANO autenticado y es obligatorio para aprobar:
    el agente solo puede haber rellenado `requested_by`.
    """
    if approve and not (approved_by or "").strip():
        raise ValueError("falta approved_by (identidad humana requerida)")

    now = _now()
    conn = _connect()
    try:
        _touch(conn)
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        current = _to_dict(row)
        if current["status"] != PENDING:
            raise RuntimeError(
                f"la solicitud ya no esta pendiente (estado: {current['status']})"
            )

        # Re-comprobacion de expiracion en el mismo instante de la decision.
        if _now() > datetime.fromisoformat(current["expires_at"]):
            conn.execute(
                "UPDATE approvals SET status='expired', decided_at=? "
                "WHERE id=? AND status='pending'",
                (_iso(now), approval_id),
            )
            conn.commit()
            raise RuntimeError("la solicitud ha expirado")

        if approve:
            new_status, actor = APPROVED, approved_by.strip()
        else:
            new_status, actor = REJECTED, None
        cursor = conn.execute(
            "UPDATE approvals SET status=?, approved_by=?, decided_at=?, result_error=? "
            "WHERE id=? AND status='pending'",
            (new_status, actor, _iso(now), (reason or None) if not approve else None, approval_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("carrera al decidir: la solicitud cambio de estado")
        conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


def mark_failed(approval_id: str, reason: str) -> dict:
    """Invalida una aprobacion que ya no se corresponde con la realidad."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE approvals SET status='failed', result_error=? "
            "WHERE id=? AND status IN ('pending','approved')",
            (reason, approval_id),
        )
        if cursor.rowcount != 1:
            row = conn.execute(
                "SELECT status FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
        conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


def claim(approval_id: str) -> dict:
    """Reclama ATOMICAMENTE una aprobacion para ejecutarla (un solo uso).

    Comprobaciones previas y despues un UPDATE condicionado al estado y a la
    expiracion: en concurrencia, exactamente un llamador recibe la fila.
    """
    now = _now()
    conn = _connect()
    try:
        _touch(conn)
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        current = _to_dict(row)

        if current["status"] == CONSUMED:
            raise RuntimeError("la aprobacion ya fue consumida (un solo uso)")
        if current["status"] == EXECUTING:
            raise RuntimeError("la aprobacion esta en ejecucion (otro consumidor la reclamo)")
        if current["status"] == FAILED:
            raise RuntimeError(f"la aprobacion fallo: {current['result_error']}")
        if current["status"] != APPROVED:
            raise RuntimeError(f"estado no ejecutable: {current['status']}")
        if datetime.fromisoformat(current["expires_at"]) <= now:
            conn.execute(
                "UPDATE approvals SET status='expired', decided_at=? "
                "WHERE id=? AND status='approved'",
                (_iso(now), approval_id),
            )
            conn.commit()
            raise RuntimeError("la aprobacion ha expirado")

        try:
            claimed = conn.execute(
                "UPDATE approvals SET status='executing', executed_at=? "
                "WHERE id=? AND status='approved' AND expires_at > ?",
                (_iso(now), approval_id, _iso(now)),
            ).rowcount
        except sqlite3.OperationalError as exc:
            # Base de datos ocupada: NO se ha reclamado nada, el llamador
            # puede reintentar mas tarde. Nunca se ejecuta dos veces.
            raise RuntimeError(f"no se pudo reclamar la aprobacion: {exc}") from exc
        if claimed != 1:
            raise RuntimeError("carrera: la aprobacion cambio de estado (claim atomico FAILED)")
        conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row)
    finally:
        conn.close()


def finish(
    approval_id: str,
    executed: bool,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Cierra una ejecucion reclamada.

    `consumed` = se ejecuto (un exit != 0 es un resultado legitimo).
    `failed` = no se pudo comunicar o el resultado es incierto. En ambos
    casos la aprobacion queda GASTADA: nunca vuelve a `approved`.

    Si se pasa `conn`, la actualización se hace dentro de la transacción del
    llamador (sin commit propio): así el cierre de la aprobación y su fila de
    auditoría son atómicos.
    """
    own = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        if row["status"] != EXECUTING:
            raise RuntimeError(f"estado inesperado para cerrar: {row['status']}")
        new_status = CONSUMED if executed else FAILED
        conn.execute(
            "UPDATE approvals SET status=?, exit_code=?, duration_ms=?, result_error=? "
            "WHERE id=? AND status='executing'",
            (new_status, exit_code, duration_ms, error, approval_id),
        )
        if own:
            conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _to_dict(row)
    finally:
        if own:
            conn.close()


def verify_against_registry(approval: dict, decision: Any) -> str | None:
    """Comprueba que la decision RE-EVALUADA sigue siendo la aprobada.

    Re-ejecuta la politica con los parametros almacenados y compara la
    huella del argv normalizado. Devuelve None si coincide; en otro caso, el
    motivo del desajuste. Protege frente a: cambio del catalogo, cambio de
    raices o de proyectos, host deshabilitado y manipulacion de la fila.
    """
    if decision is None:
        return "no se pudo reevaluar la politica"
    if not getattr(decision, "requires_approval", False):
        # O bien la operacion ha pasado a ser auto-aprobable (cambio de
        # categoria), o bien ya no se permite (host deshabilitado, ruta
        # fuera de las raices, host desconocido...). En ambos casos la
        # aprobacion deja de ser valida.
        if decision.allowed:
            return "la operacion cambio de categoria en el catalogo"
        return "la politica ya no permite esta operacion"
    # Integridad de la FILA: el argv persistido debe seguir correspondiendo
    # a su propia huella (detecta manipulacion de la tabla).
    stored = decision_hash(
        approval["argv"], approval["resolved_path"], approval["timeout_s"]
    )
    if stored != approval["decision_hash"]:
        return "la fila de aprobacion fue alterada (argv modificado)"
    # Integridad del ENTORNO: la decision re-evaluada debe ser identica.
    recomputed = decision_hash(decision.argv, decision.resolved_path, decision.timeout)
    if recomputed != approval["decision_hash"]:
        return "los argumentos normalizados cambiaron desde la aprobacion"
    recomputed_request = request_hash(
        approval["host"], approval["operation"], approval["params"]
    )
    if recomputed_request != approval["request_hash"]:
        return "la fila de aprobacion fue alterada (hash de peticion)"
    return None
