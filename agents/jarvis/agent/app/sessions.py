"""CRUD de sesiones de navegador en SQLite."""
import datetime as dt

from .db import session

_FMT = "%Y-%m-%dT%H:%M:%S+00:00"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime(_FMT)


def create(fingerprint: str, ttl_days: int) -> None:
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


def purge_expired() -> int:
    now = _iso(_now())
    with session() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return cursor.rowcount
