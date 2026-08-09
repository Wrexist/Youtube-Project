"""Is this install working, and if not, what is the single next action?

Every check answers three questions: is it working, does it *have* to work, and
what is the one thing to do if it does not. A check that just says FAIL has made
the situation worse, so none of them do.

This lives in the package rather than in `scripts/doctor.py` because it has two
callers now. The CLI still prints it; the Setup screen renders it, so nobody has
to open a terminal and remember a virtualenv path to find out why a render failed.
`scripts/doctor.py` is a thin front end over `run()`.

Two constraints that shape the code:

**No module-level state.** The old version accumulated into a global list, which
is fine for a process that exits immediately afterwards and wrong for a server
that answers this endpoint repeatedly — the second call would have reported the
first call's results as well.

**Nothing here writes.** The CLI used to set `STUDIO_PERSIST=false` at import to
be sure of that. Importing a module with that side effect into the running engine
would have reconfigured it, so instead the checks are read-only by construction:
`SELECT 1`, a Redis ping, an import, a file stat.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Level = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    """One capability, and what to do when it is not there."""

    #: Stable identifier, so the UI can key off it without matching on prose.
    key: str
    name: str
    level: Level
    #: What is true right now.
    detail: str = ""
    #: The single next action. Empty when there is nothing to do.
    fix: str = ""
    #: A command to run, when the fix is one. Rendered as copyable text.
    command: str = ""
    #: Where to go to fix it *inside the app*, when that is possible. The point of
    #: showing diagnostics in the UI is that most fixes should not need a terminal.
    href: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.level == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warn"]

    @property
    def ready(self) -> bool:
        return not self.blockers


# ── checks ──────────────────────────────────────────────────────────────────
#
# Each takes the report and appends to it. Deliberately not returning values and
# composing: a check that raises should not take the rest of the report with it,
# and `run()` below guards each one individually.


def _check_python(report: Report) -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        report.add(
            Check(
                key="python",
                name="Python",
                level="fail",
                detail=f"{major}.{minor}",
                fix="Install Python 3.11 or newer.",
            )
        )
    else:
        report.add(Check(key="python", name="Python", level="ok", detail=f"{major}.{minor}"))


def _check_imports(report: Report) -> None:
    required = {
        "fastapi": "the API",
        "sqlalchemy": "persistence",
        "moviepy": "rendering",
        "edge_tts": "narration",
        "scipy": "the analytics gate",
    }
    missing = []
    for module, purpose in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{module} ({purpose})")

    if missing:
        report.add(
            Check(
                key="packages",
                name="Python packages",
                level="fail",
                detail=f"{len(missing)} missing: {', '.join(missing)}",
                fix="Re-run the setup script — it installs everything.",
                command='cd apps/engine && .venv/bin/python -m pip install -e ".[dev]"',
            )
        )
    else:
        report.add(
            Check(
                key="packages",
                name="Python packages",
                level="ok",
                detail=f"{len(required)} core imports",
            )
        )


def _check_tts_trust(report: Report) -> None:
    """Whether the voiceover stage can verify Azure's certificate.

    Reported separately from the other network checks because it is the one that
    fails *late*: voiceover is stage 9 of 17, so a TLS problem here surfaces after
    the research and the whole script chain have already been paid for. Saying so
    up front costs nothing.
    """
    import os

    from engine.workflows.media import _CA_BUNDLE_VARS, _trust_extra_cas

    configured = next((os.environ[v] for v in _CA_BUNDLE_VARS if os.environ.get(v)), None)
    bundle = _trust_extra_cas()

    if configured and not bundle:
        # The three "no bundle applied" outcomes are not the same thing.
        # `_trust_extra_cas()` returns None for *no bundle configured* and also for
        # a configured path that is missing or that upstream's private `_SSL_CTX`
        # no longer exists to receive — and reporting all three as healthy meant a
        # proxy-dependent install passed diagnostics and then failed at voiceover.
        report.add(
            Check(
                key="tts_trust",
                name="Voiceover TLS",
                level="fail",
                detail=f"{configured} is configured but was not applied",
                fix=(
                    "Check the path exists and is readable. If it does, edge-tts has "
                    "moved its SSL context and `_trust_extra_cas` needs updating."
                ),
            )
        )
        return

    if bundle:
        report.add(
            Check(
                key="tts_trust",
                name="Voiceover TLS",
                level="ok",
                detail=f"edge-tts also trusting {bundle}",
            )
        )
        return

    # No hand-configured bundle, which is the normal case. What matters then is
    # whether the platform verifier is in play, because that is what makes an
    # intercepting antivirus or proxy work without anyone exporting anything.
    from engine import tls

    report.add(
        Check(
            key="tts_trust",
            name="Certificate trust",
            level="ok" if tls.STATUS == "using the system certificate store" else "warn",
            detail=tls.STATUS,
            fix=(
                "Outbound calls verify against certifi's fixed bundle, which does not "
                "include the root your antivirus or proxy re-signs with. Reinstall the "
                "engine so truststore is present: see Install Studio.cmd."
            ),
        )
    )


def _check_ffmpeg(report: Report) -> None:
    if shutil.which("ffmpeg"):
        report.add(Check(key="ffmpeg", name="ffmpeg", level="ok", detail="on PATH"))
        return
    try:
        import imageio_ffmpeg

        imageio_ffmpeg.get_ffmpeg_exe()
        report.add(
            Check(key="ffmpeg", name="ffmpeg", level="ok", detail="bundled with imageio-ffmpeg")
        )
    except Exception:  # noqa: BLE001 — any failure here means no usable ffmpeg
        report.add(
            Check(
                key="ffmpeg",
                name="ffmpeg",
                level="fail",
                detail="not found — every render will fail",
                fix="Install it with your package manager.",
                command="apt install ffmpeg      # or: brew install ffmpeg",
            )
        )


def _check_font(report: Report) -> None:
    from engine.services import fonts

    try:
        report.add(Check(key="font", name="Subtitle font", level="ok", detail=str(fonts.resolve())))
    except RuntimeError:
        report.add(
            Check(
                key="font",
                name="Subtitle font",
                level="fail",
                detail="none found — renders fail when burning subtitles",
                fix="Install a font, or point STUDIO_SUBTITLE_FONT at a .ttf.",
                command="apt install fonts-dejavu-core",
            )
        )


async def _check_database(report: Report) -> None:
    from engine import db
    from engine.settings import get_settings

    url = get_settings().database_url
    kind = "SQLite" if url.startswith("sqlite") else "Postgres"
    try:
        from sqlalchemy import text

        async with db.engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        report.add(Check(key="database", name="Database", level="ok", detail=f"{kind} reachable"))
    except Exception as exc:  # noqa: BLE001 — the type varies by driver
        if kind == "SQLite":
            report.add(
                Check(
                    key="database",
                    name="Database",
                    level="fail",
                    detail=f"SQLite unusable: {type(exc).__name__}",
                    fix="Check that ./storage is writable.",
                )
            )
        else:
            report.add(
                Check(
                    key="database",
                    name="Database",
                    level="fail",
                    detail=f"Postgres unreachable ({type(exc).__name__})",
                    fix="Start it, or unset STUDIO_DATABASE_URL to fall back to SQLite.",
                    command="docker compose up -d",
                )
            )
    # Deliberately no `dispose()` here, unlike the old CLI-only version. This runs
    # inside the live API, where the engine's connection pool is shared with every
    # other request — tearing it down because someone opened the Setup screen would
    # drop connections out from under running jobs.


async def _check_redis(report: Report) -> None:
    from engine.settings import get_settings

    try:
        from redis.asyncio import from_url

        client = from_url(get_settings().redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        report.add(
            Check(
                key="redis",
                name="Redis",
                level="ok",
                detail="reachable — renders can run in a worker",
            )
        )
    except Exception:  # noqa: BLE001 — no Redis is a supported configuration
        report.add(
            Check(
                key="redis",
                name="Redis",
                level="warn",
                detail="not reachable — renders run inside the API instead",
                fix=(
                    "Optional. Running a worker means restarting the API cannot "
                    "kill a render in progress."
                ),
                command="docker compose up -d",
            )
        )


def _check_keys(report: Report) -> None:
    from engine.settings import get_settings

    s = get_settings()

    named = [
        (n, v)
        for n, v in (
            ("Anthropic", s.anthropic_api_key),
            ("OpenAI", s.openai_api_key),
            ("Gemini", s.gemini_api_key),
        )
        if v
    ]
    if named:
        report.add(
            Check(
                key="llm",
                name="Model provider",
                level="ok",
                detail=", ".join(n for n, _ in named),
            )
        )
    else:
        report.add(
            Check(
                key="llm",
                name="Model provider",
                level="fail",
                detail="none set — the script and SEO stages cannot run",
                fix="Add a key on the Setup screen, or route every task to Ollama.",
                href="/setup",
            )
        )

    footage = [n for n, v in (("Pexels", s.pexels_api_key), ("Pixabay", s.pixabay_api_key)) if v]
    if footage:
        report.add(
            Check(key="footage", name="Stock footage", level="ok", detail=", ".join(footage))
        )
    else:
        report.add(
            Check(
                key="footage",
                name="Stock footage",
                level="fail",
                detail="no key — there is nothing to cut the narration against",
                fix="Add a Pexels key on the Setup screen. Free, and instant.",
                href="/setup",
            )
        )

    if s.google_client_id and s.google_client_secret:
        report.add(
            Check(key="oauth", name="YouTube publishing", level="ok", detail="OAuth configured")
        )
    else:
        report.add(
            Check(
                key="oauth",
                name="YouTube publishing",
                level="warn",
                detail="not configured — everything works except uploading",
                fix="Optional. Needs a Google Cloud project; allow about 15 minutes.",
                href="/setup",
            )
        )

    _check_channel_key(report, s)


def _check_channel_key(report: Report, s) -> None:
    """The key that encrypts refresh tokens.

    Worth its own check because the failure is silent and total: `.env.example`
    once shipped a placeholder key and `setup.sh` copies that file, so an install
    could look completely healthy while protecting channel credentials with a
    value published in this repository.
    """
    from engine.crypto import KEY_FILE
    from engine.settings import DEV_SECRET_KEY, PLACEHOLDER_SECRETS

    # DEV_SECRET_KEY is the field's own default, so seeing it means nobody set the
    # variable at all — the normal, correct state. Any *other* placeholder can only
    # have come from a .env, which is the case worth a warning.
    if s.secret_key in PLACEHOLDER_SECRETS - {DEV_SECRET_KEY}:
        report.add(
            Check(
                key="crypto",
                name="Channel encryption",
                level="warn",
                detail="STUDIO_SECRET_KEY is a placeholder published in this repository",
                fix=(
                    f"Comment that line out of .env. A random key is generated at "
                    f"storage/{KEY_FILE} instead. Reconnect any channel you connected "
                    f"while it was set."
                ),
            )
        )
        return

    if s.secret_key != DEV_SECRET_KEY:
        if len(s.secret_key or "") < 32:
            report.add(
                Check(
                    key="crypto",
                    name="Channel encryption",
                    level="fail",
                    detail=f"STUDIO_SECRET_KEY is {len(s.secret_key or '')} characters; 32 minimum",
                    fix="Lengthen it, or comment it out and let one be generated.",
                )
            )
        else:
            report.add(
                Check(
                    key="crypto",
                    name="Channel encryption",
                    level="ok",
                    detail="using STUDIO_SECRET_KEY",
                )
            )
        return

    path = Path(s.storage_root) / KEY_FILE
    report.add(
        Check(
            key="crypto",
            name="Channel encryption",
            level="ok",
            detail=(
                f"generated key at {path} — back this up"
                if path.is_file()
                else "a key is generated when you connect a channel"
            ),
        )
    )


async def _check_grounding(report: Report) -> None:
    """The first stage of the only workflow. Blocked here means nothing runs."""
    from engine.research import keywords
    from engine.settings import get_settings

    phrases, failure = await keywords.suggest_with_failures("bridges", expand=False, timeout=6.0)
    if phrases:
        report.add(
            Check(
                key="grounding",
                name="Keyword grounding",
                level="ok",
                detail=f"YouTube autocomplete answered ({len(phrases)} phrases)",
            )
        )
    elif not failure:
        report.add(
            Check(
                key="grounding",
                name="Keyword grounding",
                level="ok",
                detail="reachable (no suggestions for the probe term)",
            )
        )
    elif get_settings().keyword_api_url:
        report.add(
            Check(
                key="grounding",
                name="Keyword grounding",
                level="warn",
                detail=f"autocomplete blocked ({failure}) — the keyed fallback will be used",
            )
        )
    else:
        report.add(
            Check(
                key="grounding",
                name="Keyword grounding",
                level="warn",
                detail=f"autocomplete unreachable: {failure}",
                fix=(
                    "Common on datacenter and VPN networks. Jobs will fail at the "
                    "first stage. Set KEYWORD_API_URL for a fallback, or run from a "
                    "home network."
                ),
            )
        )


def _check_env_file(report: Report) -> None:
    from engine.api.setup import env_path

    path = env_path()
    if path.is_file():
        report.add(Check(key="env", name="Configuration file", level="ok", detail=str(path)))
    else:
        report.add(
            Check(
                key="env",
                name="Configuration file",
                level="warn",
                detail="not created yet — built-in defaults are in use",
                fix="Saving anything on the Setup screen creates it.",
                href="/setup",
            )
        )


# ── the report ──────────────────────────────────────────────────────────────


async def run(*, include_network: bool = True) -> Report:
    """Every check, in the order they are worth reading.

    `include_network` exists for the Setup screen's automatic first load: the
    grounding probe reaches out to YouTube with a six-second timeout, which is a
    long time to hold a page render. The screen asks for it on the explicit
    "Run checks" press instead.

    Each check is guarded individually. One raising must not cost the report the
    other nine — a diagnostic tool that itself fails opaquely is the worst
    possible version of this file.
    """
    report = Report()

    checks: list = [_check_python, _check_imports, _check_ffmpeg, _check_env_file]
    checks += [_check_font, _check_database, _check_redis, _check_keys, _check_tts_trust]
    if include_network:
        checks.append(_check_grounding)

    for check in checks:
        try:
            result = check(report)
            if result is not None:
                await result
        except Exception as exc:  # noqa: BLE001 — a broken check is itself a finding
            report.add(
                Check(
                    key=getattr(check, "__name__", "check").removeprefix("_check_"),
                    name=getattr(check, "__name__", "check").removeprefix("_check_").title(),
                    level="fail",
                    detail=f"the check itself failed: {type(exc).__name__}: {exc}",
                    fix="This is a bug in Studio, not in your setup. Please report it.",
                )
            )

    return report
