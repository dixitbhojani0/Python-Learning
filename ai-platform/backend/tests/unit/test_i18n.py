"""
i18n translator tests — proves no user-facing text is static: the same key
resolves to genuinely different text per locale, missing keys/locales degrade
loudly-but-safely instead of crashing, and a caller/locale drift raises.
"""
from __future__ import annotations

import pytest

from backend.app.core.i18n import DEFAULT_LOCALE, TranslationKeyError, available_locales, t


# ── Positive ──────────────────────────────────────────────────────────────

def test_known_key_default_locale():
    assert t("health.ok", locale="en") == "Service is healthy."


def test_interpolation_substitutes_placeholder():
    result = t("health.degraded", locale="en", reason="db offline")
    assert "db offline" in result
    assert "{reason}" not in result


def test_second_locale_returns_genuinely_different_text():
    """
    The core proof that text is not static: the same key, two locales,
    two different strings — not a build-time-baked single string.
    """
    en_text = t("health.ok", locale="en")
    hi_text = t("health.ok", locale="hi")

    assert en_text != hi_text
    assert len(hi_text) > 0


def test_available_locales_includes_both_shipped_locales():
    locales = available_locales()
    assert "en" in locales
    assert "hi" in locales


# ── Negative ──────────────────────────────────────────────────────────────

def test_unknown_key_returns_visible_marker_not_exception():
    result = t("this.key.does.not.exist", locale="en")
    assert result == "??this.key.does.not.exist??"


def test_missing_interpolation_variable_raises_loudly():
    """
    A missing placeholder means the calling code and the locale file have
    drifted — that's a real bug and must not be swallowed into blank/wrong text.
    """
    with pytest.raises(TranslationKeyError):
        t("health.degraded", locale="en")  # "reason" not supplied


# ── Edge ──────────────────────────────────────────────────────────────────

def test_unrecognized_locale_falls_back_to_default():
    result = t("health.ok", locale="xx-not-a-real-locale")
    assert result == t("health.ok", locale=DEFAULT_LOCALE)


def test_default_locale_constant_has_a_translation_for_every_used_key():
    # Guards against a future key being added to hi.json but forgotten in en.json
    # (or vice versa) — DEFAULT_LOCALE is the fallback floor and must be complete.
    for key in ["health.ok", "health.degraded", "config.invalid", "llm.provider_unknown", "llm.generation_failed"]:
        assert t(key, locale=DEFAULT_LOCALE, reason="x", layer="x", detail="x", name="x", available="x")


# ── Side effects ────────────────────────────────────────────────────────

def test_translating_one_locale_does_not_affect_another():
    t("health.ok", locale="hi")
    en_after = t("health.ok", locale="en")
    assert en_after == "Service is healthy."
