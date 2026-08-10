"""The repurpose workflow, and the two places it refuses.

`RightsStage` is second in the graph and is the only stage in this repository whose
job is to refuse. `OriginalityStage` is the other refusal, and it runs before the
SEO stages so a blocked video does not pay for a title it will never use. Between
them they are the whole point of the workflow, so most of what is tested here is
that they actually stop things.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine import repository
from engine.repurpose.rights import Grant, Lane, own
from engine.workflows import repurpose, video
from engine.workflows.base import WorkflowError


async def _events(sink: list):
    async def emit(event: dict) -> None:
        sink.append(event)

    return emit


#: Sentinel so "no grant" and "the default grant" stay distinguishable — a plain
#: None default would make `grant=None` mean "use the default", which is the exact
#: opposite of what every refusal test needs it to mean.
_DEFAULT_GRANT = object()


async def _seed(external_id="aaa", *, grant=_DEFAULT_GRANT) -> str:
    await repository.upsert_clip_sources(
        [{"platform": "tiktok", "external_id": external_id, "duration_s": 20}],
        channel_key="main",
    )
    clips = await repository.clip_sources(channel_key="main")
    source_id = next(c["id"] for c in clips if c["external_id"] == external_id)
    if grant is _DEFAULT_GRANT:
        grant = own()
    if grant is not None:
        await repository.record_grant(source_id, grant)
    return source_id


# ── registration ────────────────────────────────────────────────────────────


def test_the_workflow_is_registered_and_startable():
    assert "repurpose" in video.WORKFLOWS
    assert "repurpose" in video.STARTABLE


def test_rights_runs_before_anything_is_fetched_or_spent():
    """Order is the entire design. A rights check after the download is a
    check on a file already on disk."""
    names = [s.name for s in repurpose.repurpose_stages()]

    assert names.index("rights") < names.index("acquire")
    assert names.index("acquire") < names.index("segment")
    assert names.index("segment") < names.index("originality")


def test_the_refusing_stages_do_not_retry():
    """A missing licence is not a transient error, and retrying it three times
    with backoff only delays the same answer while looking like a network fault."""
    stages = {s.name: s for s in repurpose.repurpose_stages()}

    assert stages["rights"].max_attempts == 1
    assert stages["originality"].max_attempts == 1


# ── the rights refusal ──────────────────────────────────────────────────────


async def test_a_run_with_no_clips_is_refused(database):
    events: list = []
    with pytest.raises(WorkflowError, match="no clips selected"):
        await repurpose.REPURPOSE_WORKFLOW.run("job1", {}, await _events(events))


async def test_an_ungranted_clip_stops_the_run_before_acquisition(database, monkeypatch):
    source_id = await _seed(grant=None)
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    events: list = []
    with pytest.raises(WorkflowError, match="no grant recorded"):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1", {"source_ids": [source_id]}, await _events(events)
        )

    assert not any(e.get("stage") == "acquire" for e in events if e["type"] == "stage.started")


async def test_an_expired_grant_stops_the_run(database, monkeypatch):
    source_id = await _seed(
        grant=Grant(
            lane=Lane.CAMPAIGN,
            grantor="@streamer",
            evidence_kind="campaign_enrolment",
            evidence_ref="https://whop.example/c/1",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    with pytest.raises(WorkflowError, match="ran out"):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1", {"source_ids": [source_id]}, await _events([])
        )


async def test_every_uncleared_clip_is_named_not_just_the_first(database, monkeypatch):
    """An operator fixing these one 30-second run at a time is the reason the
    check is batched."""
    a = await _seed("aaa", grant=None)
    b = await _seed("bbb", grant=None)
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    with pytest.raises(WorkflowError) as caught:
        await repurpose.REPURPOSE_WORKFLOW.run("job1", {"source_ids": [a, b]}, await _events([]))

    assert a in str(caught.value)
    assert b in str(caught.value)


async def test_a_platform_the_grant_does_not_cover_is_refused(database, monkeypatch):
    source_id = await _seed(
        grant=Grant(
            lane=Lane.LICENSED,
            grantor="@creator",
            evidence_kind="email",
            evidence_ref="storage://g/1",
            platforms=frozenset({"tiktok"}),
        )
    )
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    with pytest.raises(WorkflowError, match="not youtube"):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1",
            {"source_ids": [source_id], "platform": "youtube"},
            await _events([]),
        )


# ── the originality refusal ─────────────────────────────────────────────────


async def test_a_bare_lift_is_refused_at_the_gate(database, monkeypatch):
    """Rights fine, edit lazy. The message must say which."""
    source_id = await _seed()
    _stub_media(monkeypatch, duration=60.0)

    with pytest.raises(WorkflowError) as caught:
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1",
            {"source_ids": [source_id], "segment_seconds": 60, "audio_bed_replaced": True},
            await _events([]),
        )

    message = str(caught.value)
    assert "Blocked on originality" in message
    assert "unbroken lift" in message or "original narration" in message


async def test_a_watermarked_clip_is_refused(database, monkeypatch):
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0, watermarked=True)

    with pytest.raises(WorkflowError, match="watermark"):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1",
            {"source_ids": [source_id], "audio_bed_replaced": True},
            await _events([]),
        )


async def test_an_unreplaced_audio_bed_is_refused(database, monkeypatch):
    """TikTok music licences cover TikTok. Video rights do not help."""
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0)

    with pytest.raises(WorkflowError, match="audio bed"):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1",
            {"source_ids": [source_id], "audio_bed_replaced": False},
            await _events([]),
        )


async def test_a_narrated_edit_with_original_material_passes(database, monkeypatch):
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0)

    states = await repurpose.REPURPOSE_WORKFLOW.run(
        "job1",
        {
            "source_ids": [source_id],
            "segment_seconds": 10,
            "audio_bed_replaced": True,
            "cut_count": 20,
            # 40s of our own narrated footage around a 10s clip.
            "original_segments": [{"duration_s": 40}],
        },
        await _events([]),
    )

    report = states["originality"].output.value
    assert report["publishable"] is True
    assert report["thresholds_version"] >= 1


async def test_the_report_is_stored_against_the_project(database, monkeypatch):
    """It carries the threshold version that judged it, which cannot be
    reconstructed once the thresholds move."""
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0)
    await repository.save_project("proj1", channel_key="main")

    with pytest.raises(WorkflowError):
        await repurpose.REPURPOSE_WORKFLOW.run(
            "job1",
            {"source_ids": [source_id], "project_id": "proj1", "segment_seconds": 60},
            await _events([]),
        )

    project = await repository.load_project("proj1")
    assert project is not None
    assert project["report"] is not None
    assert project["report"]["publishable"] is False


# ── helpers ─────────────────────────────────────────────────────────────────


def _stub_media(monkeypatch, *, duration: float, watermarked: bool = False):
    """Acquisition and signal extraction, without media."""

    async def fake_acquire(source_id, _url):
        from engine.repurpose.acquire import Acquired

        return Acquired(
            storage_key=f"clips/{source_id}.mp4",
            sha256="0" * 64,
            duration_s=duration,
            width=1080,
            height=1920,
            has_watermark=watermarked,
            watermark_regions=[{"region": "top-right"}] if watermarked else [],
        )

    async def fake_signals(_asset):
        # A clean rise a third of the way in, so `choose_segment` has something to
        # find rather than declining and falling back to the opening.
        windows = max(3, int(duration))
        energy = [0.3] * windows
        for i in range(windows // 3, min(windows // 3 + 6, windows)):
            energy[i] = 0.9
        return {"energy": energy, "speech": energy, "motion": [0.1] * windows}

    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", fake_acquire)
    monkeypatch.setattr(repurpose, "_signals", fake_signals)


async def _tripwire(*_a, **_k):
    raise AssertionError("acquisition must not run before rights clear")


# ── reachable from the API ──────────────────────────────────────────────────


async def test_a_repurpose_job_can_be_started_through_the_jobs_endpoint(database):
    """Registered but unreachable is the state `PUBLISH_WORKFLOW` was in for
    months — see the note in workflows/video.py."""
    from fastapi.testclient import TestClient

    from engine.main import app

    source_id = await _seed()

    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            json={
                "topic": "three clips about the same mistake",
                "workflow": "repurpose",
                "repurpose": {"source_ids": [source_id], "audio_bed_replaced": True},
            },
        )

    assert response.status_code == 202


def test_repurpose_inputs_are_flattened_onto_the_job():
    """Stages read `ctx.inputs[...]` flat. A nested dict would reach every one of
    them as a dict they would each have to unpack."""
    from engine.main import JobRequest

    body = JobRequest(
        topic="a topic",
        workflow="repurpose",
        repurpose={"source_ids": ["a", "b"], "audio_bed_replaced": True},
    )
    inputs = body.model_dump()
    nested = inputs.pop("repurpose") or {}
    inputs.update(nested)

    assert inputs["source_ids"] == ["a", "b"]
    assert inputs["audio_bed_replaced"] is True
    assert "repurpose" not in inputs
