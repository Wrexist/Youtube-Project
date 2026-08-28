"""The whole TikTok path, driven against a simulation of TikTok itself.

`test_tiktok_reliability.py` proves the *client* handles the shapes TikTok
documents, with a `MockTransport` under one provider function at a time.
`test_tiktok_account.py` proves the *repository* refreshes and stores a token,
with `tiktok.refresh` monkeypatched away. Both stub out the half of the system the
other one is testing, so nothing yet proved that the two halves fit together —
which is where an unproven integration actually breaks.

This file removes both stubs. Real provider, real repository, real endpoint
functions, real encryption; only the network is simulated, at TikTok's own URLs
through `respx`. A sweep here runs the same code path a connected install runs:
`POST /v1/repurpose/discover` → refresh the token over HTTP → page `video/list` →
score → persist → read back through `GET /v1/repurpose/clips`.

The simulation is faithful to TikTok's annoyances rather than to a tidy REST API:

  * **Two error shapes.** The Display API nests the error in an object; the OAuth
    token endpoint returns a bare string with the message in `error_description`.
    Both arrive as HTTP 200. Reading the second as the first raised
    `AttributeError` — see §4.13.
  * **Rotating refresh tokens**, which is why two concurrent sweeps must produce
    exactly one refresh.
  * **`has_more` and a cursor** that a malformed page can leave behind.
  * **429 and 5xx**, which must reach the operator as a sentence rather than as an
    empty grid — the ambiguity KNOWN-ISSUES §5.5 calls out.

Endpoints are called directly rather than through `TestClient`, for the reason
`test_repurpose_endpoint.py` sets out at length: `TestClient` runs its own event
loop, these tests write rows on pytest's, and asyncpg refuses a pool shared across
the two (§4.10).

**This still proves nothing about the live API.** Every byte here was written by
this file. See the note at the bottom.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi import HTTPException

from engine import repository
from engine.api import repurpose as api
from engine.providers import tiktok
from engine.repurpose.rights import Grant, Lane
from engine.research import keywords

USER_INFO = f"{tiktok.API}/user/info/"
VIDEO_LIST = f"{tiktok.API}/video/list/"


# ── the simulation ──────────────────────────────────────────────────────────


def _display(data: dict) -> httpx.Response:
    """A Display API success.

    Carries the `error: {code: "ok"}` envelope TikTok really sends on success — the
    field is present on a good response, which is why a client cannot treat "has an
    error object" as "failed".
    """
    return httpx.Response(
        200,
        json={"data": data, "error": {"code": "ok", "message": "", "log_id": "2026082800"}},
    )


def _display_error(code: str, message: str = "", status: int = 200) -> httpx.Response:
    """A Display API failure. **HTTP 200**, unless the caller asks otherwise."""
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": message, "log_id": "2026082801"}},
    )


def _token(
    *,
    access_token: str = "access-1",
    refresh_token: str = "refresh-1",
    open_id: str = "open-id-1",
    expires_in: int = 86_400,
    refresh_expires_in: int = 31_536_000,
) -> httpx.Response:
    """A token-endpoint success: v2 returns the fields at the top level."""
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "expires_in": expires_in,
            "open_id": open_id,
            "refresh_token": refresh_token,
            "refresh_expires_in": refresh_expires_in,
            "scope": "user.info.basic,video.list",
            "token_type": "Bearer",
        },
    )


def _token_error(
    error: str = "invalid_grant",
    description: str = "Refresh token is invalid or expired.",
    status: int = 200,
) -> httpx.Response:
    """A token-endpoint failure, in the shape the token endpoint actually uses.

    Not the Display API's nested object: `error` is a **string** and the readable
    part is a sibling `error_description`. This is the body a dead refresh token
    comes back with, and it arrives as HTTP 200 like everything else.
    """
    return httpx.Response(
        status,
        json={"error": error, "error_description": description, "log_id": "2026082802"},
    )


def _video(video_id: str, *, description: str, duration: int, views: int = 0) -> dict:
    return {
        "id": video_id,
        "title": "",
        "video_description": description,
        "duration": duration,
        "cover_image_url": f"https://p16.tiktokcdn.example/{video_id}.jpeg",
        "share_url": f"https://www.tiktok.com/@studio/video/{video_id}",
        "embed_link": f"https://www.tiktok.com/embed/v2/{video_id}",
        "like_count": views // 20,
        "comment_count": views // 400,
        "share_count": views // 200,
        "view_count": views,
        "create_time": 1_750_000_000,
    }


def _page(videos: list[dict], *, cursor: int | None = None, has_more: bool = False) -> dict:
    """One `video/list` page. TikTok's cursor is a millisecond timestamp."""
    page: dict = {"videos": videos, "has_more": has_more}
    if cursor is not None:
        page["cursor"] = cursor
    return page


