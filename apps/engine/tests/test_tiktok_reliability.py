"""The TikTok client's failure modes.

None of this can be verified against the live API, so these tests encode what the
API *documents* and, more importantly, the shapes that break a client silently.
Four in particular, each of which stops the integration working without anything
looking wrong:

  1. TikTok answers **200 with an error body**, so a status-code check passes.
  2. Access tokens last **24 hours**, so an integration with no refresh works the
     day it is set up and is dead by the next sweep.
  3. `video.list` is **paginated**, so a sweep that ignores `has_more` silently
     sees only the newest 20 posts.
  4. An expired token and an empty account **look identical** if every failure
     returns `[]`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from engine.providers import tiktok
from engine.providers.tiktok import TikTokAuthExpired, TikTokUnavailable


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    from engine.settings import get_settings

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Backoff is real and tested; waiting for it is not."""

    async def instant(_seconds):
        return None

    monkeypatch.setattr(tiktok.asyncio, "sleep", instant)


def _transport(monkeypatch, handler):
    """Install a fake HTTP layer. `handler(request) -> httpx.Response`."""
    calls: list[httpx.Request] = []

    def track(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    # Captured *before* patching. `tiktok.httpx` is the httpx module itself, so
    # constructing through the module name inside the replacement calls the
    # replacement — an infinite recursion rather than a mocked request.
    real_client = httpx.AsyncClient

    def build(**kwargs):
        # The provider passes its own `timeout`; the mock supplies the transport.
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(track), **kwargs)

    monkeypatch.setattr(tiktok.httpx, "AsyncClient", build)
    return calls


