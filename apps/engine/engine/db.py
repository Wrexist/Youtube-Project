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


@lru_cache(maxsize=1)
def engine() -> AsyncEngine:
    url = get_settings().database_url
    return create_async_engine(
        url,
        # A render holds no connection, but a burst of jobs starting together
        # will. Ten is generous for one box and bounded enough to notice a leak.
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,  # a recycled container leaves dead sockets in the pool
        future=True,
    )


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
