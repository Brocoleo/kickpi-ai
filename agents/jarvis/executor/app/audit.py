"""Auditoria de ejecuciones en SQLite.

Vive en /mnt/data (el disco externo) para que sobreviva a reinicios del
contenedor y no dependa de la eMMC.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    host_id       TEXT NOT NULL,
    operation     TEXT NOT NULL,
    params_json   TEXT,
    allowed       INTEGER NOT NULL,
    ok            INTEGER NOT NULL,
    exit_code     INTEGER,
    duration_ms   INTEGER,
    output_excerpt TEXT,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_exec_created ON executions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exec_host ON executions(host_id, created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    path = get_settings().audit_db
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _cursor():
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record(
    host_id: str,
    operation: str,
    params: dict,
    allowed: bool,
    ok: bool,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    output: str = "",
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    excerpt = output[:4000]
    with _cursor() as conn:
        conn.execute(
            "INSERT INTO executions(created_at, host_id, operation, params_json, "
            "allowed, ok, exit_code, duration_ms, output_excerpt, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                now,
                host_id,
                operation,
                json.dumps(params, ensure_ascii=False)[:1000],
                1 if allowed else 0,
                1 if ok else 0,
                exit_code,
                duration_ms,
                excerpt,
                error,
            ),
        )


def recent(limit: int = 50) -> list[dict]:
    with _cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM executions ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
        ).fetchall()
    return [dict(r) for r in rows]


def by_host(host_id: str, limit: int = 20) -> list[dict]:
    with _cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM executions WHERE host_id = ? ORDER BY id DESC LIMIT ?",
            (host_id, max(1, min(limit, 100))),
        ).fetchall()
    return [dict(r) for r in rows]