def _bridges(views: int = 900_000) -> dict:
    return _video(
        "7300000000000000001",
        description="why suspension bridges fail in crosswinds #engineering #bridges",
        duration=42,
        views=views,
    )


def _too_short() -> dict:
    return _video(
        "7300000000000000002",
        description="a three second bridge joke that is far too short to build around",
        duration=3,
        views=12_000,
    )


AUTOCOMPLETE = [
    "why bridges fail",
    "suspension bridges explained",
    "bridge collapse footage",
    "engineering disasters",
]


def _autocomplete() -> respx.Route:
    """YouTube autocomplete, so the demand component is measured rather than zero."""
    return respx.get(keywords.SUGGEST_URL).mock(
        return_value=httpx.Response(200, json=["seed", AUTOCOMPLETE])
    )


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    from engine.settings import get_settings

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "test-client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "test-client-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_backoff(monkeypatch):
    """Backoff is real and tested in `test_tiktok_reliability`; waiting is not.

    Opt-in rather than autouse, unlike that file: `tiktok.asyncio` *is* the asyncio
    module, so patching its `sleep` patches everyone's — including the concurrency
    test below, whose whole subject is what two callers do while one of them waits.
    """

    async def instant(_seconds):
        return None

    monkeypatch.setattr(tiktok.asyncio, "sleep", instant)


async def _connect(
    *,
    access_token: str = "stored-access",
    refresh_token: str = "stored-refresh",
    expires_in: timedelta = timedelta(hours=20),
    refresh_expires_in: timedelta = timedelta(days=300),
) -> None:
    """An account already connected, as a returning install has."""
    now = datetime.now(UTC)
    await repository.save_tiktok_account(
        tiktok.Tokens(
            access_token=access_token,
            refresh_token=refresh_token,
            open_id="open-id-1",
            expires_at=now + expires_in,
            refresh_expires_at=now + refresh_expires_in,
            scope="user.info.basic,video.list",
        ),
        handle="@studio",
    )


async def _list_clips(channel_key: str = "main") -> api.Clips:
    """`api.clips` with its `Query` defaults supplied — see test_repurpose_endpoint."""
    return await api.clips(channel_key=channel_key, status="discovered", limit=50)


# ── 1. OAuth, against the token endpoint ────────────────────────────────────


@respx.mock
async def test_a_dead_refresh_token_reads_as_reconnect_rather_than_crashing(database):
    """§4.13. The token endpoint's `error` is a string, and the client read it as an
    object.

    `"invalid_grant".get("code")` is an `AttributeError`, which is neither of this
    module's exception types — so every `except TikTokUnavailable` in the codebase
    missed it and the most ordinary failure this integration has (a refresh token
    that died) surfaced as a 500 rather than as "reconnect the account".
    """
    respx.post(tiktok.TOKEN_URL).mock(return_value=_token_error())
    await _connect(expires_in=-timedelta(hours=1))

    with pytest.raises(tiktok.TikTokAuthExpired, match="Reconnect"):
        await repository.tiktok_access_token()


@respx.mock
async def test_a_token_endpoint_failure_that_is_not_the_users_fault_is_not_a_reconnect(database):
    """`invalid_client` is a wrong key in `.env`. Reconnecting cannot fix it, so it
    must not be dressed up as something a button solves."""
    respx.post(tiktok.TOKEN_URL).mock(
        return_value=_token_error("invalid_client", "Client key or secret is incorrect.")
    )
    await _connect(expires_in=-timedelta(hours=1))

    with pytest.raises(tiktok.TikTokUnavailable) as raised:
        await repository.tiktok_access_token()

    assert not isinstance(raised.value, tiktok.TikTokAuthExpired)
    assert "invalid_client" in str(raised.value)