def _ok(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _video(video_id: str) -> dict:
    return {
        "id": video_id,
        "video_description": f"clip {video_id} #tag",
        "duration": 20,
        "share_url": f"https://tiktok.example/v/{video_id}",
        "view_count": 100,
    }


# ── 1. 200 with an error body ───────────────────────────────────────────────


async def test_an_error_body_on_a_200_is_not_treated_as_success(monkeypatch):
    """`raise_for_status()` sees nothing wrong with this, which is the whole
    problem: a client that trusts the status code reads it as an empty sweep."""
    _transport(
        monkeypatch,
        lambda _r: _ok({"error": {"code": "internal_error", "message": "boom"}}),
    )

    with pytest.raises(TikTokUnavailable, match="internal_error"):
        await tiktok.own_videos("token")


async def test_code_ok_is_success_not_failure(monkeypatch):
    """TikTok sends `{"error": {"code": "ok"}}` on success. Treating any error
    object as a failure would reject every good response."""
    _transport(
        monkeypatch,
        lambda _r: _ok({"error": {"code": "ok"}, "data": {"user": {"username": "me"}}}),
    )

    assert await tiktok.creator_handle("token") == "@me"


async def test_a_body_that_is_not_json_is_a_clear_failure(monkeypatch):
    _transport(monkeypatch, lambda _r: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(TikTokUnavailable, match="not JSON"):
        await tiktok.own_videos("token")


# ── 2. auth failures are their own thing ────────────────────────────────────


async def test_an_invalid_token_raises_rather_than_returning_nothing(monkeypatch):
    """An expired token and an empty account must not look the same — one needs a
    reconnect button, the other needs nothing."""
    _transport(
        monkeypatch,
        lambda _r: _ok({"error": {"code": "access_token_invalid", "message": "nope"}}),
    )

    with pytest.raises(TikTokAuthExpired, match="Reconnect"):
        await tiktok.own_videos("token")


async def test_a_401_is_an_auth_failure_whatever_code_it_carries(monkeypatch):
    """The backstop for an error code not on the list — which is likely, since
    none of this has been seen against the live API."""
    _transport(monkeypatch, lambda _r: _ok({"error": {"code": "some_new_code"}}, status=401))

    with pytest.raises(TikTokAuthExpired):
        await tiktok.own_videos("token")


async def test_an_auth_failure_is_not_retried(monkeypatch):
    """It fails identically three times and only delays the reconnect."""
    calls = _transport(monkeypatch, lambda _r: _ok({"error": {"code": "access_token_expired"}}))

    with pytest.raises(TikTokAuthExpired):
        await tiktok.creator_handle("token")

    assert len(calls) == 1


# ── 3. retries, rate limits, backoff ────────────────────────────────────────


async def test_a_500_is_retried_and_can_succeed(monkeypatch):
    responses = [
        httpx.Response(500, json={}),
        _ok({"data": {"user": {"username": "me"}}}),
    ]
    _transport(monkeypatch, lambda _r: responses.pop(0))

    assert await tiktok.creator_handle("token") == "@me"


async def test_a_429_is_retried(monkeypatch):
    responses = [
        httpx.Response(429, json={}),
        _ok({"data": {"user": {"username": "me"}}}),
    ]
    _transport(monkeypatch, lambda _r: responses.pop(0))

    assert await tiktok.creator_handle("token") == "@me"


async def test_retries_give_up_and_report(monkeypatch):
    calls = _transport(monkeypatch, lambda _r: httpx.Response(503, json={}))

    with pytest.raises(TikTokUnavailable, match="503"):
        await tiktok.creator_handle("token")

    assert len(calls) == tiktok.MAX_ATTEMPTS


async def test_retry_after_is_honoured_and_capped(monkeypatch):
    """Guessing a backoff shorter than the one TikTok asked for is how a rate
    limit becomes a ban. An absurd value is capped rather than obeyed."""
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(tiktok.asyncio, "sleep", record)
    responses = [
        httpx.Response(429, json={}, headers={"retry-after": "5"}),
        httpx.Response(429, json={}, headers={"retry-after": "99999"}),
        _ok({"data": {"user": {"username": "me"}}}),
    ]
    _transport(monkeypatch, lambda _r: responses.pop(0))

    await tiktok.creator_handle("token")

    assert slept[0] == 5.0
    assert slept[1] == 30.0


async def test_a_network_error_is_retried_then_reported(monkeypatch):
    def boom(_request):
        raise httpx.ConnectError("no route to host")

    calls = _transport(monkeypatch, boom)

    with pytest.raises(TikTokUnavailable, match="could not reach"):
        await tiktok.creator_handle("token")

    assert len(calls) == tiktok.MAX_ATTEMPTS


# ── 4. pagination ───────────────────────────────────────────────────────────


async def test_a_sweep_follows_has_more(monkeypatch):
    """Without this a sweep sees the newest 20 posts and nothing else — which is
    indistinguishable from a working sweep on a small account."""
    pages = [
        _ok({"data": {"videos": [_video("a"), _video("b")], "cursor": 111, "has_more": True}}),
        _ok({"data": {"videos": [_video("c")], "cursor": 222, "has_more": False}}),
    ]

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        return pages.pop(0)

    _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token", limit=50)

    assert [c.external_id for c in clips] == ["a", "b", "c"]


async def test_the_cursor_is_sent_on_later_pages(monkeypatch):
    import json

    pages = [
        _ok({"data": {"videos": [_video("a")], "cursor": 999, "has_more": True}}),
        _ok({"data": {"videos": [_video("b")], "has_more": False}}),
    ]
    bodies: list[dict] = []

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        bodies.append(json.loads(request.content))
        return pages.pop(0)

    _transport(monkeypatch, handler)

    await tiktok.own_videos("token", limit=50)

    assert "cursor" not in bodies[0]
    assert bodies[1]["cursor"] == 999


async def test_a_cursor_that_does_not_advance_stops_the_sweep(monkeypatch):
    """A malformed response would otherwise turn a sweep into an infinite loop
    re-reading page one."""

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        return _ok({"data": {"videos": [_video("a")], "cursor": 1, "has_more": True}})

    calls = _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token", limit=200)

    assert len(clips) <= 2
    assert len(calls) < tiktok.MAX_PAGES


async def test_the_page_ceiling_bounds_a_runaway(monkeypatch):
    counter = {"n": 0}

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        counter["n"] += 1
        return _ok(
            {
                "data": {
                    "videos": [_video(f"v{counter['n']}")],
                    "cursor": counter["n"],
                    "has_more": True,
                }
            }
        )

    _transport(monkeypatch, handler)

    await tiktok.own_videos("token", limit=10_000)

    assert counter["n"] <= tiktok.MAX_PAGES


async def test_the_limit_is_respected_across_pages(monkeypatch):
    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        return _ok({"data": {"videos": [_video("a"), _video("b")], "cursor": 1, "has_more": True}})

    _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token", limit=3)

    assert len(clips) == 3


async def test_a_failure_partway_keeps_the_pages_that_worked(monkeypatch):
    """One bad page late in a sweep must not discard the good ones."""
    pages = [
        _ok({"data": {"videos": [_video("a")], "cursor": 1, "has_more": True}}),
        httpx.Response(503, json={}),
        httpx.Response(503, json={}),
        httpx.Response(503, json={}),
    ]

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "me"}}})
        return pages.pop(0)

    _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token", limit=50)

    assert [c.external_id for c in clips] == ["a"]


