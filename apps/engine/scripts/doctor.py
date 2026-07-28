"""Check everything, and say exactly what to do about whatever is missing.

    apps/engine/.venv/bin/python apps/engine/scripts/doctor.py

Every check answers three questions: is it working, does it *have* to work, and
what is the single next action if it does not. A check that just says "FAIL" has
made the situation worse, so none of them do.

Exit code is 0 unless something **required** is broken, which makes this usable as
a pre-flight in a script or a container healthcheck.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
REPO = ENGINE.parents[1]
sys.path.insert(0, str(ENGINE))

os.environ.setdefault("STUDIO_PERSIST", "false")  # never write while diagnosing

# The checks deliberately provoke failures, and each one logs. That noise is the
# opposite of what this command is for — the report below says it better.
try:
    from loguru import logger

    logger.remove()
except ImportError:
    pass

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    GREEN = YELLOW = RED = DIM = RESET = ""

# What each capability needs, so a failure names the feature that stops working
# rather than the library that failed to import.
_results: list[tuple[str, str, str, str]] = []  # (level, name, detail, fix)


def ok(name: str, detail: str = "") -> None:
    _results.append(("ok", name, detail, ""))


def warn(name: str, detail: str, fix: str) -> None:
    _results.append(("warn", name, detail, fix))


def fail(name: str, detail: str, fix: str) -> None:
    _results.append(("fail", name, detail, fix))


# ── checks ──────────────────────────────────────────────────────────────────


def check_python() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        fail("Python", f"{major}.{minor}", "Install Python 3.11 or newer.")
    else:
        ok("Python", f"{major}.{minor}")


def check_imports() -> None:
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
        fail(
            "Python packages",
            f"{len(missing)} missing: {', '.join(missing)}",
            'cd apps/engine && .venv/bin/python -m pip install -e ".[dev]"',
        )
    else:
        ok("Python packages", f"{len(required)} core imports")


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        ok("ffmpeg", "on PATH")
        return
    try:
        import imageio_ffmpeg

        imageio_ffmpeg.get_ffmpeg_exe()
        ok("ffmpeg", "bundled with imageio-ffmpeg")
    except Exception:
        fail(
            "ffmpeg",
            "not found — every render will fail",
            "apt install ffmpeg   (or: brew install ffmpeg)",
        )


def check_font() -> None:
    from engine.services import fonts

    try:
        ok("Subtitle font", str(fonts.resolve()))
    except RuntimeError:
        fail(
            "Subtitle font",
            "none found — renders fail when burning subtitles",
            "apt install fonts-dejavu-core, or set STUDIO_SUBTITLE_FONT to a .ttf",
        )


async def check_database() -> None:
    from engine import db
    from engine.settings import get_settings

    url = get_settings().database_url
    kind = "SQLite" if url.startswith("sqlite") else "Postgres"
    try:
        from sqlalchemy import text

        async with db.engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        ok("Database", f"{kind} reachable")
    except Exception as exc:
        if kind == "SQLite":
            fail(
                "Database", f"SQLite unusable: {type(exc).__name__}", "Check ./storage is writable."
            )
        else:
            fail(
                "Database",
                f"Postgres unreachable ({type(exc).__name__})",
                "docker compose up -d   — or unset STUDIO_DATABASE_URL to use SQLite.",
            )
    finally:
        await db.dispose()


async def check_redis() -> None:
    from engine.settings import get_settings

    try:
        from redis.asyncio import from_url

        client = from_url(get_settings().redis_url)
        await client.ping()
        await client.aclose()
        ok("Redis", "reachable — renders run in the worker")
    except Exception:
        warn(
            "Redis",
            "not reachable — renders run inside the API instead",
            "Optional. `docker compose up -d` to run them in a worker so a "
            "restart cannot kill a render.",
        )


def check_keys() -> None:
    from engine.settings import get_settings

    s = get_settings()

    if s.anthropic_api_key or s.openai_api_key or s.gemini_api_key:
        which = [
            n
            for n, v in (
                ("Anthropic", s.anthropic_api_key),
                ("OpenAI", s.openai_api_key),
                ("Gemini", s.gemini_api_key),
            )
            if v
        ]
        ok("LLM key", ", ".join(which))
    else:
        fail(
            "LLM key",
            "none set — the script and SEO stages cannot run",
            "Easiest: `npm start`, then paste it at http://localhost:3000/setup. "
            "Or put ANTHROPIC_API_KEY in .env (console.anthropic.com — 2 min), "
            "or route every task to Ollama on the Models screen.",
        )

    if s.pexels_api_key or s.pixabay_api_key:
        which = [n for n, v in (("Pexels", s.pexels_api_key), ("Pixabay", s.pixabay_api_key)) if v]
        ok("Stock footage", ", ".join(which))
    else:
        fail(
            "Stock footage",
            "no key — MaterialsStage raises, so no video renders",
            "PEXELS_API_KEY from pexels.com/api (free, instant) — paste it at "
            "http://localhost:3000/setup. Add PIXABAY_API_KEY too so one "
            "provider failing is not fatal.",
        )

    if s.google_client_id and s.google_client_secret:
        ok("Google OAuth", "configured")
    else:
        warn(
            "Google OAuth",
            "not configured — everything works except publishing",
            "See SETUP.md step 3, then paste the client ID and secret at "
            "http://localhost:3000/setup and press Connect YouTube. Needs a "
            "Google Cloud project; allow ~15 min.",
        )

    _check_channel_key(s)


def _check_channel_key(s) -> None:
    """The key that encrypts refresh tokens.

    Worth its own check because the failure is silent and total: `.env.example`
    shipped a placeholder key and `setup.sh` copies that file, so an install could
    look completely healthy while protecting channel credentials with a value that
    is published in this repository.
    """
    from pathlib import Path

    from engine.crypto import KEY_FILE
    from engine.settings import DEV_SECRET_KEY, PLACEHOLDER_SECRETS

    # DEV_SECRET_KEY is the field's own default, so seeing it means nobody set the
    # variable at all — the normal, correct state. Any *other* placeholder can only
    # have come from a .env, which is the case worth a warning.
    if s.secret_key in PLACEHOLDER_SECRETS - {DEV_SECRET_KEY}:
        warn(
            "Channel encryption",
            "STUDIO_SECRET_KEY in .env is a placeholder published in this repository",
            f"Comment that line out. A random key is generated at storage/{KEY_FILE} "
            "instead. If you connected a channel while it was set, reconnect it.",
        )
        return

    if s.secret_key != DEV_SECRET_KEY:
        # The same 32-character floor `crypto._resolve_secret` enforces. Without
        # this the doctor printed a green tick for a key that makes every channel
        # operation raise RuntimeError — which is the exact opposite of its job.
        if len(s.secret_key or "") < 32:
            fail(
                "Channel encryption",
                f"STUDIO_SECRET_KEY is {len(s.secret_key or '')} characters; 32 are required",
                "Lengthen it, or comment the line out and let a random key be "
                f"generated at storage/{KEY_FILE}.",
            )
            return
        ok("Channel encryption", "using STUDIO_SECRET_KEY")
        return

    path = Path(s.storage_root) / KEY_FILE
    if path.is_file():
        ok("Channel encryption", f"generated key at {path} — back this up")
    else:
        ok("Channel encryption", "a key is generated when you connect a channel")


async def check_grounding() -> None:
    """The first stage of the only workflow. Blocked here means nothing runs."""
    from engine.research import keywords
    from engine.settings import get_settings

    phrases, failure = await keywords.suggest_with_failures("bridges", expand=False, timeout=6.0)
    if phrases:
        ok("Keyword grounding", f"YouTube autocomplete answered ({len(phrases)} phrases)")
    elif not failure:
        ok("Keyword grounding", "reachable (no suggestions for the probe term)")
    elif get_settings().keyword_api_url:
        warn(
            "Keyword grounding",
            f"autocomplete blocked ({failure}) — the keyed fallback will be used",
            "",
        )
    else:
        warn(
            "Keyword grounding",
            f"autocomplete unreachable: {failure}",
            "Common on datacenter/VPN networks. Jobs will fail at the first "
            "stage. Set KEYWORD_API_URL for a fallback, or run from a home network.",
        )


def check_env_file() -> None:
    if (REPO / ".env").exists():
        ok(".env", "present")
    else:
        warn(
            ".env",
            "not created yet — defaults are in use",
            "cp .env.example .env   then fill in the keys above.",
        )


# ── report ──────────────────────────────────────────────────────────────────


async def main() -> int:
    check_python()
    check_imports()
    check_ffmpeg()
    check_env_file()

    # Anything below needs the package importable.
    try:
        check_font()
        await check_database()
        await check_redis()
        check_keys()
        await check_grounding()
    except ImportError as exc:
        fail("engine package", str(exc), 'cd apps/engine && .venv/bin/pip install -e ".[dev]"')

    width = max(len(name) for _, name, _, _ in _results) + 2
    print()
    for level, name, detail, _ in _results:
        mark = {"ok": f"{GREEN}✓{RESET}", "warn": f"{YELLOW}!{RESET}", "fail": f"{RED}✗{RESET}"}[
            level
        ]
        print(f" {mark} {name:<{width}} {DIM}{detail}{RESET}")

    blockers = [r for r in _results if r[0] == "fail"]
    warnings = [r for r in _results if r[0] == "warn"]

    if blockers:
        print(f"\n{RED}{len(blockers)} thing(s) must be fixed before anything runs:{RESET}")
        for _, name, _, fix in blockers:
            print(f"   {name}: {fix}")

    if warnings:
        print(f"\n{YELLOW}{len(warnings)} optional:{RESET}")
        for _, name, _, fix in warnings:
            if fix:
                print(f"   {name}: {fix}")

    if not blockers:
        print(f"\n{GREEN}Ready.{RESET} Start it with:")
        print(f"   {DIM}npm run dev{RESET}                     web on :3000")
        print(f"   {DIM}apps/engine/.venv/bin/python -m uvicorn engine.main:app --port 8080{RESET}")

    print()
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
