"""Conexión y esquema de SQLite (memoria + auditoría)."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT,
    tool_calls      TEXT,
    tool_call_id    TEXT,
    name            TEXT,
    -- Metadatos de la llamada a herramienta, para poder reconstruir el
    -- historial con el mismo detalle que el streaming en vivo (el JSON de
    -- `content` es el resultado crudo: no incluye duración ni resumen).
    tool_ok         INTEGER,
    tool_duration_ms INTEGER,
    tool_summary    TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    conversation_id TEXT,
    tool            TEXT NOT NULL,
    arguments       TEXT,
    ok              INTEGER NOT NULL,
    duration_ms     INTEGER,
    summary         TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(id DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,   -- SHA-256 del token de sesión
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Sujeto (usuario humano) de cada sesion. Tabla aparte para NO alterar la
-- tabla `sessions` ya existente en produccion: solo se anade una tabla
-- nueva, que es una migracion aditiva y sin perdida de datos.
CREATE TABLE IF NOT EXISTS session_subjects (
    fingerprint  TEXT PRIMARY KEY,   -- SHA-256 del token de sesion
    subject      TEXT NOT NULL,      -- identidad humana (p.ej. "leandro")
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_subjects_created ON session_subjects(created_at);

-- Vinculo entre una aprobacion pendiente y el turno de conversacion que la
-- origino. Permite reanudar el grafo cuando el humano ejecuta o rechaza,
-- para que el chat reciba la respuesta final (y no se quede a medias).
CREATE TABLE IF NOT EXISTS approval_context (
    approval_id     TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool_call_id    TEXT,
    created_at      TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path(get_settings().data_dir) / "jarvis.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session():
    """Conexión con transacción y cierre garantizado."""
    conn = connect()
    try:
        with conn:  # hace commit al salir, rollback si hay excepción
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        _migrar(conn)


# Columnas añadidas después de la primera versión. `CREATE TABLE IF NOT
# EXISTS` no toca una tabla ya creada, así que se añaden explícitamente.
# Es aditivo e idempotente: nunca borra ni reescribe datos.
MIGRACIONES: tuple[tuple[str, str, str], ...] = (
    ("messages", "tool_ok", "INTEGER"),
    ("messages", "tool_duration_ms", "INTEGER"),
    ("messages", "tool_summary", "TEXT"),
)


def _migrar(conn: sqlite3.Connection) -> None:
    for tabla, columna, tipo in MIGRACIONES:
        existentes = {
            fila["name"] for fila in conn.execute(f"PRAGMA table_info({tabla})")
        }
        if columna not in existentes:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
