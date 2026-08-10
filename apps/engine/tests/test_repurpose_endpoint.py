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
