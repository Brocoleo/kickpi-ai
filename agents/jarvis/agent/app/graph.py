"""Grafo del agente (LangGraph).

Ciclo: modelo → (si pidió herramientas) tools → modelo → … → respuesta final.
Solo hay un nodo de herramientas y todas son de lectura.
"""
import asyncio
import json
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .config import get_settings
from .executor_client import TOOL_NAMES as EXECUTOR_TOOL_NAMES
from .executor_client import TOOL_SPECS as EXECUTOR_TOOL_SPECS
from .executor_client import ExecutorError, call_executor
from .llm import (
    STREAM_EMIT,
    LLMError,
    chat_completion,
    chat_completion_stream,
)
from .memory import add_audit, link_approval
from .tools_client import TOOL_SPECS, ToolsError, call_tool


class AgentState(TypedDict, total=False):
    messages: list
    tool_trace: list
    iterations: int
    conversation_id: str
    model_used: str | None


def _summarize(result: dict) -> str:
    if not isinstance(result, dict):
        return "ok"
    if "error" in result:
        return f"error: {result['error']}"
    if result.get("denied"):
        return f"denegado: {result.get('reason')}"
    if result.get("state") == "pending_approval":
        approval = result.get("approval") or {}
        return (
            f"solicitud de aprobacion pendiente "
            f"({approval.get('id', '?')}) para el usuario"
        )
    if "containers" in result:
        return f"{result.get('count')} contenedores"
    if "disks" in result:
        return ", ".join(f"{d['mount']} {d['percent']}%" for d in result["disks"])
    if "logs" in result:
        return f"logs de {result.get('container')} ({result.get('tail')} líneas)"
    if "cpu" in result:
        return f"CPU {result['cpu']['percent_total']}% · RAM {result['memory']['percent']}%"
    if "hosts" in result:
        return f"{result.get('count')} equipos registrados"
    if "executions" in result:
        return f"{result.get('count')} ejecuciones"
    if "stdout" in result:
        first = (result.get("stdout") or "").strip().splitlines()
        head = first[0][:80] if first else ""
        return f"exit={result.get('exit_code')} {head}"
    return "ok"


async def _call_model(state: AgentState) -> dict:
    messages = state["messages"]
    iterations = state.get("iterations", 0) + 1
    tools = list(TOOL_SPECS) + list(EXECUTOR_TOOL_SPECS)
    emit = STREAM_EMIT.get()
    try:
        if emit is None:
            msg, model_used = await chat_completion(messages, tools=tools)
        else:
            # Cada llamada al modelo abre una burbuja nueva en la PWA. Se
            # emite "segment" con el primer token, no antes: si el modelo
            # falla y se hace fallback, no queda una burbuja vacia.
            started = {"value": False}

            def on_token(text: str) -> None:
                if not started["value"]:
                    started["value"] = True
                    emit({"event": "segment"})
                emit({"event": "token", "text": text})

            msg, model_used = await chat_completion_stream(
                messages, tools, on_token
            )
    except LLMError as exc:
        # Degradamos con elegancia: el usuario ve el motivo, no un 500.
        msg = {"role": "assistant", "content": f"⚠️ No pude consultar el modelo: {exc}"}
        return {"messages": messages + [msg], "iterations": iterations}
    return {
        "messages": messages + [msg],
        "iterations": iterations,
        "model_used": model_used,
    }


