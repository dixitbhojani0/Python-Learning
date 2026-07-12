"""
core/settings.py

MCP server environment settings — only the vars this service needs.
Strips out FastAPI JWT, LangSmith, Qdrant, Redis, etc. (not our concern).
"""
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── MCP Server
    MCP_SERVER_HOST: str = "127.0.0.1"
    MCP_SERVER_PORT: int = 8100

    # ── OAuth 2.1 auth (MCP 2025 standard)
    # Public URL clients use to reach this server (must match what hosts see)
    OAUTH_ISSUER_URL: str = "http://127.0.0.1:8100"
    # Optional PIN shown on the browser consent page — empty = no PIN required
    OAUTH_CONSENT_PIN: str = ""
    # Shared secret for /oauth/token/service endpoint.
    # ai-sdlc POSTs this to get a short-lived (1hr) rotating access token automatically.
    # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
    # Set the same value in ai-sdlc-assistant/.env as MCP_SERVICE_SECRET.
    OAUTH_SERVICE_SECRET: str = "placeholder"
    # ── Redis (token store)
    # Docker: redis://redis:6379  (service name within docker-compose network)
    # Local:  redis://localhost:6381  (mapped port from ai-sdlc-assistant Redis)
    REDIS_URL: str = "redis://127.0.0.1:6379"

    # ── Jira (Atlassian Cloud)
    JIRA_BASE_URL: str = "https://your-org.atlassian.net"
    JIRA_EMAIL: str = "your-email@company.com"
    JIRA_TOKEN: str = "placeholder"
    JIRA_PROJECT_KEY: str = "SDLC"

    # ── Confluence (same Atlassian auth as Jira)
    CONFLUENCE_SPACE_KEY: str = "SDLC"

    # ── GitHub
    GITHUB_TOKEN: str = "placeholder"
    GITHUB_REPO: str = "your-org/your-repo"

    # ── Slack
    SLACK_BOT_TOKEN: str = "placeholder"
    SLACK_USE_MOCK: bool = False

    # ── App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
