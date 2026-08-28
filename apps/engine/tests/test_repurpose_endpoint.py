"""The Repurpose endpoints.

Mostly about refusals. The screen's job is to stop a clip becoming a video before
anyone has paid for a render, so the interesting assertions are all about what the
API declines to accept.

**The endpoints are called directly rather than through `TestClient`.** This file
used to use one, which is the split `test_spend.py` writes up at length: `TestClient`
runs the app on a blocking portal with its own event loop, and these tests also
write rows on pytest's loop. aiosqlite tolerates a pool shared across two loops and
asyncpg does not — so every test here passed locally and all sixteen errored on CI
with "attached to a different loop", in a file whose own logic was fine. The rest of
the suite already splits this way: `database`-fixture tests on one side, `TestClient`
tests on the other.

A refusal that reaches the router as an `HTTPException` is asserted as one. That is
what the client sees as a status code, and checking it here rather than through a
response object keeps the assertion about the endpoint rather than about Starlette.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from engine import repository
from engine.api import repurpose as api


async def _seed(external_id="aaa") -> str:
    """A discovered clip, via the repository — there is no discovery endpoint yet."""
    await repository.upsert_clip_sources(
        [
            {
                "platform": "tiktok",
                "external_id": external_id,
                "url": f"https://tiktok.example/v/{external_id}",
                "creator_handle": "@someone",
                "caption": "a caption",
                "duration_s": 24.0,
                "fit_score": 0.8,
            }
        ],
        channel_key="main",
    )
    clips = await repository.clip_sources(channel_key="main")
    return clips[0]["id"]


def _grant(**kw) -> api.GrantIn:
    return api.GrantIn(**kw)


def _timeline(**kw) -> api.TimelineIn:
    return api.TimelineIn(**kw)


async def _clips(channel_key="main") -> api.Clips:
    """`api.clips`, with its query defaults supplied.

    Called directly, FastAPI's `Query(50, ...)` arrives as a `Query` object rather
    than as 50 — the framework substitutes it per request, and there is no request
    here. Passing it explicitly is the price of not using `TestClient`, and it is
    a good deal cheaper than a pool shared across two event loops.
    """
    return await api.clips(channel_key=channel_key, status="discovered", limit=50)


async def test_clips_are_listed_with_their_rights_state(database):
    listed = await _clips()

    assert listed.clips == []


async def test_a_listed_clip_carries_its_grant(database):
    """The rights chip is drawn from this. A second round trip per card to find
    out whether a clip is usable would make the grid useless."""
    source_id = await _seed()

    listed = await _clips()

    assert [c.id for c in listed.clips] == [source_id]
    assert listed.clips[0].grant is None
    assert listed.clips[0].cleared is False


async def test_a_grant_without_evidence_is_refused(database):
    """Caught while the operator still has the DM open, not forty minutes into a
    render."""
    with pytest.raises(HTTPException) as raised:
        await api.record_grant("nonexistent", _grant(lane="licensed", grantor="@creator"))

    assert raised.value.status_code == 422
    codes = {p["code"] for p in raised.value.detail["problems"]}
    assert "no_evidence" in codes


async def test_a_grant_for_an_unknown_clip_is_404(database):
    with pytest.raises(HTTPException) as raised:
        await api.record_grant(
            "nonexistent",
            _grant(
                lane="licensed",
                grantor="@creator",
                evidence_kind="email",
                evidence_ref="storage://g/1",
            ),
        )

    assert raised.value.status_code == 404


async def test_own_lane_needs_no_evidence(database):
    """Lane A is the one with no counterparty. Demanding paperwork for your own
    footage would be theatre."""
    with pytest.raises(HTTPException) as raised:
        await api.record_grant("nonexistent", _grant(lane="own"))

    # 404 for the missing clip, not 422 — the grant itself was acceptable.
    assert raised.value.status_code == 404


async def test_a_recorded_grant_clears_the_clip(database):
    source_id = await _seed()

    stored = await api.record_grant(source_id, _grant(lane="own"))

    assert stored.cleared is True
    assert stored.lane == "own"


# ── revoking ────────────────────────────────────────────────────────────────
#
# The system could record permission and enforce it, and gave an operator no way
# to take it back: the only route was writing to the repository by hand. A
# creator who changes their mind is the most ordinary rights event there is.


async def test_revoking_withdraws_permission_and_the_clip_stops_being_cleared(database):
    source_id = await _seed()
    await api.record_grant(source_id, _grant(lane="own"))
    assert (await _clips()).clips[0].grant.cleared is True

    revoked = await api.revoke_grant(source_id)

    assert revoked.cleared is False
    assert revoked.revoked_at is not None
    assert (await _clips()).clips[0].grant.cleared is False


async def test_revoking_appends_rather_than_erasing_the_original_grant(database):
    """The old row answers "were we allowed to publish that, when we published it".

    Mutating the standing grant would erase exactly the evidence a rights
    question needs, and a rights question always arrives after the fact.
    """
    source_id = await _seed()
    await api.record_grant(source_id, _grant(lane="own"))

    await api.revoke_grant(source_id)

    from sqlalchemy import func, select

    from engine.db import session
    from engine.tables import ClipGrant

    grants = await repository.grants_for([source_id])
    assert grants[source_id].revoked() is True

    # Both rows survive: the grant and its withdrawal.
    async with session() as db:
        count = await db.scalar(
            select(func.count()).select_from(ClipGrant).where(ClipGrant.source_id == source_id)
        )
    assert count == 2


async def test_revoking_twice_is_someone_making_sure_not_an_error(database):
    source_id = await _seed()
    await api.record_grant(source_id, _grant(lane="own"))
    first = await api.revoke_grant(source_id)

    second = await api.revoke_grant(source_id)

    assert second.cleared is False
    assert second.revoked_at == first.revoked_at, "the standing revocation is returned unchanged"


async def test_revoking_a_clip_with_no_grant_is_404(database):
    source_id = await _seed()

    with pytest.raises(HTTPException) as raised:
        await api.revoke_grant(source_id)

    assert raised.value.status_code == 404


async def test_a_revoked_clips_media_can_no_longer_be_recorded(database):
    """The invariant the whole feature rests on: no live grant, no stored media."""
    source_id = await _seed()
    await api.record_grant(source_id, _grant(lane="own"))
    await api.revoke_grant(source_id)

    with pytest.raises(PermissionError):
        await repository.record_asset(
            source_id,
            {"storage_key": "clips/x.mp4", "sha256": "0" * 64, "duration_s": 4.0},
        )


async def test_evaluate_reports_both_verdicts_separately(database):
    """A licensed-but-lazy edit must not read as a rights problem."""
    report = await api.evaluate_timeline(
        _timeline(
            segments=[{"start_s": 0, "end_s": 60, "source_id": "unknown"}],
            cuts=0,
            audio_bed_replaced=True,
            compared_against=10,
        )
    )

    body = report.model_dump()
    assert body["publishable"] is False
    assert body["rights"]["cleared"] is False
    assert body["transformation"]["passed"] is False
    # Both named, so the screen can say which to fix.
    assert "rights" in body["headline"] and "original" in body["headline"]


async def test_evaluate_passes_a_genuinely_transformative_edit(database):
    report = await api.evaluate_timeline(
        _timeline(
            segments=[{"start_s": 0, "end_s": 90, "narrated": True}],
            cuts=20,
            audio_bed_replaced=True,
            compared_against=10,
        )
    )

    body = report.model_dump()
    assert body["publishable"] is True
    assert body["thresholds_version"] >= 1


async def test_dismissing_an_unknown_clip_is_404(database):
    with pytest.raises(HTTPException) as raised:
        await api.dismiss("nope")

    assert raised.value.status_code == 404


async def test_a_dismissed_clip_leaves_the_grid(database):
    """Discovery re-runs on the same data, so a dismissal that did not stick would
    re-propose the same reject tomorrow."""
    source_id = await _seed()

    await api.dismiss(source_id)

    assert await _clips() == api.Clips(clips=[])


# ── the schema, which needs no database ─────────────────────────────────────


def test_evaluate_is_typed_rather_than_a_bare_dict():
    """The response model is what `packages/contracts` generates from.

    An endpoint typed `-> dict` produces `Record<string, never>` in TypeScript,
    which is worse than no type: the screen then hand-writes the shape it expects,
    and CLAUDE.md forbids exactly that.
    """
    from engine.main import app

    schema = app.openapi()["paths"]["/v1/repurpose/evaluate"]["post"]
    ref = schema["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("ReportOut")


def test_discovery_takes_no_access_token_from_the_caller():
    """It used to. That made the caller responsible for a credential that expires
    every 24 hours, so the obvious client caches it and the sweep starts failing
    the next day for a reason invisible from the outside."""
    from engine.main import app

    schema = app.openapi()["components"]["schemas"]["DiscoverRequest"]

    assert "access_token" not in schema["properties"]


def test_the_tiktok_status_is_typed_too():
    """Same reasoning as `evaluate`. `configured` and `connected` are the two
    fields the Setup screen branches on, and a `Record<string, never>` would make
    it guess them."""
    from engine.main import app

    schema = app.openapi()["paths"]["/v1/repurpose/auth/tiktok/status"]["get"]
    ref = schema["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("TikTokStatusOut")


# ── the TikTok connection ───────────────────────────────────────────────────


@pytest.fixture
def tiktok_configured(monkeypatch):
    from engine.settings import get_settings

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_connecting_without_credentials_says_what_to_set():
    from engine.settings import get_settings

    get_settings.cache_clear()

    with pytest.raises(HTTPException) as raised:
        await api.begin_tiktok_auth()

    assert raised.value.status_code == 409
    # The name that actually works. `validation_alias` overrides `env_prefix`, so
    # the STUDIO_-prefixed form reads as unset.
    assert "TIKTOK_CLIENT_KEY" in raised.value.detail
    assert "STUDIO_TIKTOK" not in raised.value.detail


async def test_the_authorize_url_is_returned_rather_than_redirected(tiktok_configured):
    """A server following the redirect would authorise the server rather than the
    person sitting in front of it."""
    result = await api.begin_tiktok_auth()

    assert result["url"].startswith("https://www.tiktok.com/")


async def test_a_callback_with_an_unknown_state_is_refused(tiktok_configured):
    """The standard OAuth CSRF: without this an attacker walks a victim through a
    link that connects the *attacker's* TikTok to the victim's install, and every
    clip swept afterwards is the attacker's."""
    response = await api.tiktok_callback(code="abc", state="never-issued")

    assert response.status_code == 303
    assert "tiktok_error" in response.headers["location"]


