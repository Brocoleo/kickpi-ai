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
from .llm import LLMError, chat_completion
from .memory import add_audit
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
    if "containers" in result:
        return f"{result.get('count')} contenedores"
    if "disks" in result:
        return ", ".join(f"{d['mount']} {d['percent']}%" for d in result["disks"])
    if "logs" in result:
        return f"logs de {result.get('container')} ({result.get('tail')} líneas)"
    if "cpu" in result:
        return f"CPU {result['cpu']['percent_total']}% · RAM {result['memory']['percent']}%"
    return "ok"


async def _call_model(state: AgentState) -> dict:
    messages = state["messages"]
    iterations = state.get("iterations", 0) + 1
    try:
        msg, model_used = await chat_completion(messages, tools=TOOL_SPECS)
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

        started = time.monotonic()
        ok = True
        try:
            result = await call_tool(name, args)
        except ToolsError as exc:
            result = {"error": str(exc)}
            ok = False
        duration = int((time.monotonic() - started) * 1000)
        summary = _summarize(result)

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
            {"tool": name, "arguments": args, "ok": ok, "duration_ms": duration, "summary": summary}
        )

    return {
        "messages": state["messages"] + new_messages,
        "tool_trace": state.get("tool_trace", []) + trace,
    }


def _should_continue(state: AgentState) -> str:
    if state.get("iterations", 0) >= get_settings().max_tool_iterations:
        return "end"
    last = state["messages"][-1]
    if last.get("role") == "assistant" and last.get("tool_calls"):
        return "tools"
    return "end"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.set_entry_point("model")
    graph.add_conditional_edges("model", _should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "model")
    return graph.compile()


AGENT = build_graph()
