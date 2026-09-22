"""
End-to-end API tests through FastAPI's TestClient — exercises config, i18n,
and the provider registry together the way a real request would, not just
each piece in isolation.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import app

client = TestClient(app)


# ── Positive ──────────────────────────────────────────────────────────────

def test_health_default_locale():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Service is healthy."}


def test_health_respects_accept_language_header():
    response = client.get("/health", headers={"Accept-Language": "hi"})
    assert response.status_code == 200
    body = response.json()
    # Same shape, genuinely different text — proves the API layer is not
    # hardcoded English, not just the i18n unit underneath it.
    assert body["message"] != "Service is healthy."


def test_echo_round_trips_through_config_and_registry():
    response = client.post("/v1/echo", json={"prompt": "ping"})
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "mock-echo-v1"
    assert "ping" in body["text"]


# ── Negative ──────────────────────────────────────────────────────────────

def test_echo_missing_prompt_field_is_rejected():
    response = client.post("/v1/echo", json={})
    assert response.status_code == 422  # FastAPI/Pydantic request validation, not a 500


def test_echo_reports_a_translated_error_when_platform_config_is_invalid(monkeypatch):
    # _PLATFORM_DEFAULTS is hardcoded-valid in normal operation — this branch
    # is genuinely dead in practice today, but it's real defensive code
    # (§7 fail-closed config) and deserves proof it actually does what it
    # says, not just an assumption that the try/except is correct.
    monkeypatch.setitem(main._PLATFORM_DEFAULTS, "llm_provider", 12345)  # wrong type for a str field
    response = client.post("/v1/echo", json={"prompt": "hi"})
    assert response.status_code == 200  # the route itself doesn't 500 — it returns a structured error
    assert "error" in response.json()


def test_echo_reports_a_translated_error_when_configured_provider_is_unregistered(monkeypatch):
    monkeypatch.setitem(main._PLATFORM_DEFAULTS, "llm_provider", "totally-bogus-provider")
    response = client.post("/v1/echo", json={"prompt": "hi"})
    assert response.status_code == 200
    assert "mock" in response.json()["error"]  # the available-providers list is included, not just "unknown"


# ── Edge ──────────────────────────────────────────────────────────────────

def test_health_unsupported_locale_falls_back_to_default():
    response = client.get("/health", headers={"Accept-Language": "fr-FR,fr;q=0.9"})
    assert response.status_code == 200
    assert response.json()["message"] == "Service is healthy."


def test_echo_empty_prompt_string_is_accepted_not_rejected():
    # Empty string is a valid (if useless) prompt — it's a UX concern for the
    # frontend to prevent, not a 422-worthy validation error at the API layer.
    response = client.post("/v1/echo", json={"prompt": ""})
    assert response.status_code == 200


# ── Side effects ────────────────────────────────────────────────────────

def test_repeated_calls_are_independent_and_consistent():
    first = client.post("/v1/echo", json={"prompt": "a"})
    second = client.post("/v1/echo", json={"prompt": "b"})

    assert "a" in first.json()["text"]
    assert "b" in second.json()["text"]
    assert "b" not in first.json()["text"]  # no bleed from the second call into the first's response object
