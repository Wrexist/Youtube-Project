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

    # Storage. `ObjectStore` is local-only today — there is no S3 implementation,
    # so "s3" is deliberately not accepted rather than silently writing to local
    # disk and losing everything when the container recycles. The interface in
    # storage.py is the extension point; add the backend there and widen this.
    storage_backend: Literal["local"] = "local"
    storage_root: str = "./storage"

    # No LLM settings. Model selection, provider and base URL all come from the
    # per-task routing table in engine/models.py — `ModelSpec.base_url` is what the
    # client actually reads — surfaced on the Models screen and at /v1/models.
    # `llm_provider`, `llm_model`, `llm_fast_model` and `llm_base_url` used to live
    # here and were read by nothing; `llm_model` additionally made /health report a
    # model the engine would never call.

    # TTS. Only Edge is implemented — `_synthesize` in workflows/media.py calls it
    # directly. This was a four-value Literal whose other three values produced
    # Edge audio *and recorded the wrong provider in provenance*, which corrupts
    # the Phase 8 attribution trail that non-negotiable #2 exists to protect.
    tts_provider: Literal["edge"] = "edge"
    tts_voice: str = "en-US-AvaNeural"

    # Render. Ken Burns alternates by default — a whole video pushing the same
    # direction develops a rhythm the viewer starts to anticipate.
    subtitle_font: str = ""
    ken_burns: Literal["none", "in", "out", "alternate"] = "alternate"
    # Hard cuts by default. Fast-cut faceless video does not dissolve between
    # shots — it reads as a slideshow. Non-zero gives a true crossfade.
    transition_fade_s: float = Field(default=0.0, ge=0.0, le=2.0)

    # Background music. Off by default and empty by design: nothing ships with
    # music, and publishing over an unlicensed bed is a copyright strike.
    # See KNOWN-ISSUES.md §3.3.
    bgm_enabled: bool = False
    bgm_dir: str = ""
    bgm_volume: float = Field(default=0.12, gt=0.0, le=1.0)

    # YouTube
    youtube_daily_quota: int = 10_000
    # Unprefixed alias to match GOOGLE_CLIENT_ID/SECRET below. Without it this read
    # STUDIO_GOOGLE_REDIRECT_URI while .env.example documented GOOGLE_REDIRECT_URI,
    # so the value was silently ignored and OAuth failed with redirect_uri_mismatch.
    google_redirect_uri: str = Field(
        default="http://localhost:8080/v1/auth/google/callback",
        validation_alias="GOOGLE_REDIRECT_URI",
    )

    # Guardrails. A runaway workflow is a billing incident.
    max_cost_per_video_usd: float = Field(default=8.0, gt=0)
    max_concurrent_renders: int = Field(default=2, ge=1)

    # Provider keys live unprefixed because their names are conventional.
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    pexels_api_key: str = Field(default="", validation_alias="PEXELS_API_KEY")
    pixabay_api_key: str = Field(default="", validation_alias="PIXABAY_API_KEY")
    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()
