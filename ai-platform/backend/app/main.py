"""
backend/app/main.py

FastAPI entrypoint — Phase 1 slice only (§W Phase 1 of the platform
blueprint): config resolution, i18n, and the LLM provider registry wired
together end-to-end behind two endpoints. No auth/tenancy/RAG yet — those
are later phases and are deliberately not stubbed here (§C non-goals: no
half-finished scaffolding for a phase that hasn't started).
"""
from __future__ import annotations

from fastapi import FastAPI, Header
from pydantic import BaseModel

from backend.app.adapters.llm import providers as _providers  # noqa: F401  (triggers registration)
from backend.app.adapters.llm.registry import LLMRegistry, UnknownProviderError
from backend.app.api import (
    admin_routes,
    auth_routes,
    chat_routes,
    ingestion_routes,
    mcp_routes,
    memory_routes,
    rag_routes,
    tenant_routes,
    tool_approval_routes,
)
from backend.app.core.config import PLATFORM_DEFAULTS, ConfigValidationError, resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE, t

app = FastAPI(title="AI Platform", version="0.11.0")
app.include_router(auth_routes.router)
app.include_router(tenant_routes.router)
app.include_router(chat_routes.router)
app.include_router(rag_routes.router)
app.include_router(memory_routes.router)
app.include_router(admin_routes.router)
app.include_router(tool_approval_routes.router)
app.include_router(ingestion_routes.router)
app.include_router(mcp_routes.router)

# The tenant layer (§7) is now wired up for real — see api/deps.py's
# get_tenant_config(). This module's /v1/echo predates auth/tenancy entirely
# ("No auth/tenancy/RAG yet" above) and has no principal to resolve a tenant
# from, so it deliberately stays platform-defaults-only.


def _resolve_locale(accept_language: str | None) -> str:
    """Pick the first requested locale we actually have a file for, else DEFAULT_LOCALE."""
    from backend.app.core.i18n import available_locales

    if not accept_language:
        return DEFAULT_LOCALE
    requested = accept_language.split(",")[0].strip().split("-")[0].lower()
    return requested if requested in available_locales() else DEFAULT_LOCALE


@app.get("/health")
def health(accept_language: str | None = Header(default=None, alias="Accept-Language")) -> dict:
    locale = _resolve_locale(accept_language)
    return {"status": "ok", "message": t("health.ok", locale=locale)}


class EchoRequest(BaseModel):
    prompt: str


@app.post("/v1/echo")
async def echo(
    body: EchoRequest,
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    """
    Minimal end-to-end slice: config -> provider registry -> provider call.
    Proves the adapter boundary works before any real vendor key exists.
    """
    locale = _resolve_locale(accept_language)
    try:
        config = resolve_config(PLATFORM_DEFAULTS)
    except ConfigValidationError as exc:
        return {"error": t("config.invalid", locale=locale, layer="platform_defaults", detail=str(exc))}

    try:
        provider = LLMRegistry.create(config.llm_provider)
    except UnknownProviderError:
        return {
            "error": t(
                "llm.provider_unknown",
                locale=locale,
                name=config.llm_provider,
                available=", ".join(LLMRegistry.available()),
            )
        }

    text = await provider.generate_text(body.prompt)
    return {"model": provider.get_model_name(), "text": text}
