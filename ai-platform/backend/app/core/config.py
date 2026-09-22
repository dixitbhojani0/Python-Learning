"""
backend/app/core/config.py

Hierarchical, schema-validated configuration resolver.

Precedence (lowest to highest — later layers win): Platform defaults ->
Environment -> Tenant/Organization -> Project/Application -> Assistant/Agent
-> User/Role/Session. This is §7/§P of the platform blueprint: everything a
tenant might vary is config, resolved through one explicit precedence chain,
never a code branch.

Fail-safe requirement: an invalid layer (unknown key, wrong type, out-of-range
value) must never silently produce a partially-applied config — resolve()
either returns a fully valid PlatformConfig or raises ConfigValidationError.
There is no partial-success return value.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Ordered names for error messages only — resolve() itself is layer-count-agnostic
# so a new layer (e.g. a future "Region" tier) doesn't require editing this list.
LAYER_NAMES = ("platform_defaults", "environment", "tenant", "project", "assistant", "user")


class ConfigValidationError(Exception):
    """Raised when merged config fails schema validation. Carries the pydantic detail."""

    def __init__(self, message: str, *, errors: list[dict[str, Any]]):
        super().__init__(message)
        self.errors = errors


class PlatformConfig(BaseModel):
    """
    The resolved, validated configuration for one request/session context.

    extra="forbid" is deliberate: a typo'd or removed config key must fail
    loudly at resolve-time, not be silently ignored — an ignored typo is a
    classic way a tenant's override quietly never takes effect.
    """

    model_config = ConfigDict(extra="forbid")

    default_locale: str = "en"
    llm_provider: str = "mock"
    embedding_provider: str = "mock"
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    rate_limit_per_minute: int = Field(default=60, gt=0)


def _deep_merge_dict_fields(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Merge two layer dicts. Dict-valued fields (e.g. feature_flags) merge key-by-key
    so a higher layer can flip ONE flag without having to repeat every flag the
    lower layer already set. Every other field is a plain override.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def resolve_config(*layers: dict[str, Any]) -> PlatformConfig:
    """
    Merge `layers` in the order given (first = lowest precedence, last = highest)
    and validate the result. Raises ConfigValidationError on any schema violation
    — callers must not catch this and fall back to a partial config; the platform
    standard is fail closed (§7), not fail open.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = _deep_merge_dict_fields(merged, layer)

    try:
        return PlatformConfig(**merged)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"Configuration failed validation after merging {len(layers)} layer(s): "
            f"{exc.error_count()} error(s).",
            errors=exc.errors(),
        ) from exc
