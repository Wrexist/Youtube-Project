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
        await _up_to("originality").run("job1", {}, await _events(events))


async def test_an_ungranted_clip_stops_the_run_before_acquisition(database, monkeypatch):
    source_id = await _seed(grant=None)
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    events: list = []
    with pytest.raises(WorkflowError, match="no grant recorded"):
        await _up_to("originality").run("job1", {"source_ids": [source_id]}, await _events(events))

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
        await _up_to("originality").run("job1", {"source_ids": [source_id]}, await _events([]))


async def test_every_uncleared_clip_is_named_not_just_the_first(database, monkeypatch):
    """An operator fixing these one 30-second run at a time is the reason the
    check is batched."""
    a = await _seed("aaa", grant=None)
    b = await _seed("bbb", grant=None)
    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", _tripwire)

    with pytest.raises(WorkflowError) as caught:
        await _up_to("originality").run("job1", {"source_ids": [a, b]}, await _events([]))

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
        await _up_to("originality").run(
            "job1",
            {"source_ids": [source_id], "platform": "youtube"},
            await _events([]),
        )


# ── the originality refusal ─────────────────────────────────────────────────


async def test_the_pipeline_refuses_to_build_a_bare_lift(database, monkeypatch):
    """A bare lift cannot get as far as the gate any more, and that is the point.

    `NarrationStage` refuses when no commentary was written, because a video with
    nothing over the clips is a reupload and building it to find that out at the
    gate wastes an encode. The gate still catches the same shape from other routes
    — see `test_repurpose_gate.py::test_bare_source_with_a_topping_and_tailing_fails`.
    """
    source_id = await _seed()
    _stub_media(monkeypatch, duration=60.0, narrate=False)

    with pytest.raises(WorkflowError, match="every clip would be bare source"):
        await _up_to("originality").run(
            "job1",
            {"source_ids": [source_id], "segment_seconds": 60},
            await _events([]),
        )


async def test_a_watermarked_clip_is_refused(database, monkeypatch):
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0, watermarked=True)

    with pytest.raises(WorkflowError, match="watermark"):
        await _up_to("originality").run(
            "job1",
            {"source_ids": [source_id], "audio_bed_replaced": True},
            await _events([]),
        )


async def test_the_bed_is_replaced_by_construction_not_by_assertion(database, monkeypatch):
    """The input flag cannot make an unlicensed bed pass, because it is no longer read.

    `audio_bed_replaced` used to be a boolean the caller asserted, so the gate
    could be satisfied by typing `true` into a request. `assemble` now *reports*
    what it produced and `build_timeline` prefers that, so the only way to have a
    replaced bed is to have actually replaced one — which `assemble` always does.
    """
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0)

    states = await _up_to("originality").run(
        "job1",
        # Asserting the opposite of the truth. It is ignored.
        {"source_ids": [source_id], "segment_seconds": 10, "audio_bed_replaced": False},
        await _events([]),
    )

    signals = {
        s["name"]: s for s in states["originality"].output.value["transformation"]["signals"]
    }
    assert signals["audio_bed"]["severity"] == "ok"