# ── the creator handle ──────────────────────────────────────────────────────


async def test_clips_carry_the_creator_handle(monkeypatch):
    """Without it the on-screen credit is blank and `clip_source` groups every
    video under "" — the most actionable attribution dimension, disabled."""

    def handler(request):
        if "user/info" in str(request.url):
            return _ok({"data": {"user": {"username": "mychannel"}}})
        return _ok({"data": {"videos": [_video("a")], "has_more": False}})

    _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token")

    assert clips[0].creator_handle == "@mychannel"


async def test_a_missing_handle_costs_a_credit_not_the_sweep(monkeypatch):
    def handler(request):
        if "user/info" in str(request.url):
            return httpx.Response(503, json={})
        return _ok({"data": {"videos": [_video("a")], "has_more": False}})

    _transport(monkeypatch, handler)

    clips = await tiktok.own_videos("token")

    assert len(clips) == 1
    assert clips[0].creator_handle == ""


async def test_an_auth_failure_reading_the_handle_still_stops_the_sweep(monkeypatch):
    """It is the same dead token the next call would hit."""
    _transport(monkeypatch, lambda _r: _ok({"error": {"code": "access_token_invalid"}}))

    with pytest.raises(TikTokAuthExpired):
        await tiktok.own_videos("token")


# ── tokens ──────────────────────────────────────────────────────────────────


async def test_a_token_response_is_parsed_with_expiry(monkeypatch):
    _transport(
        monkeypatch,
        lambda _r: _ok(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "open_id": "oid",
                "expires_in": 86_400,
                "refresh_expires_in": 31_536_000,
                "scope": "video.list",
            }
        ),
    )

    tokens = await tiktok.exchange_code("code", "https://example.test/cb", "verifier")

    assert tokens.access_token == "at"
    assert tokens.open_id == "oid"
    assert tokens.expires_at is not None
    assert not tokens.expired


async def test_tokens_nested_under_data_are_also_accepted(monkeypatch):
    """Getting this wrong produces an empty token that fails later and further
    away from the cause."""
    _transport(monkeypatch, lambda _r: _ok({"data": {"access_token": "at", "expires_in": 100}}))

    tokens = await tiktok.exchange_code("code", "https://example.test/cb", "verifier")

    assert tokens.access_token == "at"


def test_a_token_inside_the_refresh_margin_counts_as_expired():
    """A sweep that starts with 30 seconds left finishes with an invalid token."""
    nearly = tiktok.Tokens(access_token="at", expires_at=datetime.now(UTC) + timedelta(minutes=1))
    assert nearly.expired

    fresh = tiktok.Tokens(access_token="at", expires_at=datetime.now(UTC) + timedelta(hours=5))
    assert not fresh.expired


