"""Cliente LLM con cadena de fallback y circuit breaker.

Política (acordada con el administrador):

    HTTP 429              -> no reintentar; pasar al siguiente modelo
    HTTP 5xx              -> reintento breve (backoff) y luego fallback
    Timeout / red         -> pasar al siguiente modelo
    HTTP 401/403          -> DETENER: es un error de configuración/credenciales
    Respuesta válida      -> mantener y devolver ese modelo
    Todos fallan          -> LLMError controlado con el resumen

Claves del diseño:
- **No se repiten acciones**: el fallback ocurre SOLO en la llamada al modelo.
  Los mensajes y las tool_calls no se reenvían a ejecución; el grafo ejecuta
  las herramientas una única vez cuando el modelo devuelve la respuesta.
- **Circuit breaker** por modelo: tras N fallos consecutivos se evita ese
  modelo durante un enfriamiento, para no martillar a un proveedor saturado.
"""
import asyncio
import contextvars
import json
import logging
import time

import httpx

from .config import get_settings

log = logging.getLogger("jarvis-agent.llm")

AUTH = "auth"
RATE_LIMIT = "rate_limit"
SERVER = "server"
TIMEOUT = "timeout"
NETWORK = "network"
BAD_REQUEST = "bad_request"


class LLMError(RuntimeError):
    def __init__(
        self,
        message: str,
        kind: str = "error",
        model: str | None = None,
        retryable: bool = False,
        emitted: bool = False,
    ):
        super().__init__(message)
        self.kind = kind
        self.model = model
        self.retryable = retryable
        # True si ya se enviaron tokens al cliente antes del fallo: en ese
        # caso NO se puede hacer fallback (duplicaria texto).
        self.emitted = emitted


class CircuitBreaker:
    """Bloqueo temporal por modelo tras fallos consecutivos."""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._last_error: dict[str, str] = {}

    def is_open(self, model: str) -> bool:
        until = self._open_until.get(model)
        if not until:
            return False
        if time.monotonic() >= until:
            # Enfriamiento terminado: half-open, se permite un intento.
            self._open_until.pop(model, None)
            self._failures.pop(model, None)
            return False
        return True

    def record_success(self, model: str) -> None:
        self._failures.pop(model, None)
        self._open_until.pop(model, None)
        self._last_error.pop(model, None)

    def record_failure(self, model: str, error: str) -> None:
        settings = get_settings()
        count = self._failures.get(model, 0) + 1
        self._failures[model] = count
        self._last_error[model] = error[:200]
        if count >= settings.llm_circuit_threshold:
            self._open_until[model] = time.monotonic() + settings.llm_circuit_cooldown
            log.warning(
                "circuito ABIERTO para %s durante %.0fs (fallos consecutivos=%d)",
                model,
                settings.llm_circuit_cooldown,
                count,
            )

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            model: {
                "open": until > now,
                "reopens_in_s": round(max(0.0, until - now), 1),
                "failures": self._failures.get(model, 0),
                "last_error": self._last_error.get(model),
            }
            for model, until in self._open_until.items()
        }


CIRCUIT = CircuitBreaker()

# Canal de eventos para el chat en streaming (SSE).
#
# El endpoint instala una funcion en este ContextVar y los nodos del grafo la
# usan para emitir tokens/eventos. Asi el grafo no depende de FastAPI ni de
# una conexion concreta (tambien funciona con `ainvoke` normal: el valor por
# defecto es None).
STREAM_EMIT: contextvars.ContextVar = contextvars.ContextVar(
    "jarvis_stream_emit", default=None
)


def _classify_status(status: int) -> tuple[str, bool]:
    if status in (401, 403):
        return AUTH, False
    if status == 429:
        return RATE_LIMIT, False
    if 500 <= status < 600:
        return SERVER, True
    if status in (400, 404, 422):
        return BAD_REQUEST, False
    return NETWORK, False


async def _post(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    timeout: float,
) -> dict:
    settings = get_settings()
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": settings.llm_max_tokens,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/kickpi-ai",
        "X-Title": "Jarvis Agent",
    }

    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LLMError(f"timeout tras {timeout:.0f}s", TIMEOUT, model) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"error de red: {exc}", NETWORK, model) from exc

    if response.status_code >= 400:
        kind, retryable = _classify_status(response.status_code)
        body = response.text[:200].replace("\n", " ")
        raise LLMError(
            f"HTTP {response.status_code}: {body}",
            kind,
            model,
            retryable=retryable,
        )

    try:
        data = response.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMError(f"respuesta inválida: {exc}", BAD_REQUEST, model) from exc

    message.setdefault("role", "assistant")
    return message


