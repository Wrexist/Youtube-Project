"""Tests for `POST /v1/jobs/{job_id}/publish` — the approval gate.

`CLAUDE.md` non-negotiable #3: nothing publishes without an explicit approval gate.
This endpoint *is* that gate, which is why publishing is not a stage of the `video`
workflow — a workflow that publishes as its last step has no gate at all.

Every refusal below must stay a refusal. Each one is cheap to check and expensive to
get wrong: a bad publish costs 1,600 quota units and puts something live on a real
channel under the user's name.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from engine.api import publishing
from engine.main import JOBS, app
from engine.workflows import video
from engine.workflows.base import StageStatus


@pytest.fixture(autouse=True)
def no_background_work(monkeypatch):
    """Creating a job must not start running one.

    Every `POST /v1/jobs` below exists to produce a *record* — the list endpoint's
    sort order, the rerun gate, the 400 on an unstartable workflow. None of them
    wants the workflow itself, but `create_job` ends in `_dispatch`, which fires
    `_run_job` as a detached task: research, an LLM call per stage, a stock search,
    a render. Roughly 27 outbound requests per created job, all of which only ever
    "passed" because this machine has no keys and no network to reach them with.

    Autouse, and here rather than inside a helper, because the cost of a new test
    forgetting is a suite that shells out to the internet without saying so.
    """
    from engine import main as main_mod

    async def no_dispatch(job_id: str, start_from: str | None = None) -> None:
        return None

    monkeypatch.setattr(main_mod, "_dispatch", no_dispatch)


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


# ── the publish job's live client ───────────────────────────────────────────


def test_the_youtube_client_never_reaches_a_response():
    """`get_job` returned job["inputs"] verbatim, and a publish job's inputs hold a
    live YouTube client carrying an access token — so the endpoint 500'd on
    serialisation, and was one annotation change away from returning the token."""
    from engine import repository

    served = repository.jsonable(
        {
            "topic": "x",
            "youtube_client": object(),
            "access_token": "ya29.SECRET",
            "nested": {"ok": 1},
        }
    )
    assert "youtube_client" not in served
    assert served["topic"] == "x"
    assert served["nested"] == {"ok": 1}


def test_a_scheduled_publish_keeps_its_time_across_a_restart():
    """`publish_at` is a datetime, which the json filter dropped outright.

    A scheduled publish that survived a restart therefore came back with no
    publish_at at all — and UploadStage reads that as "publish now, publicly"
    rather than "private until the scheduled time".
    """
    from datetime import UTC, datetime

    from engine.repository import _restore_inputs, jsonable

    when = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)
    restored = _restore_inputs(jsonable({"publish_at": when, "privacy": "private"}))
    assert restored["publish_at"] == when
    assert restored["privacy"] == "private"


def test_a_corrupt_stored_timestamp_does_not_crash_the_restore():
    from engine.repository import _restore_inputs

    assert "publish_at" not in _restore_inputs({"publish_at": "not-a-date"})


# ── the publish workflow is not directly startable ──────────────────────────


def test_starting_the_publish_workflow_directly_is_refused():
    """It used to return 202, run the entire paid render, then die on a bare
    KeyError: 'youtube_client' in a stage with max_attempts = 1."""
    client, main = _client()
    main.JOBS.clear()
    response = client.post("/v1/jobs", json={"topic": "a topic", "workflow": "publish"})
    assert response.status_code == 400
    assert "publish" in response.json()["detail"]
    assert main.JOBS == {}, "nothing should have been created"


def test_health_does_not_advertise_a_workflow_you_cannot_start():
    client, _ = _client()
    assert "publish" not in client.get("/health").json()["workflows"]


def test_the_startable_workflows_still_work():
    client, main = _client()
    main.JOBS.clear()
    for name in ("video", "script", "seo"):
        assert (
            client.post("/v1/jobs", json={"topic": "a topic", "workflow": name}).status_code == 202
        )


# ── a published video cannot be published again by the back door ────────────
#
# The double-publish guard lives on `POST /publish` alone, and two other endpoints
# reach the same `UploadStage` without going past it: `/rerun` with stage "upload",
# and `/edit` on anything the upload depends on — description, tags, titles — which
# invalidates the upload and re-runs it.
#
# It was covered only at helper level (`_existing_publish` in
# test_cancel_and_double_publish.py). That is why a guard which never ran on either
# endpoint could survive: the helper's tests pass whether or not anything calls it.
# These go through HTTP, and count what the YouTube client was actually asked to do.


class FakeYouTube:
    """A YouTube client that records instead of spending.

    Every method here is a real quota charge upstream — `upload` is 1,600 of the
    day's 10,000 — so the counts are the assertion, not decoration.
    """

    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.thumbnails: list[str] = []
        self.captions: list[str] = []

    async def upload(self, path, **kwargs) -> str:
        self.uploads.append(kwargs.get("title", ""))
        return f"yt-{len(self.uploads)}"

    async def set_thumbnail(self, video_id, path) -> None:
        self.thumbnails.append(video_id)

    async def upload_captions(self, video_id, path) -> None:
        self.captions.append(video_id)

    async def add_to_playlist(self, video_id, playlist_id) -> None:  # pragma: no cover
        pass


@pytest.fixture
def runs_for_real(monkeypatch, tmp_path):
    """Let the workflow actually execute, somewhere harmless.

    Overrides `no_background_work` for the re-run tests below, because the thing
    being proved is what happens when the guard is *absent*, and "no upload was
    attempted" proves nothing against a dispatch that never dispatches.

    Awaited rather than spawned as a task: the assertion must not race the run.
    `store._root` is redirected because `CaptionsStage` writes an SRT, and the test
    suite has no business writing into `./storage`.
    """
    from engine import main as main_mod
    from engine.storage import store

    monkeypatch.setattr(store, "_root", tmp_path)

    async def run_now(job_id: str, start_from: str | None = None) -> None:
        await main_mod._run_job(job_id, start_from)

    monkeypatch.setattr(main_mod, "_dispatch", run_now)


def _published_job(publish_id: str = "pub", source_id: str = "src") -> tuple[dict, FakeYouTube]:
    """A publish job that has already put the video on YouTube.

    Shaped exactly as `publish_job` leaves it: the video stages seeded DONE from the
    source job, the four publish stages DONE on top, and a live client in `inputs`.
    """
    from engine.workflows.base import Provenance, StageOutput

    source = _finished_video_job(source_id)
    wf = video.get("publish")
    states = wf.initial_states()
    for name, state in source["states"].items():
        if name in states:
            states[name] = state

    for name, value in (
        ("upload", "yt-original"),
        ("thumbnail_set", "thumbnails/src-0.jpg"),
        ("captions", "captions/pub.srt"),
    ):
        states[name].status = StageStatus.DONE
        states[name].output = StageOutput(value=value, provenance=Provenance())
    states["playlist"].status = StageStatus.SKIPPED

    fake = FakeYouTube()
    job = {
        "id": publish_id,
        "workflow": wf,
        "inputs": {**source["inputs"], "youtube_client": fake, "source_job_id": source_id},
        "states": states,
        "wake": asyncio.Event(),
        "events": [],
        "status": "completed",
    }
    JOBS[publish_id] = job
    return job, fake


def test_rerunning_the_upload_of_a_published_video_is_refused(client, runs_for_real):
    """`/rerun {"stage": "upload"}` on a finished publish job is a second upload.

    Same 1,600 units and the same duplicate public video as a second click on
    Publish, arriving through an endpoint that never asked `_existing_publish`.
    """
    job, fake = _published_job()

    response = client.post("/v1/jobs/pub/rerun", json={"stage": "upload"})

    assert response.status_code == 409, response.text
    assert fake.uploads == [], "the video was uploaded a second time"
    assert job["states"]["upload"].output.value == "yt-original", "the original id was discarded"


def test_editing_upstream_of_a_published_upload_is_refused(client):
    """`/edit` on the description invalidates the upload and re-runs it.

    The refusal has to happen before `mark_edited`, which is why the assertion is on
    the stage state: without the guard the upload is already STALE with its video id
    thrown away by the time the response is written, whatever the re-run then does.
    """
    job, fake = _published_job()

    response = client.post(
        "/v1/jobs/pub/edit", json={"stage": "description", "value": "A better description."}
    )

    assert response.status_code == 409, response.text
    assert job["states"]["upload"].status is StageStatus.DONE
    assert job["states"]["upload"].output.value == "yt-original"
    assert fake.uploads == []


def test_rerunning_the_thumbnail_of_a_published_video_is_allowed(client, runs_for_real):
    """The reason the guard is per-stage rather than per-job.

    Swapping the thumbnail on a live video is the one publish stage worth re-running
    by hand — it is 50 quota units against 1,600, and nothing downstream of `upload`
    re-uploads anything. Refusing it would make the guard cost more than it saves.
    """
    _job, fake = _published_job()

    response = client.post("/v1/jobs/pub/rerun", json={"stage": "thumbnail_set"})

    assert response.status_code == 200, response.text
    assert fake.thumbnails == ["yt-original"], "the thumbnail should have been set again"
    assert fake.uploads == [], "re-running the thumbnail must not re-upload the video"
