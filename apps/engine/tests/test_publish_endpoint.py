"""Tests for `POST /v1/jobs/{job_id}/publish` — the approval gate.

`CLAUDE.md` non-negotiable #3: nothing publishes without an explicit approval gate.
This endpoint *is* that gate, which is why publishing is not a stage of the `video`
workflow — a workflow that publishes as its last step has no gate at all.

Every refusal below must stay a refusal. Each one is cheap to check and expensive to
get wrong: a bad publish costs 1,600 quota units and puts something live on a real
channel under the user's name.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import publishing
from engine.main import JOBS, app
from engine.workflows import video
from engine.workflows.base import StageStatus


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    JOBS.clear()
    publishing.CHANNELS.clear()


def _finished_video_job(job_id: str = "src", *, complete: bool = True) -> dict:
    """A completed video job whose stages all carry plausible output."""
    wf = video.get("video")
    states = wf.initial_states()

    class Title:
        text = "Why bridges collapse"
        strategy = "curiosity"

    class Grounding:
        is_grounded = True

    class Critique:
        severity = 1

    values = {
        "grounding": Grounding(),
        "research": {"digest": "…", "sources": ["https://example.test/a"]},
        "titles": [Title()],
        "description": "A description.",
        "tags": ["bridges"],
        "render": "renders/src.mp4",
        "thumbnail": [{"key": "thumbnails/src-0.jpg"}],
        "subtitles": [{"start": 0.0, "end": 1.0, "text": "Hi."}],
    }

    for name, state in states.items():
        state.status = StageStatus.DONE
        if name in values:
            from engine.workflows.base import Provenance, StageOutput

            state.output = StageOutput(value=values[name], provenance=Provenance())
        elif complete:
            from engine.workflows.base import Provenance, StageOutput

            state.output = StageOutput(value="x", provenance=Provenance())

    job = {
        "id": job_id,
        "workflow": wf,
        "inputs": {"topic": "why bridges collapse"},
        "states": states,
        "queue": None,
        "events": [],
        "status": "completed",
    }
    JOBS[job_id] = job
    return job


def _connect_channel():
    from engine.providers.youtube import Credentials

    publishing.CHANNELS["default"] = Credentials(
        refresh_token_encrypted="enc", access_token="tok", channel_id="UC123"
    )


# ── the gate refuses ────────────────────────────────────────────────────────


def test_unknown_job_is_404(client):
    assert client.post("/v1/jobs/nope/publish", json={}).status_code == 404


def test_a_running_job_cannot_publish(client):
    job = _finished_video_job()
    job["status"] = "running"
    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 409
    assert "running" in resp.json()["detail"]


def test_a_non_video_job_cannot_publish(client):
    job = _finished_video_job()
    job["workflow"] = video.get("seo")
    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 409
    assert "seo" in resp.json()["detail"]


def test_publishing_without_a_connected_channel_is_refused(client):
    _finished_video_job()
    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 409
    assert "no channel connected" in resp.json()["detail"]


def test_quality_blockers_stop_the_publish(client):
    """The whole point of the gate: an ungrounded or unfinished video does not ship."""
    job = _finished_video_job()
    _connect_channel()
    # Strip the thumbnail and the sources — two independent blockers.
    job["states"]["thumbnail"].output = None
    job["states"]["research"].output = None

    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    codes = {b["code"] for b in detail["blockers"]}
    assert {"ungrounded", "no_thumbnail"} <= codes
    # Every blocker carries a readable reason, not just a code.
    assert all(b["message"] for b in detail["blockers"])


def test_the_gate_reports_all_blockers_at_once(client):
    """One round trip should tell the operator everything to fix."""
    job = _finished_video_job()
    _connect_channel()
    for name in ("thumbnail", "research", "description", "tags"):
        job["states"][name].output = None

    detail = client.post("/v1/jobs/src/publish", json={}).json()["detail"]
    assert len(detail["blockers"]) >= 3


def test_exhausted_quota_refuses_before_the_spend(client, monkeypatch):
    """1,600 units is most of a day's budget — refuse before, never after."""
    _finished_video_job()
    _connect_channel()
    from engine import main as main_mod

    monkeypatch.setattr(main_mod.ledger, "can_afford", lambda *_a, **_kw: False)
    monkeypatch.setattr(main_mod.ledger, "remaining", lambda *_a, **_kw: 200)

    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 429
    assert "quota" in resp.json()["detail"]


# ── the gate admits ─────────────────────────────────────────────────────────


