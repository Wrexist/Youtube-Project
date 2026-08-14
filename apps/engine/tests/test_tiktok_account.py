"""Storing a TikTok connection, and keeping it alive.

Access tokens last 24 hours. Everything here exists so the integration is still
working on day two — which is the failure nobody catches during setup, because
setup happens on day one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine import repository
from engine.providers import tiktok
from engine.providers.tiktok import TikTokAuthExpired, Tokens


def _tokens(**kw) -> Tokens:
    base = dict(
        access_token="at",
        refresh_token="rt",
        open_id="oid",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        refresh_expires_at=datetime.now(UTC) + timedelta(days=365),
        scope="user.info.basic,video.list",
    )
    return Tokens(**{**base, **kw})


# ── storage ─────────────────────────────────────────────────────────────────


async def test_a_connection_survives_a_restart(database):
    await repository.save_tiktok_account(_tokens(), handle="@me")

    account = await repository.load_tiktok_account()

    assert account is not None
    assert account["handle"] == "@me"
    assert account["open_id"] == "oid"
    assert account["connected"] is True


async def test_the_status_read_never_returns_a_credential(database):
    """A status endpoint has no business handling a refresh token."""
    await repository.save_tiktok_account(_tokens(), handle="@me")

    account = await repository.load_tiktok_account()

    assert account is not None
    assert "refresh_token" not in account
    assert "refresh_token_encrypted" not in account
    assert "access_token" not in account


async def test_the_refresh_token_is_encrypted_at_rest(database):
    """Non-negotiable #4. Durable access to an account is not stored in the clear."""
    from sqlalchemy import select

    from engine.db import session
    from engine.tables import TikTokAccount

    await repository.save_tiktok_account(_tokens(refresh_token="super-secret"), handle="@me")

    async with session() as db:
        row = (await db.execute(select(TikTokAccount))).scalars().one()

    assert "super-secret" not in row.refresh_token_encrypted
    assert row.refresh_token_encrypted


async def test_reconnecting_replaces_rather_than_duplicates(database):
    """Reconnecting is the normal recovery from an expired refresh token. Two rows
    would leave the loader picking between credentials with no way to know which
    is live."""
    await repository.save_tiktok_account(_tokens(open_id="first"), handle="@one")
    await repository.save_tiktok_account(_tokens(open_id="second"), handle="@two")

    account = await repository.load_tiktok_account()

    assert account is not None
    assert account["open_id"] == "second"
    assert account["handle"] == "@two"


async def test_disconnecting_forgets_the_account(database):
    await repository.save_tiktok_account(_tokens(), handle="@me")

    assert await repository.disconnect_tiktok() is True
    assert await repository.load_tiktok_account() is None
    assert await repository.disconnect_tiktok() is False


async def test_no_account_reads_as_none_rather_than_raising(database):
    assert await repository.load_tiktok_account() is None


# ── the refresh cycle ───────────────────────────────────────────────────────


async def test_a_live_token_is_returned_without_calling_tiktok(database, monkeypatch):
    called = False

    async def tripwire(_token):
        nonlocal called
        called = True

    monkeypatch.setattr(tiktok, "refresh", tripwire)
    await repository.save_tiktok_account(_tokens(access_token="live"), handle="@me")

    assert await repository.tiktok_access_token() == "live"
    assert not called


async def test_an_expiring_token_is_refreshed_before_it_dies(database, monkeypatch):
    """Inside the margin, not after it: a sweep that starts with 30 seconds left
    finishes with an invalid token."""

    async def fake_refresh(refresh_token):
        assert refresh_token == "rt"
        return _tokens(access_token="fresh")

    monkeypatch.setattr(tiktok, "refresh", fake_refresh)
    await repository.save_tiktok_account(
        _tokens(access_token="stale", expires_at=datetime.now(UTC) + timedelta(minutes=1)),
        handle="@me",
    )

    assert await repository.tiktok_access_token() == "fresh"


async def test_a_refreshed_token_is_stored_for_next_time(database, monkeypatch):
    async def fake_refresh(_token):
        return _tokens(access_token="fresh", expires_at=datetime.now(UTC) + timedelta(hours=24))

    monkeypatch.setattr(tiktok, "refresh", fake_refresh)
    await repository.save_tiktok_account(
        _tokens(access_token="stale", expires_at=datetime.now(UTC) - timedelta(hours=1)),
        handle="@me",
    )

    await repository.tiktok_access_token()

    # Second call finds it live and does not refresh again.
    async def tripwire(_token):
        raise AssertionError("should not refresh a freshly stored token")

    monkeypatch.setattr(tiktok, "refresh", tripwire)
    assert await repository.tiktok_access_token() == "fresh"


