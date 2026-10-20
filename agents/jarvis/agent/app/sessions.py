"""CRUD de sesiones de navegador en SQLite."""
import datetime as dt

from .db import session

_FMT = "%Y-%m-%dT%H:%M:%S+00:00"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime(_FMT)


def create(fingerprint: str, ttl_days: int, subject: str = "admin") -> None:
    now = _now()
    expires = now + dt.timedelta(days=ttl_days)
    with session() as conn:
        # Higiene: purgamos las caducadas en cada login.
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))
        conn.execute(
            "INSERT OR REPLACE INTO sessions(id, created_at, expires_at, last_seen)"
            " VALUES (?, ?, ?, ?)",
            (fingerprint, _iso(now), _iso(expires), _iso(now)),
        )
        # Identidad humana de la sesion: es lo que queda como `approved_by`
        # en una aprobacion. La deriva el servidor, nunca el navegador.
        conn.execute(
            "INSERT OR REPLACE INTO session_subjects(fingerprint, subject, created_at)"
            " VALUES (?, ?, ?)",
            (fingerprint, subject or "admin", _iso(now)),
        )


def subject(fingerprint: str) -> str | None:
    """Identidad humana de la sesion (None si no hay)."""
    with session() as conn:
        row = conn.execute(
            "SELECT subject FROM session_subjects WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
    return row["subject"] if row else None


def valid(fingerprint: str) -> bool:
    now = _iso(_now())
    with session() as conn:
        row = conn.execute(
            "SELECT expires_at FROM sessions WHERE id = ?", (fingerprint,)
        ).fetchone()
        if not row:
            return False
        if row["expires_at"] <= now:
            conn.execute("DELETE FROM sessions WHERE id = ?", (fingerprint,))
            return False
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?", (now, fingerprint)
        )
        return True


def delete(fingerprint: str) -> None:
    with session() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (fingerprint,))
        conn.execute("DELETE FROM session_subjects WHERE fingerprint = ?", (fingerprint,))


def purge_expired() -> int:
    now = _iso(_now())
    with session() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return cursor.rowcount
