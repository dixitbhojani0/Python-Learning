"""
Config resolver tests — positive, negative, edge, and side-effect cases.
Run as part of the full suite; see backend/tests for the growing regression set.
"""
from __future__ import annotations

import pytest

from backend.app.core.config import ConfigValidationError, PlatformConfig, resolve_config


# ── Positive ──────────────────────────────────────────────────────────────

def test_no_layers_resolves_to_schema_defaults():
    config = resolve_config()
    assert config == PlatformConfig()


def test_higher_layer_overrides_lower_layer():
    platform_defaults = {"default_locale": "en", "llm_provider": "mock"}
    tenant_override = {"llm_provider": "gemini"}

    config = resolve_config(platform_defaults, tenant_override)

    assert config.llm_provider == "gemini"
    assert config.default_locale == "en"  # untouched field survives from the lower layer


def test_feature_flags_deep_merge_instead_of_replace():
    platform_defaults = {"feature_flags": {"rag_enabled": True}}
    tenant_override = {"feature_flags": {"agents_enabled": False}}

    config = resolve_config(platform_defaults, tenant_override)

    # Both flags present — tenant layer added a flag, it did not wipe the platform one.
    assert config.feature_flags == {"rag_enabled": True, "agents_enabled": False}


# ── Negative ──────────────────────────────────────────────────────────────

def test_unknown_key_in_a_layer_fails_closed():
    with pytest.raises(ConfigValidationError):
        resolve_config({"this_key_does_not_exist": True})


def test_wrong_type_fails_closed():
    with pytest.raises(ConfigValidationError):
        resolve_config({"rate_limit_per_minute": "not-a-number"})


def test_validation_error_carries_pydantic_detail_for_debugging():
    with pytest.raises(ConfigValidationError) as exc_info:
        resolve_config({"rate_limit_per_minute": "not-a-number"})

    assert len(exc_info.value.errors) == 1
    assert exc_info.value.errors[0]["loc"] == ("rate_limit_per_minute",)


# ── Edge ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_value", [0, -1, -1000])
def test_rate_limit_must_be_strictly_positive(bad_value):
    with pytest.raises(ConfigValidationError):
        resolve_config({"rate_limit_per_minute": bad_value})


def test_empty_dict_layers_are_a_no_op():
    config = resolve_config({}, {}, {})
    assert config == PlatformConfig()


# ── Side effects ──────────────────────────────────────────────────────────

def test_resolve_config_does_not_mutate_caller_supplied_layers():
    platform_defaults = {"feature_flags": {"rag_enabled": True}}
    original_snapshot = dict(platform_defaults)

    resolve_config(platform_defaults, {"feature_flags": {"agents_enabled": True}})

    # The caller's dict must be exactly what it was before the call — a config
    # layer object might be reused across multiple resolve_config() calls for
    # different tenants, and mutation here would silently corrupt every later call.
    assert platform_defaults == original_snapshot


def test_two_independent_resolutions_do_not_leak_into_each_other():
    config_a = resolve_config({"llm_provider": "groq"})
    config_b = resolve_config({"llm_provider": "gemini"})

    assert config_a.llm_provider == "groq"
    assert config_b.llm_provider == "gemini"