async def test_an_expired_token_read_from_sqlite_still_refreshes(database, monkeypatch):
    """SQLite drops the timezone, so a naive `expires_at` compared against an
    aware `now` raises TypeError — the same trap `rights._aware` documents, and
    it only bites outside CI because CI runs Postgres."""
    from sqlalchemy import select

    from engine.db import session
    from engine.tables import TikTokAccount

    async def fake_refresh(_token):
        return _tokens(access_token="fresh")

    monkeypatch.setattr(tiktok, "refresh", fake_refresh)
    await repository.save_tiktok_account(_tokens(access_token="stale"), handle="@me")

    # Force the naive shape SQLite hands back.
    async with session() as db:
        row = (await db.execute(select(TikTokAccount))).scalars().one()
        row.expires_at = datetime.now() - timedelta(hours=1)  # noqa: DTZ005 — the point

    assert await repository.tiktok_access_token() == "fresh"


async def test_no_account_asks_for_a_connection(database):
    with pytest.raises(TikTokAuthExpired, match="no TikTok account"):
        await repository.tiktok_access_token()


async def test_a_rejected_refresh_surfaces_as_reconnect(database, monkeypatch):
    async def refuse(_token):
        raise TikTokAuthExpired("TikTok refused the refresh token")

    monkeypatch.setattr(tiktok, "refresh", refuse)
    await repository.save_tiktok_account(
        _tokens(expires_at=datetime.now(UTC) - timedelta(hours=1)), handle="@me"
    )

    with pytest.raises(TikTokAuthExpired):
        await repository.tiktok_access_token()


async def test_an_undecryptable_token_says_reconnect_not_decrypt_error(database, monkeypatch):
    """The secret key changed. The stored token is unrecoverable and the only fix
    is reconnecting, so a decrypt error nobody can act on is the wrong message."""
    from sqlalchemy import select

    from engine.db import session
    from engine.tables import TikTokAccount

    await repository.save_tiktok_account(_tokens(), handle="@me")
    async with session() as db:
        row = (await db.execute(select(TikTokAccount))).scalars().one()
        row.refresh_token_encrypted = "not-a-real-ciphertext"
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)

    with pytest.raises(TikTokAuthExpired, match="reconnect"):
        await repository.tiktok_access_token()


# ── concurrency ─────────────────────────────────────────────────────────────


async def test_simultaneous_sweeps_refresh_once_between_them(database, monkeypatch):
    """The stampede that kills the connection.

    TikTok rotates the refresh token on refresh. Two callers that both find the
    access token expired both spend the *same* stored refresh token — and the
    second spends one TikTok has already retired, so its failure gets written over
    the first one's good token and the account is dead until a human reconnects.

    This is the ordinary shape of the system, not a rare race: the worker sweeps on
    a schedule and the operator presses Discover whenever they like.
    """
    import asyncio

    calls = 0

    async def fake_refresh(_token):
        nonlocal calls
        calls += 1
        # Long enough that a second caller would certainly be inside the window if
        # nothing serialised them.
        await asyncio.sleep(0.05)
        return _tokens(access_token="fresh", refresh_token="rotated")

    monkeypatch.setattr(tiktok, "refresh", fake_refresh)

    await repository.save_tiktok_account(
        _tokens(expires_at=datetime.now(UTC) - timedelta(minutes=1)), handle="@me"
    )

    got = await asyncio.gather(
        *(repository.tiktok_access_token() for _ in range(4)),
    )

    assert calls == 1, "each concurrent sweep spent the rotated-away refresh token"
    assert got == ["fresh"] * 4


async def test_a_refresh_token_past_its_own_expiry_asks_for_a_reconnect(database):
    """No network call: the stored row already says the answer.

    Refresh tokens last about a year, so this is the install nobody has swept in a
    long time — the one case where a round trip buys nothing but a slower error.
    """
    await repository.save_tiktok_account(
        _tokens(
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            refresh_expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
        handle="@me",
    )

    with pytest.raises(TikTokAuthExpired, match="reconnect"):
        await repository.tiktok_access_token()
