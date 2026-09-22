"""
backend/app/core/i18n.py

Translation service — every user-facing string in this platform goes through
this, never a hardcoded Python string. Locale dictionaries live in
backend/locales/<locale>.json, keyed by dotted key (e.g. "health.ok").

Why this exists (not a nice-to-have): §24/§K of the platform blueprint requires
per-tenant locale as configuration, not a build-time constant — a hardcoded
string here would mean a second app build per language, which defeats the
"one codebase, configure per client" mandate.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent.parent.parent / "locales"
DEFAULT_LOCALE = "en"

# Marker used when a key is missing — visible in output so a missing
# translation is a loud bug during development, not a silent blank string.
_MISSING_KEY_MARKER = "??{key}??"


class TranslationKeyError(Exception):
    """Raised when a key is missing from every locale in the fallback chain, including default."""


@lru_cache(maxsize=None)
def _load_locale(locale: str) -> dict[str, str]:
    """
    Load one locale file as a flat dict. Cached — files are read once per
    process, not once per request. Returns {} for a locale file that doesn't
    exist (the caller falls back to DEFAULT_LOCALE), not a raised error —
    an unrecognized locale is a routine, expected condition, not a bug.
    """
    path = _LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        logger.warning("i18n: locale file not found for '%s' (%s)", locale, path)
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def available_locales() -> list[str]:
    """List of locale codes that actually have a locale file on disk."""
    if not _LOCALES_DIR.exists():
        return []
    return sorted(p.stem for p in _LOCALES_DIR.glob("*.json"))


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs: object) -> str:
    """
    Translate `key` into `locale`, with {placeholder} interpolation from kwargs.

    Fallback chain: requested locale -> DEFAULT_LOCALE -> loud missing-key marker.
    Never raises for a missing locale or a missing key — a translation gap
    must not turn into a 500 for the end user; it must be visibly wrong
    instead, so it gets caught in review/QA rather than crashing production.
    A missing *interpolation variable* IS a caller bug and raises, since that
    means the calling code and the locale string have drifted out of sync.
    """
    catalog = _load_locale(locale)
    template = catalog.get(key)

    used_locale = locale
    if template is None and locale != DEFAULT_LOCALE:
        catalog = _load_locale(DEFAULT_LOCALE)
        template = catalog.get(key)
        used_locale = DEFAULT_LOCALE

    if template is None:
        logger.error("i18n: missing translation key '%s' in '%s' and default locale", key, locale)
        return _MISSING_KEY_MARKER.format(key=key)

    try:
        return template.format(**kwargs)
    except KeyError as exc:
        raise TranslationKeyError(
            f"i18n: key '{key}' (locale '{used_locale}') requires placeholder {exc}, "
            f"which was not provided by the caller."
        ) from exc


def reset_cache() -> None:
    """Test-only: clear the locale cache so file changes are picked up mid-test."""
    _load_locale.cache_clear()
