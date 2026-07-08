from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    avatar_provider: str = "anam"  # which provider /api/v1/session hands out
    persona_name: str = "Deniel"
    active_scenes: int = 2  # how many of the scripted scenes the demo serves

    anam_api_key: str = ""
    anam_avatar_id: str = ""
    anam_avatar_model: str = "cara-4"
    anam_voice_id: str = ""


settings = Settings()
