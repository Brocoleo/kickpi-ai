"""Fusible por modelo: lo que se cuenta y lo que se ve.

El detalle que importa: un rechazo del proveedor que no llega a abrir el
fusible (una cuota agotada, por ejemplo) tiene que aparecer en `/api/llm/
status`. Si no aparece, el diagnóstico dice "nada ha pasado" mientras el
chat no responde.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from app.config import get_settings  # noqa: E402
from app.llm import CircuitBreaker  # noqa: E402


def test_snapshot_muestra_fallos_sin_abrir(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LLM_CIRCUIT_THRESHOLD", "2")
    get_settings.cache_clear()
    breaker = CircuitBreaker()

    breaker.record_failure("uno/modelo:free", "HTTP 400: model not available")

    datos = breaker.snapshot()
    assert datos["uno/modelo:free"]["open"] is False
    assert datos["uno/modelo:free"]["failures"] == 1
    assert datos["uno/modelo:free"]["last_error"].startswith("HTTP 400")
    assert datos["uno/modelo:free"]["last_failure_ago_s"] is not None


def test_segundo_fallo_abre_el_fusible(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LLM_CIRCUIT_THRESHOLD", "2")
    get_settings.cache_clear()
    breaker = CircuitBreaker()

    breaker.record_failure("dos/modelo:free", "HTTP 429")
    breaker.record_failure("dos/modelo:free", "HTTP 429")

    datos = breaker.snapshot()["dos/modelo:free"]
    assert datos["open"] is True
    assert datos["reopens_in_s"] > 0
    assert datos["failures"] == 2


def test_un_exito_borra_el_historial() -> None:
    breaker = CircuitBreaker()
    breaker.record_failure("tres/modelo:free", "timeout")

    breaker.record_success("tres/modelo:free")

    assert breaker.snapshot() == {}


def test_el_fusible_no_mezcla_modelos(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LLM_CIRCUIT_THRESHOLD", "2")
    get_settings.cache_clear()
    breaker = CircuitBreaker()

    breaker.record_failure("cuatro/uno:free", "HTTP 429")
    breaker.record_failure("cuatro/dos:free", "HTTP 500")

    datos = breaker.snapshot()
    assert datos["cuatro/uno:free"]["failures"] == 1
    assert datos["cuatro/dos:free"]["failures"] == 1
    assert datos["cuatro/uno:free"]["open"] is False