async def chat_completion(
    messages: list[dict], tools: list[dict] | None = None
) -> tuple[dict, str]:
    """Devuelve (mensaje_del_asistente, modelo_usado).

    `messages` no se muta: el fallback reenvía exactamente el mismo contexto.
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMError("No hay JARVIS_LLM_API_KEY configurada", AUTH)

    chain = settings.llm_model_chain
    if not chain:
        raise LLMError("No hay modelos configurados", AUTH)

    started = time.monotonic()
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        for model in chain:
            if CIRCUIT.is_open(model):
                errors.append(f"{model}: en enfriamiento (circuit breaker)")
                log.info("saltando %s: circuito abierto", model)
                continue

            remaining = settings.llm_total_budget - (time.monotonic() - started)
            if remaining <= 1:
                errors.append("presupuesto de tiempo agotado")
                break

            attempts = 1 + max(0, settings.llm_max_retries_server)
            succeeded = False
            for attempt in range(attempts):
                per_attempt = min(settings.llm_timeout, max(1.0, remaining))
                try:
                    message = await _post(client, model, messages, tools, per_attempt)
                except LLMError as exc:
                    if exc.kind == AUTH:
                        # Credenciales: no tiene sentido probar otros modelos.
                        log.error("error de autenticación con %s: %s", model, exc)
                        raise

                    if exc.retryable and attempt < attempts - 1:
                        delay = settings.llm_retry_backoff * (attempt + 1)
                        log.warning(
                            "%s intento %d/%d falló (%s); reintento en %.1fs",
                            model, attempt + 1, attempts, exc.kind, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    CIRCUIT.record_failure(model, str(exc))
                    errors.append(f"{model}: {exc}")
                    log.warning("modelo %s descartado (%s): %s", model, exc.kind, exc)
                    break
                succeeded = True
                break

            if succeeded:
                CIRCUIT.record_success(model)
                if errors:
                    log.info("respondió %s tras fallback (%d errores previos)", model, len(errors))
                return message, model

    detail = " | ".join(errors) if errors else "sin detalle"
    raise LLMError(
        f"Ningún modelo respondió. {detail}",
        "all_failed",
    )


async def _stream_post(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    timeout: float,
    on_token,
) -> tuple[dict, bool]:
    """Una llamada SSE al proveedor. Devuelve (mensaje, emitio_texto)."""
    settings = get_settings()
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": settings.llm_max_tokens,
        "temperature": 0.2,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/kickpi-ai",
        "X-Title": "Jarvis Agent",
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"

    parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    emitted = False

    try:
        async with client.stream(
            "POST", url, json=payload, headers=headers, timeout=timeout
        ) as response:
            if response.status_code >= 400:
                body_bytes = await response.aread()
                body = body_bytes.decode("utf-8", "replace")[:200].replace("\n", " ")
                kind, retryable = _classify_status(response.status_code)
                raise LLMError(
                    f"HTTP {response.status_code}: {body}",
                    kind,
                    model,
                    retryable=retryable,
                    emitted=emitted,
                )

            async for line in response.aiter_lines():
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                text = delta.get("content")
                if isinstance(text, list):  # algunos proveedores mandan bloques
                    text = "".join(
                        part.get("text", "")
                        for part in text
                        if isinstance(part, dict)
                    )
                if text:
                    parts.append(text)
                    emitted = True
                    on_token(text)

                for call in delta.get("tool_calls") or []:
                    try:
                        index = int(call.get("index") or 0)
                    except (TypeError, ValueError):
                        index = 0
                    acc = tool_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if call.get("id"):
                        acc["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        acc["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        acc["function"]["arguments"] += fn["arguments"]
    except httpx.TimeoutException as exc:
        raise LLMError(
            f"timeout tras {timeout:.0f}s", TIMEOUT, model, emitted=emitted
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(
            f"error de red: {exc}", NETWORK, model, emitted=emitted
        ) from exc
    except LLMError as exc:
        exc.emitted = emitted
        raise

    message: dict = {"role": "assistant", "content": "".join(parts)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return message, emitted


async def chat_completion_stream(
    messages: list[dict], tools: list[dict] | None, on_token
) -> tuple[dict, str]:
    """Como `chat_completion`, pero reenviando cada token a `on_token`.

    El fallback entre modelos solo ocurre si todavia no se ha enviado texto
    (si ya se emitio, cambiar de modelo duplicaria la respuesta).
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMError("No hay JARVIS_LLM_API_KEY configurada", AUTH)

    chain = settings.llm_model_chain
    if not chain:
        raise LLMError("No hay modelos configurados", AUTH)

    started = time.monotonic()
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        for model in chain:
            if CIRCUIT.is_open(model):
                errors.append(f"{model}: en enfriamiento (circuit breaker)")
                continue

            remaining = settings.llm_total_budget - (time.monotonic() - started)
            if remaining <= 1:
                errors.append("presupuesto de tiempo agotado")
                break

            attempts = 1 + max(0, settings.llm_max_retries_server)
            for attempt in range(attempts):
                per_attempt = min(settings.llm_timeout, max(1.0, remaining))
                try:
                    message, _ = await _stream_post(
                        client, model, messages, tools, per_attempt, on_token
                    )
                except LLMError as exc:
                    if exc.emitted:
                        # Ya hay texto en pantalla: no se reintenta.
                        CIRCUIT.record_failure(model, str(exc))
                        raise
                    if exc.kind == AUTH:
                        raise
                    if exc.retryable and attempt < attempts - 1:
                        delay = settings.llm_retry_backoff * (attempt + 1)
                        log.warning(
                            "%s (stream) intento %d/%d fallo (%s); reintento en %.1fs",
                            model, attempt + 1, attempts, exc.kind, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    CIRCUIT.record_failure(model, str(exc))
                    errors.append(f"{model}: {exc}")
                    log.warning(
                        "modelo %s descartado en stream (%s): %s",
                        model, exc.kind, exc,
                    )
                    break

                CIRCUIT.record_success(model)
                if errors:
                    log.info(
                        "respondio %s en stream tras fallback (%d errores)",
                        model, len(errors),
                    )
                return message, model

    detail = " | ".join(errors) if errors else "sin detalle"
    raise LLMError(f"Ningun modelo respondio. {detail}", "all_failed")
