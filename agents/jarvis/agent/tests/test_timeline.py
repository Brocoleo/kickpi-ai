"""Pruebas del historial "para pintar" (`get_timeline`).

Motivo: la PWA reconstruía el historial con `get_messages` (formato LLM), que
no trae `id` ni `created_at` y descarta las herramientas. Consecuencias reales:
todas las burbujas recibían la misma clave de React (`h-undefined`) y al reabrir
una conversación desaparecía toda la actividad de herramientas.

Se cubre:
- `get_timeline` devuelve id, fecha y metadatos de herramientas,
- `_persist_turn_sync` guarda ok / duración / resumen de cada herramienta,
- `/api/conversations/{id}` responde con ese formato,
- la migración añade las columnas a una base de datos ya existente sin
  perder filas.
"""
from __future__ import annotations

import sqlite3

from app import main, memory
from app.config import get_settings
from conftest import API_TOKEN


def test_timeline_incluye_id_fecha_y_herramientas(client):
    conv = memory.create_conversation("prueba")
    memory.add_message(conv, "user", "¿cómo está el sistema?")
    memory.add_message(
        conv,
        "assistant",
        None,
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "system_stats", "arguments": "{}"},
            }
        ],
    )
    memory.add_message(
        conv,
        "tool",
        '{"cpu": 5}',
        tool_call_id="call_1",
        name="system_stats",
        tool_ok=True,
        tool_duration_ms=42,
        tool_summary="CPU 5%",
    )
    memory.add_message(conv, "assistant", "Todo tranquilo.")

    filas = memory.get_timeline(conv)
    assert [f["role"] for f in filas] == ["user", "assistant", "tool", "assistant"]

    # id y fecha: lo que faltaba y rompía las claves de React.
    assert all(isinstance(f["id"], int) for f in filas)
    assert all(f["created_at"] for f in filas)
    assert len({f["id"] for f in filas}) == len(filas)

    # tool_calls se devuelve ya parseado, no como texto.
    assert filas[1]["tool_calls"][0]["function"]["name"] == "system_stats"

    herramienta = filas[2]
    assert herramienta["tool_ok"] is True
    assert herramienta["tool_duration_ms"] == 42
    assert herramienta["tool_summary"] == "CPU 5%"


def test_timeline_no_inventa_metadatos(client):
    conv = memory.create_conversation("sin meta")
    memory.add_message(conv, "user", "hola")

    fila = memory.get_timeline(conv)[0]
    assert "tool_ok" not in fila
    assert "tool_duration_ms" not in fila
    assert "tool_summary" not in fila


def test_endpoint_devuelve_timeline(client):
    conv = memory.create_conversation("endpoint")
    memory.add_message(conv, "user", "hola")
    memory.add_message(conv, "assistant", "buenas")

    resp = client.get(
        f"/api/conversations/{conv}", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["conversation_id"] == conv
    primero = cuerpo["messages"][0]
    assert primero["id"] >= 1
    assert primero["created_at"]


def test_persistencia_guarda_metadatos_de_herramienta(client):
    conv = memory.create_conversation("turno")
    base = [{"role": "system", "content": "eres jarvis"}, {"role": "user", "content": "x"}]
    result = {
        "messages": base
        + [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "docker_ps", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_9",
                "name": "docker_ps",
                "content": '{"containers": []}',
            },
            {"role": "assistant", "content": "No hay contenedores."},
        ],
        "tool_trace": [
            {
                "tool": "docker_ps",
                "arguments": {},
                "ok": False,
                "duration_ms": 321,
                "summary": "fallo al listar",
                "tool_call_id": "call_9",
            }
        ],
    }

    texto = main._persist_turn_sync(conv, len(base), result)
    assert texto == "No hay contenedores."

    herramienta = [f for f in memory.get_timeline(conv) if f["role"] == "tool"][0]
    assert herramienta["tool_ok"] is False
    assert herramienta["tool_duration_ms"] == 321
    assert herramienta["tool_summary"] == "fallo al listar"


def test_migracion_anade_columnas_sin_perder_datos(tmp_path, monkeypatch):
    """Simula una base creada antes de estos metadatos."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ruta = data_dir / "jarvis.db"

    antigua = sqlite3.connect(ruta)
    antigua.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_call_id TEXT,
            name TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO messages (conversation_id, role, content, created_at)
        VALUES ('c1', 'user', 'mensaje antiguo', '2026-10-01T00:00:00+00:00');
        """
    )
    antigua.commit()
    antigua.close()

    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    try:
        from app.db import init_db

        init_db()
        init_db()  # idempotente: no debe fallar ni duplicar columnas

        conexion = sqlite3.connect(ruta)
        columnas = {f[1] for f in conexion.execute("PRAGMA table_info(messages)")}
        filas = conexion.execute("SELECT role, content FROM messages").fetchall()
        conexion.close()
    finally:
        get_settings.cache_clear()

    assert {"tool_ok", "tool_duration_ms", "tool_summary"} <= columnas
    assert filas == [("user", "mensaje antiguo")]


def test_borrar_conversacion_se_lleva_sus_mensajes(browser):
    conv = memory.create_conversation("borrar")
    memory.add_message(conv, "user", "hola")
    memory.add_message(conv, "assistant", "buenas")
    assert len(memory.get_timeline(conv)) == 2

    resp = browser.delete(f"/api/conversations/{conv}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert memory.get_timeline(conv) == []
    assert browser.get(f"/api/conversations/{conv}").status_code == 404


def test_borrar_conversacion_inexistente_es_404(browser):
    assert browser.delete("/api/conversations/no-existe").status_code == 404


def test_borrar_exige_sesion_humana(client):
    """El token de API no basta: borrar es destructivo."""
    conv = memory.create_conversation("protegida")
    resp = client.delete(
        f"/api/conversations/{conv}", headers={"X-API-Token": API_TOKEN}
    )
    assert resp.status_code in (401, 403)
    assert memory.conversation_exists(conv)
