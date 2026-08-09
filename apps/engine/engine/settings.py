"""Single source of configuration.

Nothing in the engine reads os.environ directly and nothing reads a TOML file.
If you find yourself wanting a global `config.app.get(...)` — that is the upstream
MoneyPrinterTurbo pattern and it is a bug here. Add a field below instead.
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from loguru import logger
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
    #: Which track, by filename. Empty means a different one each render, which
    #: `bgm.resolve` has always supported and nothing was able to override — the
    #: renderer called `resolve()` with no argument, so "random" was the only
    #: behaviour reachable however many tracks were on disk.
    bgm_track: str = ""

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


#: Shape of an environment variable name that may be *named by an API caller*.
#: `ModelSpec.api_key_env` is operator-supplied and reaches `named_credential`
#: below, which reads it out of the process environment and `.env` — so without a
#: constraint, registering a model with `api_key_env: "GOOGLE_CLIENT_SECRET"` and
#: `base_url` pointing anywhere sends that secret to the operator's own endpoint.
#: The suffix allowlist is what makes the reachable set "things that are an API
#: credential for a model provider" rather than "every variable in `.env`":
#: `STUDIO_SECRET_KEY` (which encrypts refresh tokens), `GOOGLE_CLIENT_SECRET` and
#: `AWS_SECRET_ACCESS_KEY` are all unreachable by name.
_CREDENTIAL_ENV_NAME = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")
CREDENTIAL_ENV_SUFFIXES = ("_API_KEY", "_TOKEN")


def _alias_spellings(alias: object) -> set[str]:
    """Every environment-variable name a `validation_alias` can be satisfied by.

    Usually a plain string. But pydantic also accepts `AliasChoices`, which holds
    several — `AliasChoices("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")` means the field
    answers to both. Nothing in `Settings` uses that today; the reason this walks
    the choices anyway is that its caller builds a **deny**-list, so an alias shape
    it does not understand fails *open*: the unrecognised spelling silently becomes
    nameable, and naming a key is the whole exfiltration route. A deny-list that
    quietly stops covering a new field is exactly the shape of the hole the
    suffix allowlist already had once.

    `AliasPath` is deliberately not walked — it addresses a position inside a
    structured payload, not an environment variable, so it has no env spelling to
    deny.
    """
    if isinstance(alias, str):
        return {alias.upper()}
    choices = getattr(alias, "choices", None)
    if choices is None:
        return set()
    return {choice.upper() for choice in choices if isinstance(choice, str)}


@lru_cache(maxsize=1)
def _own_credential_names() -> frozenset[str]:
    """Every environment variable name this process reads into `Settings`.

    Both spellings, because a field can be reached either way: `ANTHROPIC_API_KEY`
    through its `validation_alias`, and `STUDIO_PERSIST` through the `env_prefix`.
    """
    prefix = str(Settings.model_config.get("env_prefix") or "")
    names = set()
    for field_name, field in Settings.model_fields.items():
        names |= _alias_spellings(getattr(field, "validation_alias", None))
        names.add(f"{prefix}{field_name}".upper())
    return frozenset(names)


def is_credential_env_name(name: str) -> bool:
    """May an API caller name this variable as a model's key?

    Two conditions, and the second one is not obvious.

    The suffix allowlist makes the reachable set "things shaped like a provider API
    credential" — see `_CREDENTIAL_ENV_NAME` — which is what puts `STUDIO_SECRET_KEY`
    and `GOOGLE_CLIENT_SECRET` out of reach.

    But that alone still admitted `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and
    `PEXELS_API_KEY`: this app's *own* provider keys, which end in `_API_KEY` like any
    other. Registering a model with `api_key_env: "ANTHROPIC_API_KEY"` and a perfectly
    public `base_url` therefore passed every check and handed the operator's real
    Anthropic key to that endpoint — the same two-field exfiltration `api/models.py`
    exists to close, one hop shorter.

    A key this process already owns is never something a caller needs to *name*:
    leaving `api_key_env` empty is how a spec asks for the provider's own key. Naming
    one is only ever a way to route it somewhere else, so it is refused.
    """
    if not _CREDENTIAL_ENV_NAME.fullmatch(name):
        return False
    if not name.endswith(CREDENTIAL_ENV_SUFFIXES):
        return False
    return name.upper() not in _own_credential_names()


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

    The name is re-checked here and not only at the endpoint that accepts it.
    `routing.json` is a file on disk that survives restarts and is editable by
    anything with write access, so a name that never passed validation can still
    arrive at this function; the API check is the gate, this one is the wall.
    """
    if not is_credential_env_name(name):
        logger.warning(
            "refusing to read {!r} as a model credential; see is_credential_env_name", name
        )
        return ""

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