def test_a_ready_video_starts_a_publish_job(client, monkeypatch):
    _finished_video_job()
    _connect_channel()

    started: dict = {}

    async def fake_run(job_id, start_from=None):
        started["job_id"] = job_id
        JOBS[job_id]["status"] = "completed"

    from engine import main as main_mod

    monkeypatch.setattr(main_mod, "_run_job", fake_run)

    resp = client.post("/v1/jobs/src/publish", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert body["source_job_id"] == "src"

    publish_job = JOBS[body["job_id"]]
    assert publish_job["workflow"].name == "publish"
    # The video stages are seeded DONE so nothing expensive re-runs.
    assert publish_job["states"]["render"].status is StageStatus.DONE
    assert publish_job["states"]["upload"].status is StageStatus.PENDING
    assert publish_job["inputs"]["youtube_client"] is not None


def test_operator_choices_reach_the_upload_stage(client, monkeypatch):
    _finished_video_job()
    _connect_channel()

    from engine import main as main_mod

    async def noop(job_id, start_from=None):
        JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(main_mod, "_run_job", noop)

    resp = client.post(
        "/v1/jobs/src/publish",
        json={
            "chosen_title_index": 2,
            "privacy": "unlisted",
            "playlist_id": "PL123",
            "made_for_kids": True,
        },
    )
    inputs = JOBS[resp.json()["job_id"]]["inputs"]
    assert inputs["chosen_title_index"] == 2
    assert inputs["privacy"] == "unlisted"
    assert inputs["playlist_id"] == "PL123"
    assert inputs["made_for_kids"] is True


def test_privacy_is_validated(client):
    _finished_video_job()
    _connect_channel()
    resp = client.post("/v1/jobs/src/publish", json={"privacy": "secret"})
    assert resp.status_code == 422


# ── the job list ────────────────────────────────────────────────────────────
#
# `GET /v1/jobs` did not exist, so the Queue and Library screens had nothing to
# read and rendered demo data permanently: generate a video and neither screen
# would ever change. They are the two screens someone looks at immediately after
# pressing Generate.


def _client():
    from fastapi.testclient import TestClient

    from engine import main

    return TestClient(main.app), main


def test_the_job_list_is_empty_rather_than_missing():
    client, main = _client()
    main.JOBS.clear()
    response = client.get("/v1/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_a_created_job_appears_in_the_list():
    client, main = _client()
    main.JOBS.clear()
    created = client.post("/v1/jobs", json={"topic": "why bridges collapse"})
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    rows = client.get("/v1/jobs").json()
    assert [r["id"] for r in rows] == [job_id]
    row = rows[0]
    assert row["topic"] == "why bridges collapse"
    assert row["workflow"] == "video"
    assert row["stages_total"] > 0
    assert row["stages_done"] <= row["stages_total"]


def test_the_list_can_be_filtered_by_status():
    client, main = _client()
    main.JOBS.clear()
    client.post("/v1/jobs", json={"topic": "a topic that is long enough"})
    for job in main.JOBS.values():
        job["status"] = "completed"

    assert len(client.get("/v1/jobs?status=completed").json()) == 1
    assert client.get("/v1/jobs?status=failed").json() == []


def test_newest_first():
    """The Queue reads top-down; oldest-first would bury the job just started."""
    from datetime import UTC, datetime, timedelta

    client, main = _client()
    main.JOBS.clear()
    client.post("/v1/jobs", json={"topic": "the older one"})
    client.post("/v1/jobs", json={"topic": "the newer one"})

    for job in main.JOBS.values():
        if job["inputs"]["topic"] == "the older one":
            job["created_at"] = datetime.now(UTC) - timedelta(hours=1)

    topics = [r["topic"] for r in client.get("/v1/jobs").json()]
    assert topics[0] == "the newer one"


def test_a_naive_and_an_aware_timestamp_can_coexist():
    """SQLite has no timezone type, so a restored job is naive while one created
    in this process is aware — and sorting the two together raises TypeError.

    This is exactly the state after any restart with a SQLite database: the list
    endpoint 500s the moment one new job is created alongside a restored one.
    """
    from datetime import UTC, datetime, timedelta

    client, main = _client()
    main.JOBS.clear()
    client.post("/v1/jobs", json={"topic": "the restored one"})
    client.post("/v1/jobs", json={"topic": "the fresh one"})

    for job in main.JOBS.values():
        if job["inputs"]["topic"] == "the restored one":
            # What SQLite hands back: no tzinfo, and older.
            job["created_at"] = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)

    response = client.get("/v1/jobs")
    assert response.status_code == 200, response.text
    assert [r["topic"] for r in response.json()][0] == "the fresh one"


def test_a_job_restored_without_a_timestamp_does_not_break_the_sort():
    """Rows written before created_at was mirrored come back with None."""
    client, main = _client()
    main.JOBS.clear()
    client.post("/v1/jobs", json={"topic": "a topic that is long enough"})
    for job in main.JOBS.values():
        job.pop("created_at", None)

    assert client.get("/v1/jobs").status_code == 200


def test_artifacts_are_exposed_so_the_library_can_show_them():
    from engine.workflows.base import Provenance, StageOutput, StageStatus

    client, main = _client()
    main.JOBS.clear()
    client.post("/v1/jobs", json={"topic": "a topic that is long enough"})
    job = next(iter(main.JOBS.values()))

    state = job["states"]["render"]
    state.status = StageStatus.DONE
    state.output = StageOutput(
        value="renders/x.mp4",
        provenance=Provenance(model="m"),
        artifacts={"render": "renders/x.mp4"},
    )
    thumb = job["states"]["thumbnail"]
    thumb.status = StageStatus.DONE
    thumb.output = StageOutput(
        value=[],
        provenance=Provenance(model="m"),
        artifacts={"thumbnail_0": "thumbnails/x-0.jpg", "thumbnail_1": "thumbnails/x-1.jpg"},
    )

    row = client.get("/v1/jobs").json()[0]
    assert row["render_key"] == "renders/x.mp4"
    assert row["thumbnail_keys"] == ["thumbnails/x-0.jpg", "thumbnails/x-1.jpg"]
