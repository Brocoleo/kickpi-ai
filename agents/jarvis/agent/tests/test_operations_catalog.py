"""Pruebas de C3 en el agente: el mapa cerrado de operaciones y el camino
de la solicitud de aprobacion hasta el chat.

Verifica que:
- el LLM ve un catalogo CERRADO (una operacion por id, sin via generica);
- `host_git_diff_stat` aparece en ese mapa como operacion que exige
  aprobacion humana;
- cuando el ejecutor devuelve `pending_approval`, el agente lleva el
  identificador de la solicitud y la explicacion al chat (el LLM puede
  informar al usuario, pero no puede decidir);
- el LLM no puede aprobar, rechazar ni ejecutar solicitudes.
"""
from __future__ import annotations

import json

from app.executor_client import OPERATIONS_CATALOGO, TOOL_SPECS, operations_text


def _tool_spec(name: str) -> dict:
    for spec in TOOL_SPECS:
        if spec["function"]["name"] == name:
            return spec
    raise AssertionError(f"herramienta ausente: {name}")


# --------------------------------------------------------------------------- #
# Mapa cerrado de operaciones
# --------------------------------------------------------------------------- #
def test_el_mapa_cerrado_incluye_la_operacion_con_aprobacion():
    assert "host_git_diff_stat" in OPERATIONS_CATALOGO
    assert "aprobacion humana" in OPERATIONS_CATALOGO["host_git_diff_stat"]
    assert "path" in OPERATIONS_CATALOGO["host_git_diff_stat"]


def test_la_descripcion_de_la_tool_se_genera_del_mapa():
    """La prosa no puede desincronizarse del mapa cerrado."""
    spec = _tool_spec("equipos_ejecutar")
    description = spec["function"]["description"]
    for op in OPERATIONS_CATALOGO:
        assert op in description, op
    assert operations_text() in description
    # Y no aparecen operaciones inventadas.
    assert "host_git_pull" not in description
    assert "host_git_commit" not in description


def test_el_mapa_no_abre_una_via_generica():
    """La tool solo acepta un id de operacion: nunca un comando."""
    spec = _tool_spec("equipos_ejecutar")
    props = spec["function"]["parameters"]["properties"]
    assert set(props) == {"host", "operation", "params"}
    assert spec["function"]["parameters"]["required"] == ["host", "operation"]
    # No hay ningun campo donde quepa un comando libre.
    assert not {"command", "argv", "shell", "cmd", "script"} & set(props)
    assert "params" in props

    # Y el cliente no tiene ningun camino para ejecutar algo libre.
    import app.executor_client as module

    source = open(module.__file__, encoding="utf-8").read()
    for prohibido in ("subprocess", "os.system", "shell=True", "shlex"):
        assert prohibido not in source


def test_el_llm_no_tiene_herramientas_de_aprobacion():
    """Las tools del LLM no listan, aprueban ni ejecutan solicitudes."""
    nombres = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert not any("aprob" in n for n in nombres)
    assert not any("approve" in n for n in nombres)
    for spec in TOOL_SPECS:
        assert "/approvals" not in str(spec)


# --------------------------------------------------------------------------- #
# El resultado pending_approval llega al chat
# --------------------------------------------------------------------------- #
async def test_el_chat_recibe_el_id_de_la_solicitud(monkeypatch):
    """El LLM ve el id y el motivo: puede avisar al usuario, no decidir."""
    import app.graph as graph_module

    approvals = []

    async def fake_call(name, arguments=None):
        approvals.append((name, arguments))
        return {
            "ok": False,
            "denied": False,
            "state": "pending_approval",
            "host": "macbook",
            "operation": "host_git_diff_stat",
            "reason": "la operacion host_git_diff_stat requiere aprobacion humana explicita",
            "approval": {
                "id": "SOLICITUD-123",
                "status": "pending",
                "requested_by": "agente",
                "approved_by": None,
                "argv": ["git", "--no-optional-locks", "-C", "/ruta", "diff", "--stat"],
            },
        }

    def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(graph_module, "call_executor", fake_call)
    monkeypatch.setattr(graph_module, "add_audit", fake_audit)

    state = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "equipos_ejecutar",
                            "arguments": json.dumps(
                                {
                                    "host": "macbook",
                                    "operation": "host_git_diff_stat",
                                    "params": {"path": "/ruta"},
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    }

    result = await graph_module._run_tools(state)
    tool_message = result["messages"][-1]
    assert tool_message["role"] == "tool"
    payload = json.loads(tool_message["content"])
    assert payload["state"] == "pending_approval"
    assert payload["approval"]["id"] == "SOLICITUD-123"
    assert "aprobacion humana" in payload["reason"]

    # El resumen de auditoria identifica la solicitud pendiente.
    summary = result["tool_trace"][-1]["summary"]
    assert "SOLICITUD-123" in summary
    assert "aprobacion" in summary


def test_el_prompt_le_dice_al_llm_que_no_puede_aprobar():
    from app.config import SYSTEM_PROMPT

    assert "APROBACIÓN HUMANA" in SYSTEM_PROMPT
    assert "NO puedes aprobar" in SYSTEM_PROMPT
    assert "Aprobaciones" in SYSTEM_PROMPT