async def test_a_state_cannot_be_replayed(database, tiktok_configured, monkeypatch):
    from engine.providers import tiktok as provider

    async def fake_exchange(_code, _redirect):
        return provider.Tokens(access_token="at", refresh_token="rt")

    async def fake_handle(_token):
        return "@me"

    monkeypatch.setattr(provider, "exchange_code", fake_exchange)
    monkeypatch.setattr(provider, "creator_handle", fake_handle)

    await api.begin_tiktok_auth()
    state = next(iter(api._PENDING_STATES))

    first = await api.tiktok_callback(code="abc", state=state)
    second = await api.tiktok_callback(code="abc", state=state)

    assert "tiktok=connected" in first.headers["location"]
    assert "tiktok_error" in second.headers["location"]


async def test_a_denied_consent_comes_back_as_a_readable_error(tiktok_configured):
    response = await api.tiktok_callback(error="access_denied", error_description="User declined")

    assert response.status_code == 303
    assert "User" in response.headers["location"]


async def test_status_reports_configured_and_connected_separately(database, tiktok_configured):
    """Three not-working states with three different fixes. Collapsing them is how
    "it shows nothing" becomes unanswerable."""
    status = await api.tiktok_status()

    assert status.configured is True
    assert status.account is None


async def test_discovery_without_a_connection_is_not_an_error(database, tiktok_configured):
    """An ordinary un-connected install must not look broken."""
    found = await api.discover(api.DiscoverRequest(channel_key="main"))

    assert found.configured is True
    assert found.connected is False
    assert found.clips == []
