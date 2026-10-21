"""Memoria persistente (conversaciones, mensajes) y auditoría de herramientas.

Todas las funciones son síncronas y baratas; se invocan desde los handlers
async mediante `asyncio.to_thread` para no bloquear el event loop.
"""
import json
import uuid
from datetime import datetime, timezone

from .db import session


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Conversaciones
# --------------------------------------------------------------------------- #
def create_conversation(title: str = "") -> str:
    conv_id = uuid.uuid4().hex
    now = _now()
    with session() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (conv_id, title[:200], now, now),
        )
    return conv_id


def conversation_exists(conv_id: str) -> bool:
    with session() as conn:
        row = conn.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return row is not None


def touch_conversation(conv_id: str) -> None:
    with session() as conn:
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))


def list_conversations(limit: int = 50) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Mensajes
# --------------------------------------------------------------------------- #
def add_message(
    conv_id: str,
    role: str,
    content: str | None = None,
    tool_calls: list | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, tool_calls, tool_call_id, name, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                conv_id,
                role,
                content,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                tool_call_id,
                name,
                _now(),
            ),
        )


def get_messages(conv_id: str, limit: int | None = None) -> list[dict]:
    """Devuelve los mensajes en formato OpenAI, listos para el LLM."""
    query = (
        "SELECT role, content, tool_calls, tool_call_id, name FROM messages "
        "WHERE conversation_id=? ORDER BY id"
    )
    with session() as conn:
        rows = conn.execute(query, (conv_id,)).fetchall()

    messages: list[dict] = []
    for row in rows:
        msg: dict = {"role": row["role"]}
        if row["content"] is not None:
            msg["content"] = row["content"]
        if row["tool_calls"]:
            msg["tool_calls"] = json.loads(row["tool_calls"])
        if row["tool_call_id"]:
            msg["tool_call_id"] = row["tool_call_id"]
        if row["name"]:
            msg["name"] = row["name"]
        messages.append(msg)

    if limit and limit > 0:
        # Recorta por el principio, pero sin dejar un 'tool' huérfano de su
        # mensaje assistant que lo invocó.
        trimmed = messages[-limit:]
        while trimmed and trimmed[0]["role"] == "tool":
            trimmed = trimmed[1:]
        return trimmed
    return messages


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #
def add_audit(
    tool: str,
    arguments: dict | None = None,
    ok: bool = True,
    duration_ms: int | None = None,
    summary: str = "",
    conversation_id: str | None = None,
) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO audit "
            "(created_at, conversation_id, tool, arguments, ok, duration_ms, summary) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                _now(),
                conversation_id,
                tool,
                json.dumps(arguments or {}, ensure_ascii=False),
                1 if ok else 0,
                duration_ms,
                summary[:500],
            ),
        )


def list_audit(limit: int = 50) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT created_at, conversation_id, tool, arguments, ok, duration_ms, summary "
            "FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["arguments"] = json.loads(d["arguments"]) if d["arguments"] else {}
        d["ok"] = bool(d["ok"])
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Aprobaciones: vinculo con el turno de conversacion
# --------------------------------------------------------------------------- #
def link_approval(
    approval_id: str, conversation_id: str, tool_call_id: str | None = None
) -> None:
    """Guarda que turno/conversacion origino una aprobacion pendiente.

    Si la misma aprobacion se vuelve a enlazar (p.ej. el ejecutor deduplica
    una peticion identica), gana el contexto mas reciente.
    """
    with session() as conn:
        conn.execute(
            "INSERT INTO approval_context "
            "(approval_id, conversation_id, tool_call_id, created_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(approval_id) DO UPDATE SET "
            "conversation_id=excluded.conversation_id, "
            "tool_call_id=excluded.tool_call_id, "
            "created_at=excluded.created_at",
            (approval_id, conversation_id, tool_call_id, _now()),
        )


def get_approval_context(approval_id: str) -> dict | None:
    with session() as conn:
        row = conn.execute(
            "SELECT approval_id, conversation_id, tool_call_id, created_at "
            "FROM approval_context WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
    return dict(row) if row else None
