"""Pruebas de la protección CSRF y de la validación de Origin."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.csrf import (
    allowed_origins,
    check_csrf,
    check_origin,
    csrf_key,
    issue_token,
    verify_token,
)


@pytest.fixture(autouse=True)
def server_secret(monkeypatch):
    """El servidor necesita un secreto para poder emitir tokens CSRF."""
    monkeypatch.setenv("JARVIS_CSRF_SECRET", "secreto-de-prueba")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = {k.lower(): v for k, v in headers.items()}


def test_token_is_deterministic_and_bound_to_the_session():
    token_a = issue_token("huella-sesion-A")
    token_b = issue_token("huella-sesion-B")
    assert token_a
    assert token_a != token_b
    assert issue_token("huella-sesion-A") == token_a


def test_verify_rejects_wrong_or_missing_token():
    token = issue_token("huella-sesion-A")
    assert verify_token("huella-sesion-A", token) is True
    assert verify_token("huella-sesion-A", "0" * 64) is False
    assert verify_token("huella-sesion-A", None) is False
    assert verify_token("huella-sesion-A", "") is False
    # El token de otra sesión no vale.
    assert verify_token("huella-sesion-B", token) is False


def test_fails_closed_without_server_secret(monkeypatch):
    monkeypatch.setenv("JARVIS_CSRF_SECRET", "")
    monkeypatch.setenv("JARVIS_API_TOKEN", "")
    monkeypatch.setenv("JARVIS_ADMIN_PASSWORD_HASH", "")
    get_settings.cache_clear()
    assert csrf_key() == b""
    assert issue_token("huella") == ""
    assert verify_token("huella", "cualquiera") is False
    get_settings.cache_clear()


def test_origin_missing_is_rejected():
    request = FakeRequest({"host": "kickpi.tail.ts.net"})
    with pytest.raises(Exception) as exc:
        check_origin(request)
    assert "Origin" in str(exc.value)


def test_origin_same_host_is_accepted():
    request = FakeRequest(
        {"host": "kickpi.tail.ts.net", "origin": "https://kickpi.tail.ts.net"}
    )
    check_origin(request)  # no lanza


def test_origin_other_host_is_rejected():
    request = FakeRequest(
        {"host": "kickpi.tail.ts.net", "origin": "https://evil.example.com"}
    )
    with pytest.raises(Exception) as exc:
        check_origin(request)
    assert "no autorizado" in str(exc.value)


def test_configured_origin_is_accepted(monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOWED_ORIGINS", "https://otro.example.com")
    get_settings.cache_clear()
    request = FakeRequest(
        {"host": "kickpi.tail.ts.net", "origin": "https://otro.example.com"}
    )
    check_origin(request)
    get_settings.cache_clear()


def test_allowed_origins_contains_same_origin(monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    request = FakeRequest({"host": "kickpi.tail.ts.net"})
    assert "kickpi.tail.ts.net" in allowed_origins(request)
    get_settings.cache_clear()


def test_csrf_header_must_match_the_session():
    request = FakeRequest({"x-csrf-token": "abc"})
    with pytest.raises(Exception) as exc:
        check_csrf(request, "huella-sesion-A")
    assert "CSRF" in str(exc.value)
