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

    The full argument list is kept, not just the count. What the stages *pass* is
    the half nobody was checking: privacy, the schedule, the chosen title and the
    made-for-kids flag are all decisions with consequences on a live channel, and
    a stage that drops one of them looks identical to one that does not from a call
    count alone.
    """

    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.thumbnails: list[str] = []
        self.captions: list[str] = []
        self.upload_calls: list[dict] = []
        self.caption_calls: list[dict] = []
        self.playlist_calls: list[dict] = []

    async def upload(self, path, **kwargs) -> str:
        self.uploads.append(kwargs.get("title", ""))
        self.upload_calls.append({"path": path, **kwargs})
        return f"yt-{len(self.uploads)}"

    async def set_thumbnail(self, video_id, path) -> None:
        self.thumbnails.append(video_id)

    async def upload_captions(self, video_id, path, **kwargs) -> None:
        self.captions.append(video_id)
        self.caption_calls.append({"video_id": video_id, "path": path, **kwargs})

    async def add_to_playlist(self, video_id, playlist_id) -> None:
        self.playlist_calls.append({"video_id": video_id, "playlist_id": playlist_id})


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


# ── the publish stages, actually executed ───────────────────────────────────
#
# Everything above drives the gate. The four stages the gate starts were another
# matter: only `ThumbnailSetStage` ever ran, through the re-run test directly
# above, so `UploadStage.run` — the one that spends 1,600 units and decides what
# the world sees — was executed by no test at all, and `CaptionsStage.run` and
# `PlaylistStage.run` by none either.
#
# Two mutations measured the hole. Making the upload ignore `privacy` and making
# it ignore `chosen_title_index` each left the suite green. Both are decisions the
# operator made on a screen and neither has any other checkpoint: a scheduled
# video that goes up public is published early and cannot be unpublished, and a
# title chosen from three variants is the whole of Phase 8's CTR attribution.


class _Title:
    def __init__(self, text: str, strategy: str = "curiosity") -> None:
        self.text, self.strategy = text, strategy


@pytest.fixture
def stage_context(monkeypatch, tmp_path):
    """A `WorkflowContext` shaped exactly as `publish_job` builds one.

    The stages are driven directly rather than through the endpoint because what is
    under test is the translation from operator choice to API argument — the
    endpoint's own job is only to put those choices into `inputs`, which
    `test_operator_choices_reach_the_upload_stage` already covers.

    `store._root` is redirected because `CaptionsStage` writes an SRT and the suite
    has no business writing into `./storage`.
    """
    from engine.storage import store
    from engine.workflows.base import WorkflowContext

    monkeypatch.setattr(store, "_root", tmp_path)

    def build(fake, *, titles=None, **inputs):
        source = _finished_video_job("src")
        wf = video.get("publish")
        states = wf.initial_states()
        for name, state in source["states"].items():
            if name in states:
                states[name] = state
        if titles is not None:
            states["titles"].output.value = titles
        JOBS.clear()  # the helper registers the source job; nothing here reads it

        async def emit(_event: dict) -> None:
            return None

        return WorkflowContext("pub", {"youtube_client": fake, **inputs}, states, emit, 8.0)

    return build


async def test_a_scheduled_upload_goes_up_private(stage_context):
    """`publishAt` is ignored on a public video — silently, by YouTube.

    So a scheduled publish that forwards the operator's "public" is not "scheduled
    and slightly wrong": it is live immediately, on a channel, ahead of the date
    someone picked. There is no undo.
    """
    from datetime import UTC, datetime

    from engine.workflows.publish import UploadStage

    when = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)
    fake = FakeYouTube()
    ctx = stage_context(fake, publish_at=when, privacy="public")

    await UploadStage().run(ctx)

    call = fake.upload_calls[0]
    assert call["privacy"] == "private", "a scheduled upload must not go up public"
    assert call["publish_at"] == when


async def test_an_unscheduled_upload_keeps_the_operators_privacy(stage_context):
    """The other direction: with no schedule, "unlisted" must stay unlisted."""
    from engine.workflows.publish import UploadStage

    fake = FakeYouTube()
    await UploadStage().run(stage_context(fake, privacy="unlisted"))

    assert fake.upload_calls[0]["privacy"] == "unlisted"
    assert fake.upload_calls[0]["publish_at"] is None


async def test_the_chosen_title_is_the_one_uploaded(stage_context):
    """Three variants are generated and one is picked. Uploading `titles[0]`
    regardless looks correct on any single-variant fixture — which is what every
    test here had until now — and silently discards the choice."""
    from engine.workflows.publish import UploadStage

    titles = [_Title("first"), _Title("second"), _Title("the one they picked", "curiosity_gap")]
    fake = FakeYouTube()
    output = await UploadStage().run(stage_context(fake, titles=titles, chosen_title_index=2))

    assert fake.upload_calls[0]["title"] == "the one they picked"
    # Non-negotiable #2: the choice has to be recorded, or Phase 8 cannot attribute
    # CTR back to the strategy that earned it.
    assert output.provenance.params["title"] == "the one they picked"
    assert output.provenance.params["strategy"] == "curiosity_gap"


async def test_made_for_kids_is_forwarded_as_declared(stage_context):
    """Omitting it is a common cause of a silently rejected upload, and declaring
    it wrongly is a legal problem rather than a technical one."""
    from engine.workflows.publish import UploadStage

    fake = FakeYouTube()
    await UploadStage().run(stage_context(fake, made_for_kids=True))
    assert fake.upload_calls[0]["made_for_kids"] is True

    default = FakeYouTube()
    await UploadStage().run(stage_context(default))
    assert default.upload_calls[0]["made_for_kids"] is False


async def test_the_description_and_tags_come_from_their_stages(stage_context):
    from engine.workflows.publish import UploadStage

    fake = FakeYouTube()
    await UploadStage().run(stage_context(fake))
    assert fake.upload_calls[0]["description"] == "A description."
    assert fake.upload_calls[0]["tags"] == ["bridges"]


async def test_captions_upload_an_srt_built_from_the_cues(stage_context):
    """A real caption track is a ranking signal; burned-in subtitles are not.

    The stage was 0% executed, so neither the SRT it writes nor the fact that it
    hands the file to the client was checked anywhere.
    """
    from engine.workflows.publish import CaptionsStage

    fake = FakeYouTube()
    ctx = stage_context(fake)
    ctx._states["upload"].status = StageStatus.DONE
    from engine.workflows.base import Provenance, StageOutput

    ctx._states["upload"].output = StageOutput(value="yt-1", provenance=Provenance())

    output = await CaptionsStage().run(ctx)

    assert fake.caption_calls[0]["video_id"] == "yt-1"
    written = fake.caption_calls[0]["path"].read_text()
    assert written.startswith("1\n00:00:00,000 --> 00:00:01,000\nHi.")
    assert output.provenance.params["cue_count"] == 1


async def test_the_playlist_stage_does_nothing_without_a_playlist(stage_context):
    from engine.workflows.publish import PlaylistStage

    fake = FakeYouTube()
    assert PlaylistStage().should_skip(stage_context(fake)) is True


async def test_a_playlist_id_adds_the_uploaded_video(stage_context):
    from engine.workflows.base import Provenance, StageOutput
    from engine.workflows.publish import PlaylistStage

    fake = FakeYouTube()
    ctx = stage_context(fake, playlist_id="PL123")
    ctx._states["upload"].status = StageStatus.DONE
    ctx._states["upload"].output = StageOutput(value="yt-1", provenance=Provenance())

    assert PlaylistStage().should_skip(ctx) is False
    await PlaylistStage().run(ctx)
    assert fake.playlist_calls == [{"video_id": "yt-1", "playlist_id": "PL123"}]


# ── the double-publish guard, over HTTP ─────────────────────────────────────
#
# `_existing_publish` is well covered as a helper in
# test_cancel_and_double_publish.py, and a helper's tests pass whether or not the
# endpoint calls it. Deleting the call — `existing = None` — left the suite green.
# These go through the ASGI app and count uploads.
#
# `httpx.ASGITransport` rather than `TestClient` for one reason: `publish_job`
# starts the run with `asyncio.create_task`, so on the sync client the assertion
# races the upload. Here the task is awaited before anything is checked.


@pytest.fixture
def asgi(monkeypatch, tmp_path):
    """The app over ASGI, with a fake client wherever the endpoint builds one."""
    import httpx

    from engine.providers import youtube
    from engine.storage import store

    monkeypatch.setattr(store, "_root", tmp_path)
    built: list[FakeYouTube] = []

    def build_client(_creds):
        built.append(FakeYouTube())
        return built[-1]

    monkeypatch.setattr(youtube, "YouTube", build_client)

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://engine.test"), built


async def _settle(publish_id: str) -> None:
    """Wait for the publish job's detached task, so nothing below races it."""
    task = JOBS[publish_id].get("task")
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=10)