@respx.mock
async def test_connecting_an_account_stores_an_encrypted_token_it_never_returns(database):
    """The callback, end to end: code in, encrypted credential and a handle out.

    `test_a_state_cannot_be_replayed` walks the same path with `exchange_code`
    monkeypatched, so the exchange itself — the form TikTok is posted, the body it
    answers with — has never been exercised through this endpoint.
    """
    from sqlalchemy import select

    from engine.crypto import decrypt
    from engine.db import session
    from engine.tables import TikTokAccount

    token_route = respx.post(tiktok.TOKEN_URL).mock(
        return_value=_token(access_token="fresh-access", refresh_token="fresh-refresh")
    )
    respx.get(USER_INFO).mock(
        return_value=_display({"user": {"open_id": "open-id-1", "username": "studio"}})
    )

    await api.begin_tiktok_auth()
    state = next(iter(api._PENDING_STATES))
    response = await api.tiktok_callback(code="auth-code-1", state=state)

    assert "provider=tiktok&status=ok" in response.headers["location"]

    form = dict(httpx.QueryParams(token_route.calls.last.request.content.decode()))
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "auth-code-1"
    assert form["client_key"] == "test-client-key"

    async with session() as db:
        row = (await db.execute(select(TikTokAccount))).scalars().one()
    assert "fresh-refresh" not in row.refresh_token_encrypted
    assert decrypt(row.refresh_token_encrypted) == "fresh-refresh"

    status = await api.tiktok_status()
    assert status.account is not None
    assert status.account.handle == "@studio"
    assert "fresh-refresh" not in status.model_dump_json()


@respx.mock
async def test_a_refused_authorisation_code_comes_back_as_a_sentence_not_a_traceback(database):
    """The thing at the other end of the callback is a browser tab someone is
    looking at. An unhandled exception there is a stack trace where an explanation
    should be."""
    respx.post(tiktok.TOKEN_URL).mock(
        return_value=_token_error("invalid_grant", "Authorization code is expired.")
    )

    await api.begin_tiktok_auth()
    state = next(iter(api._PENDING_STATES))
    response = await api.tiktok_callback(code="stale-code", state=state)

    assert response.status_code == 303
    assert "provider=tiktok&status=error" in response.headers["location"]
    assert await repository.load_tiktok_account() is None


@respx.mock
async def test_a_refreshed_token_is_persisted_and_the_next_sweep_reuses_it(database):
    """Rotation, storage and reuse in one pass.

    A client that refreshes per call burns a rotation every time and multiplies the
    chance of the stampede below; one that never refreshes works for a day. The
    contract is exactly one refresh, at the point the stored token goes stale.
    """
    from sqlalchemy import select

    from engine.crypto import decrypt
    from engine.db import session
    from engine.tables import TikTokAccount

    token_route = respx.post(tiktok.TOKEN_URL).mock(
        return_value=_token(access_token="rotated-access", refresh_token="rotated-refresh")
    )
    await _connect(expires_in=-timedelta(hours=1))

    assert await repository.tiktok_access_token() == "rotated-access"
    assert await repository.tiktok_access_token() == "rotated-access"
    assert token_route.call_count == 1, "the second caller refreshed a token that was fresh"

    form = dict(httpx.QueryParams(token_route.calls.last.request.content.decode()))
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "stored-refresh"

    async with session() as db:
        row = (await db.execute(select(TikTokAccount))).scalars().one()
    assert decrypt(row.refresh_token_encrypted) == "rotated-refresh", "the rotation was not stored"
    assert "rotated-refresh" not in row.refresh_token_encrypted


# ── 2. two sweeps at once ───────────────────────────────────────────────────


