"""Database session and engine.

One async engine per process, created lazily so importing the app never opens a
connection — the tests, the CLI and `--help` all need to import without a
database running.

`sessionmaker` rather than a global session: a session is a unit of work, not a
connection pool, and sharing one across concurrent jobs is how you get
`InterfaceError: another operation is in progress`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from engine.settings import get_settings


async def ensure_schema() -> str:
    """Create any missing tables. Called at startup.

    Alembic remains the source of truth for *migrations* — this only creates what
    is absent, so an existing database is never touched and a schema change still
    needs a revision. What it buys is that a fresh clone runs without anyone
    remembering `alembic upgrade head` first, which is the single most common way
    a first run fails.

    Returns a short description of what it found, for the startup log.
    """
    from sqlalchemy import inspect

    from engine.tables import Base

    async with engine().begin() as conn:
        existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        expected = set(Base.metadata.tables)
        missing = expected - existing

        if not missing:
            return f"schema present ({len(expected)} tables)"

        await conn.run_sync(Base.metadata.create_all)

        if not existing:
            # A schema built from metadata is at head by construction, but Alembic
            # has no way to know that — so `alembic upgrade head`, which README,
            # SETUP and CLAUDE.md all document, replayed the initial revision on top
            # of the tables that already existed and died with
            # `table channel_launches already exists`. Every machine that had
            # started the app once hit it. Stamping closes the gap.
            #
            # Same connection and same transaction as `create_all` deliberately: on
            # a crash in between, a stamped-but-empty or created-but-unstamped
            # database is exactly the state this is meant to prevent. (`command.stamp`
            # would open its own connection unless handed one through
            # `cfg.attributes`, so the row is written directly.)
            stamped = await conn.run_sync(_stamp_head)
            if stamped:
                return f"created schema ({len(missing)} tables) at {stamped}"

    if existing:
        # Some tables were there and some were not: a revision was probably added
        # without being applied. Creating the gap keeps the app up, but say so.
        #
        # Not stamped, deliberately — unlike the fresh case there may be a genuinely
        # pending revision here, and claiming head would make `upgrade` skip it.
        return f"created {len(missing)} missing table(s) — consider `alembic upgrade head`"
    return f"created schema ({len(missing)} tables)"


def _stamp_head(conn) -> str | None:
    """Record the head revision in `alembic_version`. Returns it, or None.

    Never raises: this runs during startup, and a missing `alembic.ini` — which is
    the case if the engine is ever installed as a wheel without it — must degrade to
    an unstamped schema, not a boot failure. The cost of returning None is that
    `alembic upgrade head` needs `stamp head` first, which is the status quo.
    """
    from pathlib import Path

    from sqlalchemy import text

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini.is_file():
        return None

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(Config(str(ini))).get_current_head()
    except Exception:  # noqa: BLE001 — see the docstring; boot must survive this
        return None
    if not head:
        return None

    conn.execute(
        text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
    )
    # Empty-guarded rather than unconditional: `create_all` above only ran because
    # tables were missing, but the version table is not one of `Base.metadata`'s, so
    # it can outlive a dropped schema and a second row would make Alembic ambiguous
    # about where it is.
    if conn.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar() == 0:
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})
    return head


@lru_cache(maxsize=1)
def engine() -> AsyncEngine:
    url = get_settings().database_url

    if url.startswith("sqlite"):
        # SQLite has no connection pool worth configuring and rejects the
        # Postgres-shaped arguments below outright.
        _ensure_parent_dir(url)
        return create_async_engine(url, future=True)

    return create_async_engine(
        url,
        # A render holds no connection, but a burst of jobs starting together
        # will. Ten is generous for one box and bounded enough to notice a leak.
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,  # a recycled container leaves dead sockets in the pool
        future=True,
    )


def _ensure_parent_dir(url: str) -> None:
    """Create the directory a SQLite file lives in.

    The default is `./storage/studio.db` and `storage/` is gitignored, so on a
    fresh clone it does not exist and SQLAlchemy fails with a bare
    "unable to open database file" that says nothing about why.
    """
    from pathlib import Path

    path = url.split("///", 1)[-1].split("?", 1)[0]
    if path and path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """A unit of work. Commits on success, rolls back on any exception.

    `expire_on_commit=False` above is what lets a caller keep reading attributes
    off a returned ORM object after this context exits — without it every read
    after commit triggers a refresh against a closed session.
    """
    async with session_factory()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def dispose() -> None:
    """Close the pool. Called on app shutdown so tests do not leak connections."""
    if engine.cache_info().currsize:
        await engine().dispose()
    engine.cache_clear()
    session_factory.cache_clear()
