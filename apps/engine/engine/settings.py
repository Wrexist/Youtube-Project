"""Single source of configuration.

Nothing in the engine reads os.environ directly and nothing reads a TOML file.
If you find yourself wanting a global `config.app.get(...)` — that is the upstream
MoneyPrinterTurbo pattern and it is a bug here. Add a field below instead.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_prefix="STUDIO_",
        extra="ignore",
    )

    env: Literal["development", "production"] = "development"
    database_url: str = "postgresql+asyncpg://studio:studio@localhost:5432/studio"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-only-do-not-use-in-production-000"

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./storage"
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # LLM
    llm_provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    llm_model: str = "claude-opus-4-8"
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_base_url: str = ""

    # TTS
    tts_provider: Literal["edge", "azure", "elevenlabs", "gemini"] = "edge"
    tts_voice: str = "en-US-AvaNeural"

    # YouTube
    youtube_daily_quota: int = 10_000
    google_redirect_uri: str = "http://localhost:8080/v1/auth/google/callback"

    # Guardrails. A runaway workflow is a billing incident.
    max_cost_per_video_usd: float = Field(default=8.0, gt=0)
    max_concurrent_renders: int = Field(default=2, ge=1)

    # Provider keys live unprefixed because their names are conventional.
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    pexels_api_key: str = Field(default="", validation_alias="PEXELS_API_KEY")
    pixabay_api_key: str = Field(default="", validation_alias="PIXABAY_API_KEY")
    elevenlabs_api_key: str = Field(default="", validation_alias="ELEVENLABS_API_KEY")
    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()