@respx.mock
async def test_two_sweeps_starting_together_refresh_exactly_once(database):
    """The stampede, at the HTTP boundary.

    `test_simultaneous_sweeps_refresh_once_between_them` proves the lock holds with
    `tiktok.refresh` replaced by a stub. This proves the same thing with the real
    provider making a real request through the real client — the arrangement where
    a second connection, a second `AsyncClient` or a retry inside `_request` could
    put a second POST on the wire while the first is still in flight.

    Held on an `Event` rather than a sleep: the second caller is provably still
    inside the window, rather than probably.
    """
    in_flight = asyncio.Event()
    release = asyncio.Event()
    posts: list[httpx.Request] = []

    async def slow_refresh(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        in_flight.set()
        await release.wait()
        return _token(access_token="rotated-access", refresh_token="rotated-refresh")

    respx.post(tiktok.TOKEN_URL).mock(side_effect=slow_refresh)
    await _connect(expires_in=-timedelta(hours=1))

    callers = [asyncio.create_task(repository.tiktok_access_token()) for _ in range(4)]
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    await asyncio.sleep(0)  # let every other caller run as far as it can get
    release.set()
    got = await asyncio.gather(*callers)

    assert len(posts) == 1, "a second caller spent the refresh token TikTok had already retired"
    assert got == ["rotated-access"] * 4


# ── 3. pagination ───────────────────────────────────────────────────────────


@respx.mock
async def test_a_sweep_pages_to_the_end_and_sends_each_cursor_back(database):
    """Three pages, the last one partial.

    A partial final page is the ordinary shape: TikTok returns "up to" `max_count`,
    so a client treating a short page as the end stops early on a full account.
    """
    pages = [
        _display(_page([_bridges()], cursor=1_750_000_003_000, has_more=True)),
        _display(
            _page(
                [_video("7300000000000000010", description="a longer clip", duration=30)],
                cursor=1_750_000_002_000,
                has_more=True,
            )
        ),
        _display(
            _page(
                [_video("7300000000000000020", description="the last one", duration=25)],
                cursor=1_750_000_001_000,
                has_more=False,
            )
        ),
    ]
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    listing = respx.post(VIDEO_LIST).mock(side_effect=lambda _r: pages.pop(0))

    clips = await tiktok.own_videos("access", limit=50)

    assert [c.external_id for c in clips] == [
        "7300000000000000001",
        "7300000000000000010",
        "7300000000000000020",
    ]
    bodies = [json.loads(call.request.content) for call in listing.calls]
    assert "cursor" not in bodies[0], "the first page asked TikTok to resume from somewhere"
    assert [b["cursor"] for b in bodies[1:]] == [1_750_000_003_000, 1_750_000_002_000]


@respx.mock
async def test_has_more_with_no_cursor_ends_the_sweep_rather_than_looping(database):
    """The malformed page that would otherwise re-read page one until `MAX_PAGES`.

    `has_more: true` with the cursor missing is not a documented response, which is
    exactly why it is worth pinning: an integration that has never met the live API
    will meet its undocumented shapes first.
    """
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    listing = respx.post(VIDEO_LIST).mock(
        return_value=_display(_page([_bridges()], cursor=None, has_more=True))
    )

    clips = await tiktok.own_videos("access", limit=200)

    assert [c.external_id for c in clips] == ["7300000000000000001"]
    assert listing.call_count == 1


# ── 4. rate limits and outages ──────────────────────────────────────────────


@respx.mock
async def test_a_rate_limited_sweep_is_an_error_rather_than_an_empty_grid(database, no_backoff):
    """KNOWN-ISSUES §5.5's ambiguity, at the endpoint that produces it.

    An empty `clips` list renders as "no clips found", which is a true statement
    about an account with nothing on it and a lie about a rate-limited one. The two
    need different actions from the operator, so they cannot share a response.
    """
    respx.get(USER_INFO).mock(return_value=httpx.Response(429, json={}))
    respx.post(VIDEO_LIST).mock(
        return_value=httpx.Response(429, json={}, headers={"retry-after": "2"})
    )
    _autocomplete()
    await _connect()

    with pytest.raises(HTTPException) as raised:
        await api.discover(api.DiscoverRequest(channel_key="main"))

    assert raised.value.status_code == 502
    assert "429" in str(raised.value.detail)


@respx.mock
async def test_an_outage_while_refreshing_the_token_is_reported_like_any_other(database):
    """§4.13. Acquiring the token is itself a call to TikTok.

    A 5xx one line later — inside the sweep — was a 502 with a sentence. The same
    outage while refreshing escaped as an unhandled `TikTokUnavailable`, because the
    endpoint caught only the `TikTokAuthExpired` subclass around that call. The
    operator got a 500 and the screen's fallback made it look like a stopped engine.
    """
    respx.post(tiktok.TOKEN_URL).mock(return_value=httpx.Response(503, json={}))
    await _connect(expires_in=-timedelta(hours=1))

    with pytest.raises(HTTPException) as raised:
        await api.discover(api.DiscoverRequest(channel_key="main"))

    assert raised.value.status_code == 502
    assert "503" in str(raised.value.detail)


@respx.mock
async def test_a_token_refused_mid_sweep_asks_for_a_reconnect_with_the_way_back(database):
    """A live-looking token TikTok refuses anyway — the shape of a revoked consent.

    409 rather than 502 because the remedy is a button, not patience, and the
    response carries where that button goes.
    """
    respx.get(USER_INFO).mock(return_value=_display_error("access_token_invalid", "invalid token"))
    await _connect()

    with pytest.raises(HTTPException) as raised:
        await api.discover(api.DiscoverRequest(channel_key="main"))

    assert raised.value.status_code == 409
    assert raised.value.detail["reconnect_at"] == "/v1/repurpose/auth/tiktok"


@respx.mock
async def test_a_5xx_on_a_later_page_keeps_the_clips_that_arrived(database, no_backoff):
    """Partial success beats a clean failure here: 20 clips and a stale second page
    is a working screen, and an exception is an empty one."""
    pages = [
        _display(_page([_bridges()], cursor=1_750_000_003_000, has_more=True)),
        httpx.Response(500, json={}),
        httpx.Response(500, json={}),
        httpx.Response(500, json={}),
    ]
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    respx.post(VIDEO_LIST).mock(side_effect=lambda _r: pages.pop(0))
    _autocomplete()
    await _connect()

    found = await api.discover(api.DiscoverRequest(channel_key="main"))

    assert [c.external_id for c in found.clips] == ["7300000000000000001"]
    assert found.connected is True


# ── 5. the discovery sweep, end to end ──────────────────────────────────────


@respx.mock
async def test_a_sweep_refreshes_pages_scores_stores_and_reads_back(database):
    """The whole of Lane A in one test, with nothing in the engine stubbed.

    Expired token in the database, two pages on the wire, and the assertions follow
    the data all the way to the grid: the refreshed token is the one the sweep
    authenticates with, the handle is attached for the on-screen credit, fit ranks
    the usable clip above the three-second one, and `GET /clips` reads back what
    `POST /discover` wrote.
    """
    token_route = respx.post(tiktok.TOKEN_URL).mock(
        return_value=_token(access_token="rotated-access", refresh_token="rotated-refresh")
    )
    user_route = respx.get(USER_INFO).mock(
        return_value=_display({"user": {"open_id": "open-id-1", "username": "studio"}})
    )
    listing = respx.post(VIDEO_LIST).mock(
        side_effect=[
            _display(_page([_bridges()], cursor=1_750_000_003_000, has_more=True)),
            _display(_page([_too_short()], cursor=1_750_000_002_000, has_more=False)),
        ]
    )
    _autocomplete()
    await _connect(expires_in=-timedelta(hours=1))

    found = await api.discover(api.DiscoverRequest(channel_key="main", limit=40))

    assert token_route.call_count == 1
    assert user_route.calls.last.request.headers["authorization"] == "Bearer rotated-access"
    assert listing.calls.last.request.headers["authorization"] == "Bearer rotated-access"
    requested = httpx.QueryParams(listing.calls.last.request.url.query.decode())["fields"]
    assert "share_url" in requested and "create_time" in requested
    assert "download_addr" not in requested, "asking for it fails the whole request"

    assert found.configured is True
    assert found.connected is True
    assert [c.external_id for c in found.clips] == [
        "7300000000000000001",
        "7300000000000000002",
    ], "the three-second clip outranked one that can actually be built with"

    usable, unusable = found.clips
    assert usable.creator_handle == "@studio"
    assert usable.url == "https://www.tiktok.com/@studio/video/7300000000000000001"
    assert usable.hashtags == ["#engineering", "#bridges"]
    assert usable.stats["views"] == 900_000
    assert usable.fit_score > unusable.fit_score
    assert any("too short" in reason for reason in unusable.fit_reasons)
    assert any("autocomplete" in reason for reason in usable.fit_reasons), (
        "demand was never measured, so the score is reach and length only"
    )

    listed = await _list_clips()
    assert [c.id for c in listed.clips] == [c.id for c in found.clips]
    assert listed.clips[0].fit_score == usable.fit_score
    assert listed.clips[0].grant is None, "a swept clip arrived pre-cleared"


@respx.mock
async def test_the_same_clip_scores_the_same_however_many_it_was_swept_with(database):
    """§4.13. `fit_score` has to mean something across sweeps.

    `upsert_clip_sources` overwrites the stored score on every pass, and the grid is
    sorted by it, so a score that depends on how many *other* clips were in the
    batch re-ranks the whole screen for a reason that has nothing to do with the
    clips. `_pooled_suggestions` pooled one autocomplete sweep per caption without
    deduplicating, so four captions returning the same phrases counted each of them
    four times and `demand` rose with batch size.
    """
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    _autocomplete()
    listing = respx.post(VIDEO_LIST)
    await _connect()

    listing.mock(return_value=_display(_page([_bridges()])))
    alone = await api.discover(api.DiscoverRequest(channel_key="main"))
    scored_alone = alone.clips[0].fit_score

    listing.mock(
        return_value=_display(
            _page(
                [
                    _bridges(),
                    _video(
                        "7300000000000000031",
                        description="bridge cables under load, explained",
                        duration=40,
                    ),
                    _video(
                        "7300000000000000032",
                        description="why bridges sway in the wind and survive",
                        duration=35,
                    ),
                    _video(
                        "7300000000000000033",
                        description="the physics of a bridge collapse, slowly",
                        duration=38,
                    ),
                ]
            )
        )
    )
    swept = await api.discover(api.DiscoverRequest(channel_key="main"))

    same_clip = next(c for c in swept.clips if c.external_id == "7300000000000000001")
    assert same_clip.fit_score == scored_alone, (
        "the clip's stored score moved because of what it was swept alongside"
    )
    assert not any("12 YouTube autocomplete" in r for r in same_clip.fit_reasons), (
        "the card counted the same four phrases once per seed caption"
    )


# ── 6. the rights gate ──────────────────────────────────────────────────────


@respx.mock
async def test_footage_swept_from_your_own_account_still_has_no_grant(database):
    """The invariant most easily lost, because it looks redundant.

    Discovery scores Lane A clips as `cleared=True` — but that is a *ranking* input
    meaning "this will be easy to clear", and no grant row exists until someone
    records one. If those two ever collapse into each other, `record_asset` starts
    accepting media on the strength of a number the fit scorer made up.
    """
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    respx.post(VIDEO_LIST).mock(return_value=_display(_page([_bridges()])))
    _autocomplete()
    await _connect()

    found = await api.discover(api.DiscoverRequest(channel_key="main"))
    source_id = found.clips[0].id

    assert found.clips[0].cleared is False
    with pytest.raises(PermissionError, match="no grant"):
        await repository.record_asset(source_id, {"storage_key": "clips/1.mp4"})

    await api.record_grant(source_id, api.GrantIn(lane="own"))
    assert await repository.record_asset(source_id, {"storage_key": "clips/1.mp4"})


@respx.mock
async def test_withdrawing_permission_stops_the_next_fetch_and_shows_on_the_card(database):
    """A grant is a record with a lifetime, not a flag that was once true.

    Grants append, so a revocation is a newer row rather than an edit — and both
    readers have to agree about which row is live: the persistence guard that
    refuses media, and the grid that draws the rights chip. A revocation the
    acquire stage honours but the card does not would leave the operator looking at
    a clip marked ready that nothing will fetch.

    Recorded through the repository because there is no revoke endpoint yet; the
    API can grant but not withdraw.
    """
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    respx.post(VIDEO_LIST).mock(return_value=_display(_page([_bridges()])))
    _autocomplete()
    await _connect()

    found = await api.discover(api.DiscoverRequest(channel_key="main"))
    source_id = found.clips[0].id

    await api.record_grant(
        source_id,
        api.GrantIn(
            lane="licensed",
            grantor="@someone",
            evidence_kind="dm_screenshot",
            evidence_ref="storage://grants/1.png",
        ),
    )
    assert (await _list_clips()).clips[0].cleared is True

    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.LICENSED,
            grantor="@someone",
            evidence_kind="dm_screenshot",
            evidence_ref="storage://grants/1.png",
            granted_at=datetime.now(UTC) - timedelta(days=30),
            revoked_at=datetime.now(UTC) - timedelta(days=1),
        ),
    )

    with pytest.raises(PermissionError, match="no longer live"):
        await repository.record_asset(source_id, {"storage_key": "clips/1.mp4"})

    card = (await _list_clips()).clips[0]
    assert card.cleared is False
    assert card.grant is not None
    assert [p.code for p in card.grant.problems if p.fatal] == ["revoked"]


