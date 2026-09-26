"""
tests/unit/test_webhook_signature.py

Locks in the GITHUB_WEBHOOK_SECRET fix: the field used to be missing from
Settings (extra="ignore" silently dropped it from .env), so _signature_valid
always returned True. These tests fail if the field ever disappears again.
"""
import hashlib
import hmac

from backend.api.routes import webhooks
from backend.core.settings import settings

BODY = b'{"action": "opened", "pull_request": {"number": 7}}'


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_settings_has_webhook_secret_field():
    # The original bug: getattr(settings, "GITHUB_WEBHOOK_SECRET", "") on a model
    # without the field → always "" even when set in .env.
    assert "GITHUB_WEBHOOK_SECRET" in type(settings).model_fields


def test_valid_signature_accepted(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "s3cret")
    assert webhooks._signature_valid(BODY, _sign("s3cret", BODY)) is True


def test_invalid_signature_rejected(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "s3cret")
    assert webhooks._signature_valid(BODY, _sign("wrong-secret", BODY)) is False


def test_malformed_header_rejected(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "s3cret")
    assert webhooks._signature_valid(BODY, "md5=deadbeef") is False
    assert webhooks._signature_valid(BODY, "") is False


def test_no_secret_demo_mode_accepts(monkeypatch):
    # Documented demo-mode behavior: empty secret → accept (with a warning log).
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    assert webhooks._signature_valid(BODY, "") is True