async def test_a_refresh_that_omits_the_refresh_token_keeps_the_old_one(monkeypatch):
    """TikTok rotates it on some grants and omits it on others. Overwriting with
    "" would end the connection at the next expiry."""
    _transport(monkeypatch, lambda _r: _ok({"access_token": "new", "expires_in": 86_400}))

    tokens = await tiktok.refresh("original-refresh")

    assert tokens.access_token == "new"
    assert tokens.refresh_token == "original-refresh"


async def test_a_rejected_refresh_asks_for_a_reconnect(monkeypatch):
    _transport(monkeypatch, lambda _r: _ok({"error": {"code": "refresh_token_expired"}}))

    with pytest.raises(TikTokAuthExpired):
        await tiktok.refresh("stale")


async def test_refreshing_without_a_token_is_an_auth_failure():
    with pytest.raises(TikTokAuthExpired, match="reconnect"):
        await tiktok.refresh("")


# ── configuration ───────────────────────────────────────────────────────────


async def test_no_token_means_no_call(monkeypatch):
    calls = _transport(monkeypatch, lambda _r: _ok({}))

    assert await tiktok.own_videos("") == []
    assert not calls


def test_the_authorize_url_carries_the_state_and_scopes():
    url = tiktok.authorize_url("https://example.test/cb", "st4te", "verifier")

    assert "state=st4te" in url
    assert "user.info.basic" in url
    assert "video.list" in url


# ── PKCE ────────────────────────────────────────────────────────────────────
#
# TikTok refuses the authorize request outright without a `code_challenge`. The
# integration shipped without one and every simulated test passed, because a
# fixture answers whatever it is asked — the real authorize page is what said no,
# on a screen reading "Something went wrong" with `code_challenge` in small print.


def test_the_authorize_url_carries_the_pkce_challenge():
    url = tiktok.authorize_url("https://example.test/cb", "st4te", "a-verifier")

    assert f"code_challenge={tiktok.code_challenge('a-verifier')}" in url
    # S256 names the hash, and is the only method TikTok accepts.
    assert "code_challenge_method=S256" in url


def test_the_challenge_is_hex_encoded_not_base64url():
    """The one detail that fails *after* the user has already said yes.

    RFC 7636 says `BASE64URL(SHA256(verifier))` and every other provider in this
    repo means that, so the obvious implementation sails through the authorize
    step and dies at the token exchange with a bare `invalid_grant`. TikTok's own
    documentation is explicit: "You must use hex encoding of SHA256".
    """
    import base64
    import hashlib

    verifier = "the-quick-brown-fox-jumps-over-the-lazy-dog-0123456789"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()

    challenge = tiktok.code_challenge(verifier)

    assert challenge == digest.hex()
    assert challenge != base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert len(challenge) == 64 and all(c in "0123456789abcdef" for c in challenge)


def test_a_verifier_is_fresh_every_time_and_within_the_rfc_length():
    first, second = tiktok.code_verifier(), tiktok.code_verifier()

    assert first != second, "reusing a verifier defeats the point of PKCE"
    for verifier in (first, second):
        assert 43 <= len(verifier) <= 128
        # RFC 7636 §4.1's unreserved set. A character outside it is rejected by
        # some servers and silently mangled in a query string by others.
        assert all(c.isalnum() or c in "-._~" for c in verifier)


async def test_the_exchange_sends_the_verifier_back(monkeypatch):
    """PKCE is only worth anything if the second half actually happens."""
    calls = _transport(
        monkeypatch,
        lambda _r: _ok({"access_token": "at", "expires_in": 100}),
    )

    await tiktok.exchange_code("code", "https://example.test/cb", "the-verifier")

    body = calls[0].content.decode()
    assert "code_verifier=the-verifier" in body
