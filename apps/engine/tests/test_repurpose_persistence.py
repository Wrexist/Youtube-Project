"""Clips, grants and assets against a real database.

The invariant under test throughout: **media cannot exist without a live grant.**
`record_asset` enforces it rather than trusting the acquire stage to remember,
because the failure it prevents — a storage directory full of other people's video
with no record of why any of it is there — is not one you can clean up after.

Called directly rather than through `TestClient`, per the note in `test_spend.py`:
the app runs on its own event loop and asyncpg will not share a pool across two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine import repository
from engine.repurpose.rights import Grant, Lane, own


def _clip(external_id: str, *, fit: float = 0.5, platform: str = "tiktok") -> dict:
    return {
        "platform": platform,
        "external_id": external_id,
        "url": f"https://tiktok.example/v/{external_id}",
        "creator_handle": "@someone",
        "caption": "a caption",
        "hashtags": ["#a"],
        "duration_s": 24.0,
        "fit_score": fit,
        "fit_reasons": ["adjacent to your last three"],
    }


async def _one_clip(channel_key: str = "main") -> str:
    await repository.upsert_clip_sources([_clip("aaa")], channel_key=channel_key)
    clips = await repository.clip_sources(channel_key=channel_key)
    return clips[0]["id"]


# ── discovery ───────────────────────────────────────────────────────────────


async def test_clips_persist_and_come_back_best_fit_first(database):
    await repository.upsert_clip_sources(
        [_clip("low", fit=0.2), _clip("high", fit=0.9)], channel_key="main"
    )

    clips = await repository.clip_sources(channel_key="main")

    assert [c["external_id"] for c in clips] == ["high", "low"]


async def test_rediscovering_a_clip_does_not_resurrect_a_dismissal(database):
    """Discovery re-runs on the same trend data. A dismissal has to outlive that."""
    source_id = await _one_clip()
    await repository.set_clip_status(source_id, "dismissed")

    added = await repository.upsert_clip_sources([_clip("aaa")], channel_key="main")

    assert added == 0
    assert await repository.clip_sources(channel_key="main") == []
    dismissed = await repository.clip_sources(channel_key="main", status="dismissed")
    assert len(dismissed) == 1


async def test_the_same_id_on_two_platforms_is_two_clips(database):
    added = await repository.upsert_clip_sources(
        [_clip("x", platform="tiktok"), _clip("x", platform="reels")]
    )
    assert added == 2


async def test_a_batch_containing_the_same_clip_twice_inserts_it_once(database):
    """The scorer has no reason to guarantee distinct results."""
    assert await repository.upsert_clip_sources([_clip("dup"), _clip("dup")]) == 1


# ── grants ──────────────────────────────────────────────────────────────────


async def test_a_grant_round_trips(database):
    source_id = await _one_clip()
    expires = datetime.now(UTC) + timedelta(days=30)

    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.CAMPAIGN,
            grantor="@streamer",
            evidence_kind="campaign_enrolment",
            evidence_ref="https://whop.example/c/1",
            granted_at=datetime.now(UTC),
            expires_at=expires,
            platforms=frozenset({"youtube"}),
            rules="credit in description",
        ),
    )

    grant = await repository.latest_grant(source_id)
    assert grant is not None
    assert grant.lane is Lane.CAMPAIGN
    assert grant.grantor == "@streamer"
    assert grant.platforms == frozenset({"youtube"})
    assert grant.rules == "credit in description"
    assert grant.cleared()


async def test_grants_append_rather_than_replace(database):
    """A superseded grant is what answers "were we allowed to publish that, then".

    An update would erase exactly the record that question needs.
    """
    source_id = await _one_clip()
    await repository.record_grant(source_id, Grant(lane=Lane.COMMENTARY))
    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.LICENSED,
            grantor="@creator",
            evidence_kind="email",
            evidence_ref="storage://g/1",
        ),
    )

    latest = await repository.latest_grant(source_id)
    assert latest is not None
    assert latest.lane is Lane.LICENSED, "the newest grant wins"


async def test_a_grant_for_an_unknown_clip_is_refused(database):
    with pytest.raises(KeyError):
        await repository.record_grant("nope", own())


async def test_grants_for_reads_several_at_once(database):
    await repository.upsert_clip_sources([_clip("a"), _clip("b")], channel_key="main")
    clips = await repository.clip_sources(channel_key="main")
    for clip in clips:
        await repository.record_grant(clip["id"], own())

    grants = await repository.grants_for([c["id"] for c in clips])

    assert len(grants) == 2
    assert all(g.lane is Lane.OWN for g in grants.values())


async def test_the_clip_list_carries_its_grant(database):
    """The rights chip decides whether the card is usable — it cannot need a
    second round trip per card."""
    source_id = await _one_clip()
    await repository.record_grant(source_id, own())

    clip = (await repository.clip_sources(channel_key="main"))[0]

    assert clip["cleared"] is True
    assert clip["grant"]["lane"] == "own"


async def test_a_clip_with_no_grant_reports_that_plainly(database):
    await _one_clip()
    clip = (await repository.clip_sources(channel_key="main"))[0]
    assert clip["grant"] is None
    assert clip["cleared"] is False


# ── the invariant ───────────────────────────────────────────────────────────


async def test_media_cannot_be_stored_without_a_grant(database):
    source_id = await _one_clip()

    with pytest.raises(PermissionError, match="no grant"):
        await repository.record_asset(source_id, {"storage_key": "k", "sha256": "h"})


async def test_media_cannot_be_stored_under_an_expired_grant(database):
    source_id = await _one_clip()
    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.CAMPAIGN,
            grantor="@streamer",
            evidence_kind="campaign_enrolment",
            evidence_ref="https://whop.example/c/1",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
    )

    with pytest.raises(PermissionError, match="no longer live"):
        await repository.record_asset(source_id, {"storage_key": "k", "sha256": "h"})


async def test_media_cannot_be_stored_under_a_revoked_grant(database):
    source_id = await _one_clip()
    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.LICENSED,
            grantor="@creator",
            evidence_kind="email",
            evidence_ref="storage://g/1",
            revoked_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    )

    with pytest.raises(PermissionError, match="no longer live"):
        await repository.record_asset(source_id, {"storage_key": "k", "sha256": "h"})


async def test_media_stores_under_a_live_grant(database):
    source_id = await _one_clip()
    await repository.record_grant(source_id, own())

    asset_id = await repository.record_asset(
        source_id,
        {"storage_key": "clips/aaa.mp4", "sha256": "abc", "duration_s": 24.0, "width": 1080},
    )

    assert asset_id
    clip = (await repository.clip_sources(channel_key="main"))[0]
    assert clip["acquired"] is True


# ── projects ────────────────────────────────────────────────────────────────


async def test_a_project_keeps_its_report_verbatim(database):
    """The report records the threshold version that judged it. Recomputing it
    later would answer a different question with today's numbers."""
    await repository.save_project(
        "proj1",
        channel_key="main",
        thesis="three clips about the same mistake",
        segments=[{"source_id": "aaa", "start_s": 0, "end_s": 10}],
        report={"publishable": False, "thresholds_version": 1},
    )

    project = await repository.load_project("proj1")

    assert project is not None
    assert project["thesis"].startswith("three clips")
    assert project["report"]["thresholds_version"] == 1


async def test_saving_a_project_twice_updates_it(database):
    await repository.save_project("proj1", channel_key="main", thesis="first")
    await repository.save_project("proj1", thesis="second")

    project = await repository.load_project("proj1")
    assert project is not None
    assert project["thesis"] == "second"
    assert project["channel_key"] == "main", "an unspecified field is left alone"
