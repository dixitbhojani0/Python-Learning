"""
backend/core/settings.py

Central settings loaded from .env via Pydantic Settings.
All environment variables are accessed through this class — never via os.getenv() directly.

Usage:
    from backend.core.settings import settings
    api_key = settings.GROQ_API_KEY
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Pydantic Settings reads from .env file automatically.
    Field names match exactly what's in .env.example.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",             # ignore unknown .env keys
    )

    # ── LLM
    GROQ_API_KEY: str = "placeholder"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── LangSmith Observability
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_API_KEY: str = "placeholder"
    LANGCHAIN_PROJECT: str = "ai-sdlc-assistant"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # ── Qdrant Vector DB
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "sdlc_knowledge"

    # ── Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_TTL_SECONDS: int = 3600

    # ── GitHub MCP
    GITHUB_TOKEN: str = "placeholder"
    GITHUB_REPO: str = "your-org/your-repo"

    # ── Jira MCP
    JIRA_BASE_URL: str = "https://your-org.atlassian.net"
    JIRA_EMAIL: str = "your-email@company.com"
    JIRA_TOKEN: str = "placeholder"
    JIRA_PROJECT_KEY: str = "SDLC"

    # ── Slack MCP
    SLACK_BOT_TOKEN: str = "placeholder"
    SLACK_USE_MOCK: bool = True

    # ── App
    APP_SECRET_KEY: str = "change_this_in_production"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Demo tokens (role-based auth without OAuth)
    DEMO_TOKEN_DEVELOPER: str = "dev_token_alice"
    DEMO_TOKEN_MANAGER: str = "manager_token_bob"
    DEMO_TOKEN_STAKEHOLDER: str = "stakeholder_token_client"

    # ── Default project
    DEFAULT_PROJECT: str = "antlog"


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings instance.
    @lru_cache ensures .env is only read once at startup.
    """
    return Settings()


# Module-level singleton — import this everywhere
settings = get_settings()