async def test_a_second_publish_over_http_uploads_nothing(asgi):
    """The gate's whole point, exercised the way the Publish button reaches it."""
    http, built = asgi
    _finished_video_job()
    _connect_channel()
    before = len(JOBS)

    async with http as client:
        first = await client.post("/v1/jobs/src/publish", json={})
        assert first.status_code == 202, first.text
        publish_id = first.json()["job_id"]
        await _settle(publish_id)

        second = await client.post("/v1/jobs/src/publish", json={})

    assert second.status_code == 409
    assert publish_id in second.json()["detail"], "the refusal must name the publish to look at"
    assert len(JOBS) == before + 1, "the refused publish created a second job anyway"
    assert [len(f.uploads) for f in built] == [1], "the video was uploaded twice"

    JOBS.clear()
    publishing.CHANNELS.clear()


async def test_an_interrupted_publish_that_uploaded_still_blocks(asgi):
    """A publish job that was mid-run when the process died comes back from
    `load_jobs` as `interrupted`, not `failed` — and the guard used to admit
    anything that was not running or completed. Its video is already live."""
    http, built = asgi
    _finished_video_job()
    _connect_channel()
    stalled, _fake = _published_job("pub")
    stalled["status"] = "interrupted"

    async with http as client:
        response = await client.post("/v1/jobs/src/publish", json={})

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "yt-original" in detail, "the refusal should name the video that is already live"
    assert built == [], "a second upload was started"

    JOBS.clear()
    publishing.CHANNELS.clear()