async def _run_tools(state: AgentState) -> dict:
    last = state["messages"][-1]
    new_messages: list[dict] = []
    trace: list[dict] = []

    for call in last.get("tool_calls") or []:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}

        # Aviso de INICIO: la PWA puede mostrar «usando X…» mientras la
        # herramienta trabaja. El evento `tool` de después solo confirma el
        # resultado; sin este, una consulta lenta parecía un modelo pensando.
        emit = STREAM_EMIT.get()
        if emit is not None:
            emit({"event": "tool_start", "tool": name, "arguments": args})

        started = time.monotonic()
        ok = True
        try:
            if name in EXECUTOR_TOOL_NAMES:
                result = await call_executor(name, args)
            else:
                result = await call_tool(name, args)
        except (ToolsError, ExecutorError) as exc:
            result = {"error": str(exc)}
            ok = False
        duration = int((time.monotonic() - started) * 1000)
        summary = _summarize(result)

        # Enlaza la aprobacion con el turno EN CUANTO se crea, sin esperar a
        # que termine el turno: el humano puede decidir antes y el chat debe
        # poder reanudarse igualmente.
        if isinstance(result, dict) and result.get("state") == "pending_approval":
            approval = result.get("approval") or {}
            approval_id = approval.get("id") if isinstance(approval, dict) else None
            if approval_id and state.get("conversation_id"):
                await asyncio.to_thread(
                    link_approval,
                    approval_id,
                    state["conversation_id"],
                    call.get("id"),
                )

        await asyncio.to_thread(
            add_audit, name, args, ok, duration, summary, state.get("conversation_id")
        )

        new_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id"),
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
        trace.append(
            {
                "tool": name,
                "arguments": args,
                "ok": ok,
                "duration_ms": duration,
                "summary": summary,
                # Permite a la persistencia asociar el resultado con su
                # llamada y guardar ok/duración/resumen en el historial.
                "tool_call_id": call.get("id"),
            }
        )
        emit = STREAM_EMIT.get()
        if emit is not None:
            emit(
                {
                    "event": "tool",
                    "tool": name,
                    "ok": ok,
                    "duration_ms": duration,
                    "summary": summary,
                }
            )

    return {
        "messages": state["messages"] + new_messages,
        "tool_trace": state.get("tool_trace", []) + trace,
    }


def _should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    pending_calls = bool(
        last.get("role") == "assistant" and last.get("tool_calls")
    )
    if state.get("iterations", 0) >= get_settings().max_tool_iterations:
        # Si el modelo se quedó pidiendo herramientas, NO se termina en seco:
        # se pasa por un nodo que cancela las llamadas pendientes y deja una
        # respuesta de texto visible para el usuario.
        return "limit" if pending_calls else "end"
    return "tools" if pending_calls else "end"


async def _close_on_limit(state: AgentState) -> dict:
    """Cierre explícito al agotar el presupuesto de iteraciones.

    - Resuelve cada `tool_call` pendiente con un mensaje `tool` (si se dejara
      sin respuesta, el historial quedaría inválido para la siguiente
      petición).
    - Añade un mensaje de asistente con texto, de modo que la PWA nunca
      muestre una burbuja vacía.
    """
    last = state["messages"][-1]
    limit = get_settings().max_tool_iterations
    new_messages: list[dict] = []
    for call in last.get("tool_calls") or []:
        fn = call.get("function") or {}
        new_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id"),
                "name": fn.get("name", ""),
                "content": json.dumps(
                    {
                        "error": "cancelado: se alcanzó el límite de pasos del agente",
                        "hint": "repregunta de forma más concreta o divide la tarea",
                    },
                    ensure_ascii=False,
                ),
            }
        )

    trace = state.get("tool_trace", [])
    new_messages.append(
        {
            "role": "assistant",
            "content": (
                f"Me detuve al alcanzar el límite de {limit} pasos con herramientas. "
                "Ya tengo parte del trabajo hecho (mira las tarjetas de actividad), "
                "pero no llegué a redactar la respuesta final. "
                "Pídeme el resumen concreto o divide la tarea en pasos más pequeños."
            ),
        }
    )
    return {
        "messages": state["messages"] + new_messages,
        "tool_trace": trace,
    }


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_node("limit", _close_on_limit)
    graph.set_entry_point("model")
    graph.add_conditional_edges(
        "model", _should_continue, {"tools": "tools", "limit": "limit", "end": END}
    )
    graph.add_edge("tools", "model")
    graph.add_edge("limit", END)
    return graph.compile()


AGENT = build_graph()