# ── 7. the originality gate ─────────────────────────────────────────────────


@respx.mock
async def test_a_lift_with_a_top_and_tail_is_refused_with_the_numbers_that_refused_it(database):
    """Cleared to use, nowhere near original enough — and the report says which.

    "Blocked" on its own sends the operator back to the rights panel, which is
    already green. Every blocking signal therefore carries the measurement and the
    threshold it missed, so the card can say *60s unbroken, allowed 15* rather than
    *not transformative enough*.
    """
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    respx.post(VIDEO_LIST).mock(return_value=_display(_page([_bridges()])))
    _autocomplete()
    await _connect()

    found = await api.discover(api.DiscoverRequest(channel_key="main"))
    source_id = found.clips[0].id
    await api.record_grant(source_id, api.GrantIn(lane="own"))

    report = await api.evaluate_timeline(
        api.TimelineIn(
            segments=[
                {"start_s": 0, "end_s": 30, "source_id": source_id, "narrated": True},
                {"start_s": 30, "end_s": 90, "source_id": source_id},
            ],
            cuts=1,
            audio_bed_replaced=True,
            compared_against=8,
            max_similarity=0.2,
        )
    )

    assert report.rights.cleared is True, "this is an originality refusal, not a rights one"
    assert report.publishable is False
    assert report.headline == "Blocked on originality — 3 checks failed."

    blocking = {s.name: s for s in report.transformation.signals if s.severity == "block"}
    assert set(blocking) == {"authored_share", "bare_source_share", "longest_bare_run"}
    assert blocking["longest_bare_run"].value == 60.0
    assert blocking["longest_bare_run"].threshold == 15.0
    assert "reupload" in blocking["longest_bare_run"].message
    assert blocking["bare_source_share"].value == pytest.approx(2 / 3, abs=1e-3)


