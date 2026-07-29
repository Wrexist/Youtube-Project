"""Setup — read what is configured, write what is missing.

This exists so that installing this app is: clone, run one script, open a browser,
paste keys, press Save. Before it, every key had to be typed into a dotfile by
hand, from a documentation page listing eleven variables of which three actually
mattered, with no feedback until a render failed nine stages later.

Three rules shape it.

**Values are written, never read back.** `GET /v1/setup` reports whether each
credential is set and the last four characters, and nothing else. CLAUDE.md's rule
is that secrets are never logged; a response body that carries them is a log entry
waiting to happen — in a proxy, in a browser cache, in a screenshot pasted into an
issue. The last four exist for one question the operator genuinely has ("did the
right key land here?") and answer nothing else.

**`.env` is edited, not regenerated.** It is the operator's file: it may carry
comments, values this screen does not manage, and an ordering they chose. Writing
it fresh from a template would silently delete all of that. Only the lines this
request names are touched, and the write is atomic, so an interrupted save cannot
leave a half-written credentials file.

**Absent means unchanged.** The screen posts only the fields someone actually
typed into. It never receives current values, so it cannot echo them back, and a
save from a partly-filled form cannot blank the keys it was never shown.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from engine.settings import get_settings

router = APIRouter(prefix="/v1/setup", tags=["setup"])


@dataclass(frozen=True)
class Credential:
    """One thing the operator can be asked for.

    `unlocks` and `without_it` are the point. A settings screen that lists
    variable names is a settings screen you need the documentation open beside;
    stating what each key buys, and what breaks without it, is what lets someone
    decide whether they need it at all.
    """

    #: The environment variable, exactly as `settings.py` reads it.
    env: str
    label: str
    #: What having it lets you do.
    unlocks: str
    #: What happens without it. Empty when nothing does.
    without_it: str
    #: Where to get one.
    url: str
    #: How long getting one realistically takes. Operators plan around this.
    effort: str
    #: The group this belongs to on the setup screen.
    group: str
    #: True when the pipeline cannot produce a video without it.
    required: bool = False
    #: Name of the settings attribute, when it differs from the lowercased env var.
    attr: str = ""

    @property
    def field(self) -> str:
        return self.attr or self.env.lower()


#: Everything the operator can set, in the order the screen shows it.
#:
#: Deliberately not every field in `Settings`. CLAUDE.md: "Don't add a settings
#: page with 40 toggles." These are credentials and connection details — the
#: things that genuinely vary per install and that nobody can guess a default
#: for. Render behaviour, model routing and scheduling all have opinionated
#: defaults and their own screens.
CREDENTIALS: tuple[Credential, ...] = (
    # ── the two that decide whether anything works at all ────────────────────
    Credential(
        env="ANTHROPIC_API_KEY",
        label="Anthropic",
        unlocks="Writes the script, the hook, the titles and the SEO package.",
        without_it="Nothing runs — the first stage of every workflow is an LLM call.",
        url="https://console.anthropic.com/settings/keys",
        effort="2 minutes",
        group="Required",
        required=True,
    ),
    Credential(
        env="PEXELS_API_KEY",
        label="Pexels",
        unlocks="Sources the stock footage every beat is cut against.",
        without_it="Renders fail at Materials — there is no footage to compose.",
        url="https://www.pexels.com/api/",
        effort="1 minute, free, instant",
        group="Required",
        required=True,
    ),
    # ── genuinely optional, and each says why it is worth it ─────────────────
    Credential(
        env="PIXABAY_API_KEY",
        label="Pixabay",
        unlocks="A second footage source, searched when Pexels comes up short.",
        without_it="One provider having a bad day is enough to fail a render.",
        url="https://pixabay.com/api/docs/",
        effort="1 minute, free",
        group="Recommended",
    ),
    Credential(
        env="OPENAI_API_KEY",
        label="OpenAI",
        unlocks="Generates thumbnail backgrounds with GPT Image.",
        without_it="Thumbnails still compose, over a flat colour instead of generated art.",
        url="https://platform.openai.com/api-keys",
        effort="2 minutes",
        group="Recommended",
    ),
    Credential(
        env="GEMINI_API_KEY",
        label="Google Gemini",
        unlocks="An alternative thumbnail background generator, and an LLM option.",
        without_it="Nothing breaks; this is only worth setting if you prefer it to OpenAI.",
        url="https://aistudio.google.com/apikey",
        effort="2 minutes",
        group="Recommended",
    ),
    # ── publishing ───────────────────────────────────────────────────────────
    Credential(
        env="GOOGLE_CLIENT_ID",
        label="Google OAuth client ID",
        unlocks="Connecting a YouTube channel, so videos can be published.",
        without_it="Everything works except publishing; you download the MP4 instead.",
        url="https://console.cloud.google.com/apis/credentials",
        effort="15 minutes — a Google Cloud project is involved",
        group="Publishing",
    ),
    Credential(
        env="GOOGLE_CLIENT_SECRET",
        label="Google OAuth client secret",
        unlocks="The other half of the OAuth client.",
        without_it="Same as above — both halves are needed together.",
        url="https://console.cloud.google.com/apis/credentials",
        effort="issued with the client ID",
        group="Publishing",
    ),
)

_BY_ENV = {c.env: c for c in CREDENTIALS}

#: Env var names are a fixed, narrow shape. Enforced rather than assumed: the
#: write path puts this string at the start of a line in the operator's `.env`,
#: so a name carrying a newline could inject an arbitrary variable.
_ENV_NAME = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")


class CredentialStatus(BaseModel):
    """One credential, as the setup screen sees it. Never carries the value."""

    env: str
    label: str
    unlocks: str
    without_it: str
    url: str
    effort: str
    group: str
    required: bool
    configured: bool
    #: The last four characters, or "". Enough to recognise a key, useless as one.
    tail: str


class SetupStatus(BaseModel):
    credentials: list[CredentialStatus]
    #: True when a video can be generated end to end right now.
    can_render: bool
    #: True when a channel is connected and a video can be published.
    can_publish: bool
    #: True when both OAuth halves are set, so "Connect YouTube" will work.
    can_connect: bool
    #: Connected channel titles, for the screen to name what it is connected to.
    channels: list[str]
    #: Required credentials that are still missing, by env var name.
    missing_required: list[str]
    #: Where `.env` is, so someone editing by hand knows which file to open.
    env_path: str
    #: True when a separate worker process is running, and will therefore keep
    #: using the keys it started with until it is restarted.
    worker_running: bool


class KeyUpdate(BaseModel):
    """A save. Only the names present here are touched.

    `dict[str, str]` rather than a field per credential so that adding one to
    `CREDENTIALS` needs no change here — the allowlist is `CREDENTIALS` itself,
    checked at write time, which keeps the two from drifting apart.
    """

    values: dict[str, str] = Field(default_factory=dict)


def env_path() -> Path:
    """The `.env` the engine actually reads.

    `Settings.model_config` lists `.env` then `../../.env`, relative to the
    working directory, because the engine is started both from the repository
    root and from `apps/engine`. Resolving it the same way here is what stops
    Save writing to a file the engine will never look at — which would present as
    a save that reported success and changed nothing.
    """
    for candidate in (Path(".env"), Path("../../.env")):
        if candidate.is_file():
            return candidate.resolve()
    # None exists yet: create it beside the repository root if that is
    # identifiable, otherwise in the working directory.
    root = Path(__file__).resolve().parents[4]
    return (root / ".env") if root.is_dir() else Path(".env").resolve()


def _tail(value: str) -> str:
    """The last four characters of a set credential.

    Not a prefix: key prefixes are shared across every key a provider issues
    (`sk-ant-`, `sk-proj-`), so they identify the provider and not the key. The
    tail is the part that differs, which is the part someone is checking.
    """
    return value[-4:] if len(value) >= 8 else ""


def _worker_running_sync() -> bool:
    """Whether a separate worker process would need restarting to see new keys.

    Best effort and deliberately cheap — this runs inside a page load. A false
    negative just means the screen omits a caveat that did not apply.

    Synchronous, and therefore never called directly from the endpoint: see
    `_worker_running` below.
    """
    try:
        from redis import Redis

        from engine.worker import probe_redis_settings

        s = probe_redis_settings()
        client = Redis(
            host=s.host, port=s.port, db=s.database, password=s.password, socket_timeout=0.4
        )
        try:
            # arq registers a health check under this key while a worker is up.
            return bool(client.exists("arq:queue:health-check"))
        finally:
            client.close()
    except Exception:  # noqa: BLE001 — no Redis is the common case, not an error
        return False


async def _worker_running() -> bool:
    """`_worker_running_sync` off the event loop.

    The sync Redis client blocks for up to `socket_timeout` when nothing answers,
    which is the *normal* case — most installs run no worker at all. Called
    directly from the handler that would have stalled every other request on this
    process for 0.4s on each load of the Setup screen, including the SSE streams
    carrying render progress.
    """
    import asyncio

    return await asyncio.to_thread(_worker_running_sync)


@router.get("")
async def status() -> SetupStatus:
    """Everything the setup screen needs, and no secret material."""
    from engine.api.publishing import CHANNELS

    s = get_settings()
    statuses: list[CredentialStatus] = []
    for cred in CREDENTIALS:
        value = str(getattr(s, cred.field, "") or "")
        statuses.append(
            CredentialStatus(
                env=cred.env,
                label=cred.label,
                unlocks=cred.unlocks,
                without_it=cred.without_it,
                url=cred.url,
                effort=cred.effort,
                group=cred.group,
                required=cred.required,
                configured=bool(value),
                tail=_tail(value),
            )
        )

    missing = [c.env for c in statuses if c.required and not c.configured]
    # `Credentials` carries an id, not a title — YouTube's channel name is not part
    # of the token exchange. The id is still worth showing: it is how someone with
    # two channels tells which one this install is bound to.
    channels = [creds.channel_id or name for name, creds in CHANNELS.items()]

    return SetupStatus(
        credentials=statuses,
        # An LLM key can come from any of three providers, so `missing_required`
        # alone would overstate the problem for someone using OpenAI or Gemini.
        can_render=bool(s.anthropic_api_key or s.openai_api_key or s.gemini_api_key)
        and bool(s.pexels_api_key or s.pixabay_api_key),
        can_publish=bool(channels),
        can_connect=bool(s.google_client_id and s.google_client_secret),
        channels=channels,
        missing_required=missing,
        env_path=str(env_path()),
        worker_running=await _worker_running(),
    )


class DiagnosticCheck(BaseModel):
    key: str
    name: str
    level: str
    detail: str
    fix: str
    command: str
    href: str


class Diagnostics(BaseModel):
    checks: list[DiagnosticCheck]
    ready: bool
    blockers: int
    warnings: int


@router.get("/diagnostics")
async def diagnostics(network: bool = True) -> Diagnostics:
    """What `scripts/doctor.py` prints, as data.

    The same checks, so the terminal and the screen cannot disagree. It existed
    only as a script, which meant the answer to "why did my render fail" lived
    behind remembering a virtualenv path — on the machine of someone who has, by
    construction, just failed to set this up.

    `network=false` skips the grounding probe, which reaches out to YouTube with a
    six-second timeout. The Setup screen loads with it off and turns it on for the
    explicit "Run checks" press.
    """
    from engine import diagnostics as diag

    report = await diag.run(include_network=network)
    return Diagnostics(
        checks=[DiagnosticCheck(**vars(c)) for c in report.checks],
        ready=report.ready,
        blockers=len(report.blockers),
        warnings=len(report.warnings),
    )


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into the dotenv at `path`, atomically.

    Existing lines are rewritten in place, keeping the operator's ordering and
    their comments. Names not already present are appended. An empty value
    removes the line rather than writing `KEY=`, because an empty assignment is
    not the same as an absent one — it is exported, and it shadows anything the
    surrounding environment would otherwise provide.

    The write goes to a temporary file in the same directory and is then renamed
    over the target, so a crash mid-write leaves the old file intact. `.env` is
    the file holding every credential this install has; truncating it is not a
    recoverable accident.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(updates)
    out: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        # A commented-out assignment stays a comment. Someone who wrote
        # `# OPENAI_API_KEY=...` disabled it on purpose, and quietly reactivating
        # it under a new value is not what Save means.
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        name = stripped.split("=", 1)[0].strip()
        if name not in remaining:
            out.append(line)
            continue
        value = remaining.pop(name)
        if value:
            out.append(f"{name}={value}")
        # else: drop the line entirely — that is what clearing means.

    appended = [f"{name}={value}" for name, value in remaining.items() if value]
    if appended:
        if out and out[-1].strip():
            out.append("")
        out.append("# Added by the Setup screen.")
        out.extend(appended)

    path.parent.mkdir(parents=True, exist_ok=True)
    # Same directory, so the rename below is on one filesystem and therefore
    # atomic. /tmp is routinely a different mount, where it would not be.
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out).rstrip("\n") + "\n")
        # Before the rename, not after: for the width of the gap otherwise, every
        # credential in the file is world-readable.
        tmp.chmod(0o600)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@router.put("/keys")
async def save_keys(body: KeyUpdate) -> SetupStatus:
    """Write credentials to `.env` and make them live in this process.

    Returns the new status rather than an acknowledgement, so the screen shows
    what is actually in force instead of what it hoped it had set.
    """
    updates: dict[str, str] = {}
    for name, value in body.values.items():
        if name not in _BY_ENV:
            # The allowlist is the point. Without it this endpoint writes
            # arbitrary variables into the process environment of the thing
            # holding every credential on the machine.
            raise HTTPException(400, f"{name} is not a credential this screen manages")
        if not _ENV_NAME.fullmatch(name):  # pragma: no cover — _BY_ENV already constrains it
            raise HTTPException(400, f"{name} is not a valid environment variable name")
        cleaned = value.strip()
        if "\n" in cleaned or "\r" in cleaned:
            # A newline in a value ends the assignment and starts a new one, so
            # this is the injection the allowlist above does not cover.
            raise HTTPException(400, f"{name} contains a line break")
        updates[name] = cleaned

    if not updates:
        return await status()

    path = env_path()
    try:
        write_env(path, updates)
    except OSError as exc:
        raise HTTPException(500, f"could not write {path}: {exc.strerror or exc}") from exc

    # `os.environ` wins over the dotenv in pydantic-settings, so a variable that
    # was already exported would keep its old value however the file is rewritten
    # — Save would report success and change nothing until the next full restart.
    for name, value in updates.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)

    get_settings.cache_clear()
    # The encryption key is derived once and cached; if STUDIO_SECRET_KEY were
    # ever managed here the cipher would otherwise keep using the old one.
    from engine import crypto

    crypto.reset_cache()

    # Names only. The whole design of this module is that values do not reach a
    # log line, and "saved OPENAI_API_KEY" is the useful half anyway.
    logger.info("setup: wrote {} to {}", ", ".join(sorted(updates)), path)
    return await status()