async def test_an_interrupted_publish_that_never_uploaded_says_so(asgi):
    """Same status, opposite advice. Nothing is live, so `?force=true` is the
    right answer here — and telling someone their video is "already published"
    when it is not is how a channel ends up with nothing on it."""
    from engine.workflows.base import StageStatus as _Status

    http, built = asgi
    _finished_video_job()
    _connect_channel()
    stalled, _fake = _published_job("pub")
    stalled["status"] = "interrupted"
    stalled["states"]["upload"].status = _Status.PENDING
    stalled["states"]["upload"].output = None

    async with http as client:
        response = await client.post("/v1/jobs/src/publish", json={})

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "force=true" in detail
    assert "yt-original" not in detail
    assert built == []

    JOBS.clear()
    publishing.CHANNELS.clear()


async def test_force_is_what_admits_the_duplicate(asgi):
    """The escape hatch has to work, or a publish that died halfway strands a
    finished render with no way to ship it."""
    http, built = asgi
    _finished_video_job()
    _connect_channel()

    async with http as client:
        first = await client.post("/v1/jobs/src/publish", json={})
        await _settle(first.json()["job_id"])

        forced = await client.post("/v1/jobs/src/publish?force=true", json={})
        assert forced.status_code == 202, forced.text
        await _settle(forced.json()["job_id"])

    assert [len(f.uploads) for f in built] == [1, 1], "force did not reach the upload"

    JOBS.clear()
    publishing.CHANNELS.clear()


# ── a publish job that came back from its row ───────────────────────────────
#
# `jsonable` strips `youtube_client` on the way to the database, so a restored
# publish job — or one handed to the worker, which only ever receives an id — has
# no client at all. The failure was quiet in the worst possible place:
# `CaptionsStage` is `optional=True`, so the framework turned its bare `KeyError`
# into SKIPPED and the job reported success while the captions of an already-live
# video were never uploaded. Nothing anywhere says a caption track is missing.


async def _round_tripped_publish_job(database) -> dict:
    """A publish job through `save_job`/`load_jobs`, as a restart leaves it."""
    from engine import repository
    from engine.workflows.seo import TitleVariant

    job, _fake = _published_job("pub")
    # The two stand-in classes in `_finished_video_job` are local to this module, so
    # `encode_value` cannot store them and the restore correctly marks their stages
    # STALE — which would re-run the whole graph, network and all. Swap in the real
    # shapes: what is under test here is the resume, not the encoder.
    job["states"]["grounding"].output.value = {"is_grounded": True}
    job["states"]["titles"].output.value = [TitleVariant(text="Why bridges collapse", strategy="c")]

    # What the endpoint puts there, and what `jsonable` must remove.
    assert job["inputs"]["youtube_client"] is not None
    await repository.save_job(job)

    restored = (await repository.load_jobs(video.get))["pub"]
    assert "youtube_client" not in restored["inputs"], "an access token reached the row"
    JOBS.clear()
    JOBS["pub"] = restored
    return restored


async def test_a_restored_publish_job_gets_a_client_back(database, monkeypatch, tmp_path):
    """The captions re-run has to reach YouTube, not be skipped into silence."""
    from engine import main as main_mod
    from engine.providers import youtube
    from engine.storage import store

    monkeypatch.setattr(store, "_root", tmp_path)

    fake = FakeYouTube()
    monkeypatch.setattr(youtube, "YouTube", lambda _creds: fake)
    _connect_channel()

    restored = await _round_tripped_publish_job(database)
    restored["states"]["captions"].status = StageStatus.PENDING
    restored["states"]["captions"].output = None
    restored["status"] = "interrupted"

    await main_mod._run_job("pub", "captions")

    assert restored["status"] == "completed", restored.get("error")
    assert fake.captions == ["yt-original"], "the captions were skipped, not uploaded"
    assert restored["states"]["captions"].status is StageStatus.DONE

    JOBS.clear()
    publishing.CHANNELS.clear()


async def test_a_restored_publish_job_with_no_channel_fails_by_name(
    database, monkeypatch, tmp_path
):
    """Not SKIPPED, and not a bare KeyError three frames down.

    An optional stage swallows any exception its `run` raises, so "no channel" has
    to stop the run *before* the workflow starts or it reads as success.
    """
    from engine import main as main_mod
    from engine.storage import store

    monkeypatch.setattr(store, "_root", tmp_path)
    publishing.CHANNELS.clear()

    restored = await _round_tripped_publish_job(database)
    restored["states"]["captions"].status = StageStatus.PENDING
    restored["states"]["captions"].output = None

    await main_mod._run_job("pub", "captions")

    assert restored["status"] == "failed"
    assert "no YouTube channel is connected" in str(restored.get("error"))
    assert restored["states"]["captions"].status is not StageStatus.SKIPPED
    assert [e["type"] for e in restored["events"]][-1] == "workflow.failed"

    JOBS.clear()
