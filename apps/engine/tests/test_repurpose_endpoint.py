"""The Repurpose endpoints.

Mostly about refusals. The screen's job is to stop a clip becoming a video before
anyone has paid for a render, so the interesting assertions are all about what the
API declines to accept.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.main import app


@pytest.fixture
def client(database):
    with TestClient(app) as c:
        yield c


def _seed(client, external_id="aaa"):
    """A discovered clip, via the repository — there is no discovery endpoint yet."""
    import asyncio

    from engine import repository

    async def go():
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

    return asyncio.get_event_loop().run_until_complete(go())


def test_clips_are_listed_with_their_rights_state(client):
    response = client.get("/v1/repurpose/clips?channel_key=main")
    assert response.status_code == 200
    assert "clips" in response.json()


def test_a_grant_without_evidence_is_refused(client):
    """Caught while the operator still has the DM open, not forty minutes into a
    render."""
    response = client.post(
        "/v1/repurpose/clips/nonexistent/grant",
        json={"lane": "licensed", "grantor": "@creator"},
    )
    assert response.status_code == 422
    codes = {p["code"] for p in response.json()["detail"]["problems"]}
    assert "no_evidence" in codes


def test_a_grant_for_an_unknown_clip_is_404(client):
    response = client.post(
        "/v1/repurpose/clips/nonexistent/grant",
        json={
            "lane": "licensed",
            "grantor": "@creator",
            "evidence_kind": "email",
            "evidence_ref": "storage://g/1",
        },
    )
    assert response.status_code == 404


def test_own_lane_needs_no_evidence(client):
    """Lane A is the one with no counterparty. Demanding paperwork for your own
    footage would be theatre."""
    response = client.post(
        "/v1/repurpose/clips/nonexistent/grant",
        json={"lane": "own"},
    )
    # 404 for the missing clip, not 422 — the grant itself was acceptable.
    assert response.status_code == 404


def test_evaluate_reports_both_verdicts_separately(client):
    """A licensed-but-lazy edit must not read as a rights problem."""
    response = client.post(
        "/v1/repurpose/evaluate",
        json={
            "segments": [{"start_s": 0, "end_s": 60, "source_id": "unknown"}],
            "cuts": 0,
            "audio_bed_replaced": True,
            "compared_against": 10,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["publishable"] is False
    assert body["rights"]["cleared"] is False
    assert body["transformation"]["passed"] is False
    # Both named, so the screen can say which to fix.
    assert "rights" in body["headline"] and "original" in body["headline"]


def test_evaluate_passes_a_genuinely_transformative_edit(client):
    response = client.post(
        "/v1/repurpose/evaluate",
        json={
            "segments": [{"start_s": 0, "end_s": 90, "narrated": True}],
            "cuts": 20,
            "audio_bed_replaced": True,
            "compared_against": 10,
        },
    )
    body = response.json()
    assert body["publishable"] is True
    assert body["thresholds_version"] >= 1


def test_evaluate_is_typed_rather_than_a_bare_dict(client):
    """The response model is what `packages/contracts` generates from.

    An endpoint typed `-> dict` produces `Record<string, never>` in TypeScript,
    which is worse than no type: the screen then hand-writes the shape it expects,
    and CLAUDE.md forbids exactly that.
    """
    schema = app.openapi()["paths"]["/v1/repurpose/evaluate"]["post"]
    ref = schema["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("ReportOut")


def test_dismissing_an_unknown_clip_is_404(client):
    assert client.post("/v1/repurpose/clips/nope/dismiss").status_code == 404


# ── the TikTok connection ───────────────────────────────────────────────────


@pytest.fixture
def tiktok_configured(monkeypatch):
    from engine.settings import get_settings

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_connecting_without_credentials_says_what_to_set(client):
    from engine.settings import get_settings

    get_settings.cache_clear()
    response = client.get("/v1/repurpose/auth/tiktok")

    assert response.status_code == 409
    # The name that actually works. `validation_alias` overrides `env_prefix`, so
    # the STUDIO_-prefixed form reads as unset.
    assert "TIKTOK_CLIENT_KEY" in response.json()["detail"]
    assert "STUDIO_TIKTOK" not in response.json()["detail"]


def test_the_authorize_url_is_returned_rather_than_redirected(client, tiktok_configured):
    """A server following the redirect would authorise the server rather than the
    person sitting in front of it."""
    response = client.get("/v1/repurpose/auth/tiktok")

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://www.tiktok.com/")


def test_a_callback_with_an_unknown_state_is_refused(client, tiktok_configured):
    """The standard OAuth CSRF: without this an attacker walks a victim through a
    link that connects the *attacker's* TikTok to the victim's install, and every
    clip swept afterwards is the attacker's."""
    response = client.get(
        "/v1/repurpose/auth/tiktok/callback?code=abc&state=never-issued",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "tiktok_error" in response.headers["location"]


def test_a_state_cannot_be_replayed(client, tiktok_configured, monkeypatch):
    from engine.api import repurpose as api
    from engine.providers import tiktok as provider

    async def fake_exchange(_code, _redirect):
        return provider.Tokens(access_token="at", refresh_token="rt")

    async def fake_handle(_token):
        return "@me"

    monkeypatch.setattr(provider, "exchange_code", fake_exchange)
    monkeypatch.setattr(provider, "creator_handle", fake_handle)

    state = next(iter(api._PENDING_STATES)) if api._PENDING_STATES else None
    if state is None:
        client.get("/v1/repurpose/auth/tiktok")
        state = next(iter(api._PENDING_STATES))

    first = client.get(
        f"/v1/repurpose/auth/tiktok/callback?code=abc&state={state}", follow_redirects=False
    )
    second = client.get(
        f"/v1/repurpose/auth/tiktok/callback?code=abc&state={state}", follow_redirects=False
    )

    assert "tiktok=connected" in first.headers["location"]
    assert "tiktok_error" in second.headers["location"]


def test_a_denied_consent_comes_back_as_a_readable_error(client, tiktok_configured):
    response = client.get(
        "/v1/repurpose/auth/tiktok/callback?error=access_denied&error_description=User+declined",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "User" in response.headers["location"]


def test_status_reports_configured_and_connected_separately(client, tiktok_configured):
    """Three not-working states with three different fixes. Collapsing them is how
    "it shows nothing" becomes unanswerable."""
    response = client.get("/v1/repurpose/auth/tiktok/status")

    body = response.json()
    assert body["configured"] is True
    assert body["account"] is None


def test_discovery_without_a_connection_is_not_an_error(client, tiktok_configured):
    """An ordinary un-connected install must not look broken."""
    response = client.post("/v1/repurpose/discover", json={"channel_key": "main"})

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["connected"] is False
    assert body["clips"] == []


def test_discovery_takes_no_access_token_from_the_caller(client):
    """It used to. That made the caller responsible for a credential that expires
    every 24 hours, so the obvious client caches it and the sweep starts failing
    the next day for a reason invisible from the outside."""
    from engine.main import app

    schema = app.openapi()["components"]["schemas"]["DiscoverRequest"]

    assert "access_token" not in schema["properties"]