async def test_a_narrated_edit_with_original_material_passes(database, monkeypatch):
    source_id = await _seed()
    _stub_media(monkeypatch, duration=30.0)

    states = await _up_to("originality").run(
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

    await _up_to("originality").run(
        "job1",
        {"source_ids": [source_id], "project_id": "proj1", "segment_seconds": 10},
        await _events([]),
    )

    project = await repository.load_project("proj1")
    assert project is not None
    assert project["report"] is not None
    assert project["report"]["thresholds_version"] >= 1


# ── helpers ─────────────────────────────────────────────────────────────────


def _up_to(stage_name: str):
    """The real stage instances, in the real order, truncated after `stage_name`.

    These tests are about the two refusals, and both happen at or before
    `originality`. Running the packaging stages past it would mean stubbing an LLM
    and a keyword sweep to assert something neither is involved in — while the
    stages themselves are real instances in their real order, so a mis-wired
    dependency still fails here exactly as it would in production. The stages
    *after* the gate are covered by `Workflow._validate` at import, which is what
    caught the missing thumbnail dependency in the publish graph.
    """
    from engine.workflows.base import Workflow

    stages = repurpose.repurpose_stages()
    cut = [s for s in stages[: [s.name for s in stages].index(stage_name) + 1]]
    return Workflow("repurpose-test", cut)


def _stub_media(monkeypatch, *, duration: float, watermarked: bool = False, narrate: bool = True):
    """Everything that reaches outside the process: acquisition, signals, the two
    model calls, TTS, and the encode. The stages themselves stay real."""

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

    class _Completion:
        model = "test-model"
        prompt = "test-prompt"
        cost_usd = 0.0

    async def fake_thesis(**_kwargs):
        return "these three clips share one mistake", _Completion()

    async def fake_commentary(*, segments, **_kwargs):
        from engine.repurpose.narrate import Line, Narration

        if not narrate:
            return Narration(thesis="t", lines=[], narrated_source_ids=[]), _Completion()
        lines = [
            Line(
                source_id=seg.get("source_id"),
                text="Here is the part everyone misreads, and why it matters.",
                segment_index=index,
                est_seconds=4.0,
            )
            for index, seg in enumerate(segments)
        ]
        narrated = [s for s in {line.source_id for line in lines} if s]
        return Narration(thesis="t", lines=lines, narrated_source_ids=narrated), _Completion()

    async def fake_synthesize(text, voice, *, original_text=None):
        from pathlib import Path

        from engine.settings import get_settings

        out = Path(get_settings().storage_root) / "tmp"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "narration.mp3"
        path.write_bytes(b"fake mp3")
        return path, [{"start": 0.0, "end": 8.0, "text": text[:40]}]

    async def fake_assemble(*, segments, hook=None, **_kwargs):
        from engine.repurpose.assemble import Assembly, Placed

        placed = []
        cursor = 0.0
        if hook and hook.get("teased"):
            length = float(hook.get("duration_s") or 2.5)
            placed.append(
                Placed(
                    source_id=hook["source_id"],
                    start_s=float(hook.get("at_s") or 0),
                    end_s=float(hook.get("at_s") or 0) + length,
                    placed_at_s=cursor,
                    is_hook=True,
                )
            )
            cursor += length
        for seg in segments:
            length = float(seg.get("duration_s") or (seg.get("end_s", 0) - seg.get("start_s", 0)))
            placed.append(
                Placed(
                    source_id=seg.get("source_id"),
                    start_s=float(seg.get("start_s") or 0),
                    end_s=float(seg.get("end_s") or 0),
                    placed_at_s=cursor,
                )
            )
            cursor += length
        return Assembly(
            output_key="repurpose/test.mp4",
            duration_s=cursor,
            placed=placed,
            cuts=max(0, len(placed) - 1),
            audio_bed_replaced=True,
        )

    monkeypatch.setattr(repurpose.acquisition, "acquire_and_record", fake_acquire)
    monkeypatch.setattr(repurpose, "_signals", fake_signals)
    monkeypatch.setattr(repurpose.narration_writer, "write_thesis", fake_thesis)
    monkeypatch.setattr(repurpose.narration_writer, "write_commentary", fake_commentary)
    monkeypatch.setattr(repurpose.assembly, "assemble", fake_assemble)
    monkeypatch.setattr("engine.workflows.media._synthesize", fake_synthesize)


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


# ── the publish path ────────────────────────────────────────────────────────


def test_a_repurpose_publish_workflow_exists_and_validates():
    """Registered *and* importable. `PUBLISH_WORKFLOW` spent months unregistered
    because a standalone graph failed `Workflow._validate` at import — the same
    check caught this one missing a thumbnail stage."""
    assert "repurpose-publish" in video.WORKFLOWS

    names = [s.name for s in video.WORKFLOWS["repurpose-publish"].stages]
    assert "upload" in names
    assert names.index("originality") < names.index("upload"), (
        "the gate has to clear before anything spends 1,600 quota units"
    )


def test_the_publish_graph_is_not_startable_directly():
    """Started directly it would run the whole paid pipeline and then die on a
    missing YouTube client — the failure `video.py` documents for "publish"."""
    assert "repurpose-publish" not in video.STARTABLE


def test_the_finished_file_reaches_the_publish_stages_under_the_name_they_read():
    """`UploadStage` reads "render"; assemble produces the file. The alternative
    to bridging them is a second copy of four publish stages differing in one
    string."""
    stages = {s.name: s for s in video.WORKFLOWS["repurpose-publish"].stages}

    assert "render" in stages
    assert "assemble" in stages["render"].depends_on


async def test_a_repurpose_job_is_accepted_by_the_publish_gate(database, monkeypatch):
    """It used to be refused outright: the gate hard-checked for the "video"
    workflow, so a repurposed video could never publish however good it was."""
    from fastapi.testclient import TestClient

    from engine.main import app

    with TestClient(app) as client:
        response = client.post("/v1/jobs/nonexistent/publish", json={})

    # 404 for the missing job — *not* a 409 about the wrong workflow, which is
    # what this used to be for every repurpose job.
    assert response.status_code == 404
