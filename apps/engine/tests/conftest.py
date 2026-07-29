"""Test configuration.

Stubs out engine modules that have external dependencies (FastAPI, arq, httpx,
moviepy, …) so the pure-logic tests can run without installing the full stack.
The stubs live here and not in the modules themselves to avoid polluting
production imports.
"""

from __future__ import annotations

import sys
import types

import pytest


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
    # These tests are *about* persistence, so it has to be on. The suite runs with
    # STUDIO_PERSIST=false ambiently, which really does skip writes.
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
