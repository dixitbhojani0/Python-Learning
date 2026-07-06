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
from pathlib import Path

# Absolute path to .env — resolved relative to this file, not CWD.
# CWD-relative paths break if uvicorn/gunicorn is launched from a different directory.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    """
    Pydantic Settings reads from .env file automatically.
    Field names match exactly what's in .env.example.
    """
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),        # absolute path — always correct regardless of CWD
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",             # ignore unknown .env keys
    )

    # ── LLM
    GROQ_API_KEY: str = "placeholder"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OPENAI_API_KEY: str = "placeholder"

    # ── LangSmith Observability
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_API_KEY: str = "placeholder"
    LANGCHAIN_PROJECT: str = "ai-sdlc-assistant"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # ── Qdrant Vector DB
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "sdlc_knowledge"
    QDRANT_COLLECTION_SEMANTIC: str = "semantic_memory"

    # ── Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_TTL_SECONDS: int = 3600

    # ── Jira / Confluence credentials — forwarded to sdlc-mcp-server via MCP tool calls.
    # All Confluence, Jira, GitHub, Slack calls go through sdlc-mcp-server.
    JIRA_BASE_URL: str = "https://your-org.atlassian.net"
    JIRA_EMAIL: str = "your-email@company.com"
    JIRA_TOKEN: str = "placeholder"
    JIRA_PROJECT_KEY: str = "SDLC"
    CONFLUENCE_SPACE_KEY: str = "SDLC"

    # ── App
    APP_SECRET_KEY: str = "change_this_in_production"
    APP_ENV: str = "development"     # development | production
    LOG_LEVEL: str = "INFO"

    # ── JWT Auth (production mode)
    # Set JWT_SECRET_KEY to a strong random secret and APP_ENV=production
    # to enable JWT validation. Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    JWT_SECRET_KEY: str = "placeholder"

    # ── Session / Conversation store
    # Leave empty (or sqlite:///...) for SQLite demo mode.
    # Set to postgresql+asyncpg://user:pass@host:5432/dbname for production.
    DATABASE_URL: str = ""

    # ── SQLite path (demo mode only — ignored when DATABASE_URL is set)
    # Leave empty to use the default: <project_root>/data/sessions.db
    # Override in .env: SQLITE_PATH=/absolute/path/to/sessions.db
    SQLITE_PATH: str = ""

    # ── HITL
    HITL_TTL_SECONDS: int = 86400    # 24 hours — how long a pending approval waits in Redis

    # ── Demo tokens (role-based auth without OAuth — development only)
    DEMO_TOKEN_DEVELOPER: str = "dev_token_alice"
    DEMO_TOKEN_MANAGER: str = "manager_token_bob"
    DEMO_TOKEN_STAKEHOLDER: str = "stakeholder_token_client"

    # ── Default project
    DEFAULT_PROJECT: str = "SDLC"

    # ── MCP Server — OAuth 2.1 service account
    # Same secret as OAUTH_SERVICE_SECRET in sdlc-mcp-server/.env.
    # The client POSTs this to /oauth/token/service to get a short-lived (1hr) token.
    MCP_SERVICE_SECRET: str = "placeholder"
    MCP_SERVER_URL: str = "http://127.0.0.1:8100/mcp"

    # ── Slack Webhook (inbound events from Slack)
    # SLACK_SIGNING_SECRET: from Slack App → Basic Information → App Credentials
    # SLACK_BOT_TOKEN:      from Slack App → OAuth & Permissions → Bot User OAuth Token (xoxb-...)
    # Required scopes: app_mentions:read, chat:write, im:write
    SLACK_SIGNING_SECRET: str = ""
    SLACK_BOT_TOKEN: str = ""


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings instance.
    @lru_cache ensures .env is only read once at startup.
    """
    return Settings()


# Module-level singleton — import this everywhere
settings = get_settings()
