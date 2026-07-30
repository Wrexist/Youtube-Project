"""Single source of configuration.

Nothing in the engine reads os.environ directly and nothing reads a TOML file.
If you find yourself wanting a global `config.app.get(...)` — that is the upstream
MoneyPrinterTurbo pattern and it is a bug here. Add a field below instead.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The placeholder `secret_key` ships with.
DEV_SECRET_KEY = "dev-only-do-not-use-in-production-000"

#: Every value that looks like a key but is published in this repository. `crypto`
#: treats all of them as "unset" and generates a real per-install key instead.
#:
#: The second one is the one that actually bit: `.env.example` set
#: STUDIO_SECRET_KEY to it, and `scripts/setup.sh` copies that file to `.env`. So
#: the default above was never even reached — every install ran with a 47-character
#: key that is sitting in the repository, encrypting YouTube refresh tokens, which
#: are permanent access to the channel. It is long enough to pass a length check,
#: which is exactly why a length check was not enough.
PLACEHOLDER_SECRETS = frozenset(
    {
        DEV_SECRET_KEY,
        "change-me-32-bytes-minimum-for-token-encryption",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_prefix="STUDIO_",
        extra="ignore",
    )

    env: Literal["development", "production"] = "development"
    # Off only for tests and throwaway instances. With this false a restart loses
    # every job, channel, booking and — worst — the day's quota spend, so the next
    # upload silently overruns Google's ceiling.
    persist: bool = True
    # SQLite by default so a fresh clone runs with no Docker and no database
    # server — the first thing anyone does should not be infrastructure. Every
    # query here is ordinary SQLAlchemy and the same suite passes on both, so
    # moving up is one env var:
    #   STUDIO_DATABASE_URL=postgresql+asyncpg://studio:studio@localhost:5432/studio
    # Use Postgres for anything real: SQLite serialises writers, which a worker
    # plus an API will contend on.
    database_url: str = "sqlite+aiosqlite:///./storage/studio.db"
    redis_url: str = "redis://localhost:6379/0"
    # Left as the shipped placeholder, `crypto` generates a real random key per
    # install and keeps it in storage/.secret_key. It is not a usable key itself —
    # it is in this repository, so anything encrypted under it is public.
    secret_key: str = DEV_SECRET_KEY

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

    # Thumbnail backgrounds. "auto" resolves to whichever of OPENAI_API_KEY /
    # GEMINI_API_KEY is set, so nobody has to name a provider — and with neither,
    # thumbnails compose over a flat background instead of failing. GPT Image wins a
    # tie: dearer, but the thumbnail is what decides whether the video gets clicked.
    image_provider: Literal["auto", "openai", "gemini", "none"] = "auto"

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
    # Where the web app lives, from the browser's point of view. Only used to send
    # someone back to the Setup screen after Google's consent page. This was
    # hardcoded to localhost:3000 in the OAuth callback, so on any install that is
    # not the developer's laptop Google returned the operator to a dead URL and a
    # channel that had in fact connected looked like it had failed.
    web_url: str = "http://localhost:3000"

    # Guardrails. A runaway workflow is a billing incident.
    max_cost_per_video_usd: float = Field(default=8.0, gt=0)
    max_concurrent_renders: int = Field(default=2, ge=1)

    # Provider keys live unprefixed because their names are conventional.
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    # Optional second grounding source. YouTube autocomplete and DuckDuckGo are
    # unauthenticated and routinely block datacenter IPs, so on a cloud host they
    # can both fail and take the only workflow's first stage with them. Any
    # endpoint answering GET {url}?q=seed with a JSON list of keywords works.
    keyword_api_url: str = Field(default="", validation_alias="KEYWORD_API_URL")
    keyword_api_key: str = Field(default="", validation_alias="KEYWORD_API_KEY")

    pexels_api_key: str = Field(default="", validation_alias="PEXELS_API_KEY")
    pixabay_api_key: str = Field(default="", validation_alias="PIXABAY_API_KEY")
    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def named_credential(name: str) -> str:
    """The value of a credential whose *variable name* is configuration.

    The one thing a field above cannot express. `ModelSpec.api_key_env` lets an
    operator point a registered model at its own key — `GROQ_API_KEY`,
    `OPENROUTER_API_KEY`, whatever they called it — and the set of names is open
    by construction, so there is nothing to declare. This is the exception the
    module docstring's "add a field below instead" does not cover, and it lives
    here rather than in the caller so `os.environ` still has exactly one reader.

    Both sources, in the same precedence pydantic-settings uses: the process
    environment wins, then `.env`. Reading only the former would miss a key that
    `scripts/setup.sh` wrote to `.env` and nobody exported, which is the normal
    way keys arrive here.
    """
    value = os.environ.get(name)
    if value:
        return value
    for env_file in Settings.model_config.get("env_file") or ():
        path = Path(env_file)
        if path.is_file():
            found = dotenv_values(path).get(name)
            if found:
                return found
    return ""
