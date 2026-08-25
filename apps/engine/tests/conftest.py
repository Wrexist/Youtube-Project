"""Test configuration.

Stubs out engine modules that have external dependencies (FastAPI, arq, httpx,
moviepy, …) so the pure-logic tests can run without installing the full stack.
The stubs live here and not in the modules themselves to avoid polluting
production imports.
"""

from __future__ import annotations

import sys
import types

import httpx2
import pytest

# Before any test module (or respx) imports `httpx`: the anthropic 1.x SDK moved
# its HTTP layer to `httpx2`, so respx — which patches `httpx` — silently stopped
# seeing the SDK's requests and every mocked Anthropic call escaped to the real
# API. Aliasing makes `import httpx` resolve to `httpx2` process-wide, so respx
# and the SDK are back on the same transport. Raises loudly if something imported
# `httpx` first, which is the failure mode we want visible.
httpx2.alias_httpx()


def _stub(name: str, **attrs: object) -> None:
    """Install a minimal module stub under *name* if not already importable."""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# `engine.providers.llm` is deliberately NOT stubbed.
#
# It used to be, with a `types.ModuleType` installed here before collection — which
# meant the real module, the four transports and the JSON-retry loop, was never
# imported by a single test, while the docs claimed otherwise. Nothing required the
# stub: importing the module opens no connection and reads no key (the anthropic SDK
# import is inside the transport, and `get_settings()` tolerates an empty `.env`), so
# the only thing the stub bought was a coverage hole. See test_llm.py.
#
# `engine.providers.images` is likewise real — its PlaceholderProvider works without
# credentials.


# ── never the developer's database ──────────────────────────────────────────
#
# `Settings.database_url` defaults to `sqlite+aiosqlite:///./storage/studio.db`,
# and `persist` defaults to True. Only CI set `STUDIO_PERSIST=false`, so a plain
# `pytest` run locally wrote to the real file, and two things went wrong there —
# both silently:
#
#   * Every `POST /v1/jobs` in the endpoint tests saved a row, and the *next* run's
#     lifespan restored them. That is how `test_publishing_without_a_connected_
#     channel_is_refused` came to fail with "already published as job pub": a `pub`
#     job left behind by an earlier run, restored into JOBS, matching the `src` the
#     test had just built. Six tests failed for reasons that were not in any of them.
#   * SQLite serialises writers, so each of those writes sat out the 5s busy
#     timeout. The two endpoint modules took 152s between them.
#
# Autouse rather than opt-in: the failure mode is a test that passes while quietly
# corrupting the one after it, which is exactly what nobody remembers to opt into.


@pytest.fixture(autouse=True)
def scratch_state(tmp_path, monkeypatch):
    """Persistence off, and pointed at a throwaway file even so.

    Both, not either. `persist=false` skips the writes, but anything calling
    `db.engine()` directly still resolves a URL — and the default one is the real
    database. Tests that are *about* persistence (`database`, `fresh_sqlite`)
    override both afterwards; a fixture set up later is torn down first, so their
    values win for the test and this one is restored around it.
    """
    from engine import db
    from engine.settings import Settings, get_settings

    monkeypatch.setenv("STUDIO_PERSIST", "false")
    monkeypatch.setenv("STUDIO_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'scratch.db'}")

    # And never the developer's `.env`, for the same reason and with a sharper
    # edge. `Settings.model_config` names `(".env", "../../.env")`, so every
    # `Settings()` in the suite read the operator's real credentials file —
    # against which `monkeypatch.delenv("PEXELS_API_KEY")` does nothing at all,
    # because the value is not coming from the process environment.
    #
    # That made a group of tests pass only on a machine with no keys configured.
    # `test_not_ready_when_there_is_no_footage_source` deletes both footage keys
    # and asserts `can_render is False`; with a real Pexels key in `.env` it
    # fails. Which would be a curiosity, except `scripts/setup.ps1` runs the test
    # suite — so the moment anyone saved a key on the Setup screen, re-running
    # Install Studio.cmd started failing, on an install that was working.
    #
    # A path in tmp_path rather than an empty tuple: `settings.credential_value`
    # iterates this to read a key `.env` has and the environment does not, and
    # emptying it would remove that lookup from the suite rather than isolate it.
    monkeypatch.setitem(Settings.model_config, "env_file", (str(tmp_path / ".env"),))
    # Both caches are read-through-once: `get_settings` for the URL, `db.engine` for
    # the connection built from it. Leaving either warm would hand this test the
    # previous one's database.
    get_settings.cache_clear()
    db.engine.cache_clear()
    db.session_factory.cache_clear()

    yield

    get_settings.cache_clear()


# ── a real database ─────────────────────────────────────────────────────────
#
# Lives here rather than in test_persistence.py because the quota tests in
# test_security.py need it too: the cross-process ceiling bug only reproduces
# against a shared database, which is the whole point of it.


@pytest.fixture
async def database(tmp_path, monkeypatch):
    """A migrated, empty database, torn down after each test.

    `STUDIO_TEST_DATABASE_URL` if set, otherwise an on-disk SQLite file — on disk
    rather than `:memory:` precisely because the point is to prove data outlives
    the process that wrote it. CI sets the Postgres URL.
    """
    import os

    from engine import db
    from engine.settings import get_settings
    from engine.tables import Base

    url = os.environ.get("STUDIO_TEST_DATABASE_URL") or (
        f"sqlite+aiosqlite:///{tmp_path / 'studio.db'}"
    )
    get_settings.cache_clear()
    await db.dispose()
    monkeypatch.setenv("STUDIO_DATABASE_URL", url)
    # These tests are *about* persistence, so it has to be on — overriding the
    # `scratch_state` fixture above, which turns it off for everything else.
    monkeypatch.setenv("STUDIO_PERSIST", "true")

    async with db.engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield url

    await db.dispose()
    get_settings.cache_clear()


# ── source assertions ───────────────────────────────────────────────────────
#
# Several guards here assert on the shape of the source rather than on behaviour,
# because the thing being prevented is structural: a module reaching for
# `os.environ`, a shared connection pool being disposed inside a request. Those
# guards all hit the same trap — the comment *documenting* the rule matches the
# pattern the rule looks for, so explaining yourself breaks the test. A rule that
# cannot be explained in a comment is one people work around silently.


def code_only(source: str) -> str:
    """`source` with comments and string literals removed.

    Tokenised rather than regexed: stripping everything after a `#` also mangles
    a `#` inside a string, and stripping quotes by pattern goes wrong on nested
    and triple quotes. `tokenize` already knows the difference.
    """
    import io
    import tokenize

    kept = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                kept.append(token.string)
    except (tokenize.TokenError, IndentationError):  # pragma: no cover
        return source  # unparseable: fall back to checking everything
    return " ".join(kept)
