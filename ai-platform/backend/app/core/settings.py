"""
backend/app/core/settings.py

Process-level infrastructure settings (env vars) — distinct from
core/config.py, which resolves per-tenant *business* config (locale,
provider, feature flags). Settings here are the same for every tenant in
one deployment; config.py varies per tenant within that deployment.

All environment variables are accessed through this class — never via
os.getenv() directly (same rule already enforced in ai-sdlc-assistant).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Runtime app connection — platform_app is an ordinary (non-superuser,
    # non-owner) role, so it is fully subject to Row-Level Security. Never
    # point this at the "platform" superuser role (see db-init/01-app-role.sql).
    DATABASE_URL: str = "postgresql+asyncpg://platform_app:platform_app_dev_only@localhost:15432/platform"

    # Migrations need DDL privileges, which the restricted runtime role
    # deliberately does not have — this connects as the table-owning
    # superuser instead. Only ever used by alembic/env.py, never by the app.
    MIGRATIONS_DATABASE_URL: str = "postgresql+asyncpg://platform:platform_dev_only@localhost:15432/platform"

    # JWT — placeholder is intentionally invalid-looking so a deployment that
    # forgets to set a real secret fails loudly in any check that inspects it,
    # rather than silently signing tokens with a guessable default.
    JWT_SECRET_KEY: str = "CHANGE_ME_INSECURE_DEFAULT"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    # LLM providers — empty by default (no live key checked into this repo).
    # A provider is only ever constructed when a tenant's config actually
    # selects it (LLMRegistry.create), so an unset key here does not stop the
    # app from starting or from running on "mock" — it only fails, loudly, if
    # something asks for "gemini"/"groq" specifically without one configured.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