@respx.mock
async def test_the_same_footage_passes_once_the_narration_covers_it(database):
    """The counterpart, and the reason the gate measures authorship rather than
    ownership of pixels: a reaction video is 100% someone else's footage and YouTube
    names it as monetisable. Cutting the lift up and talking over it is the fix the
    refusal above is pointing at."""
    respx.get(USER_INFO).mock(return_value=_display({"user": {"username": "studio"}}))
    respx.post(VIDEO_LIST).mock(return_value=_display(_page([_bridges()])))
    _autocomplete()
    await _connect()

    found = await api.discover(api.DiscoverRequest(channel_key="main"))
    source_id = found.clips[0].id
    await api.record_grant(source_id, api.GrantIn(lane="own"))

    report = await api.evaluate_timeline(
        api.TimelineIn(
            segments=[
                {"start_s": 0, "end_s": 30, "source_id": source_id, "narrated": True},
                {"start_s": 30, "end_s": 40, "source_id": source_id},
                {"start_s": 40, "end_s": 90, "source_id": source_id, "narrated": True},
            ],
            cuts=20,
            audio_bed_replaced=True,
            compared_against=8,
            max_similarity=0.2,
        )
    )

    assert report.publishable is True
    assert report.headline == "Cleared to publish."


# ── what this file does not prove ───────────────────────────────────────────
#
# Everything above is a conversation with a fixture. It proves the client, the
# repository, the endpoints and the two gates agree with each other and with what
# TikTok *documents*. It cannot prove:
#
#   * that TikTok accepts these requests at all — a renamed form field, a required
#     header, or a scope the app was never granted all pass here and fail on first
#     contact;
#   * that the error codes in `_AUTH_ERRORS` are the ones TikTok actually sends.
#     They are transcribed from documentation, and the 401 backstop in `_unwrap`
#     exists precisely because that list is the likeliest thing here to be wrong;
#   * that `refresh_expires_in` is really a year, or that the refresh token rotates
#     on every grant rather than some;
#   * that `video/list` pages the way this simulates — cursor semantics are
#     documented thinly and "20 per page" is asserted, not observed;
#   * anything about rate limit thresholds, which TikTok does not publish.
#
# Only credentials and a reviewed app close those, which is §1.1's argument for the
# second API. See KNOWN-ISSUES §5.5.
