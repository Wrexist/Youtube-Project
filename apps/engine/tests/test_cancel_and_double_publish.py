"""Two ways to spend money or hang a browser tab that nobody was guarding.

Both are about the gap between "the server changed a variable" and "the thing the
user is looking at knows about it".
"""

from __future__ import annotations

import asyncio

import pytest

from engine import main as main_mod
from engine.main import JOBS


@pytest.fixture
def job():
    JOBS.clear()
    JOBS["j1"] = {
        "id": "j1",
        "workflow": None,
        "inputs": {},
        "states": {},
        "wake": asyncio.Event(),
        "events": [],
        "status": "running",
    }
    yield JOBS["j1"]
    JOBS.clear()


# ── cancel ──────────────────────────────────────────────────────────────────


async def test_cancelling_releases_an_open_subscriber(job):
    """The load-bearing `_wake`.

    `stream_job` parks on `await waiting.wait()` and only re-checks the status when
    that Event fires. Cancel set the status and never fired it, so every open SSE
    connection hung forever — the tab sat on a spinner for a job that had stopped.
    """

    async def drain():
        response = await main_mod.stream_job("j1")
        return [
            (chunk["event"] if isinstance(chunk, dict) else chunk)
            async for chunk in response.body_iterator
        ]

    reader = asyncio.create_task(drain())
    await asyncio.sleep(0)

    await main_mod.cancel_job("j1")

    seen = await asyncio.wait_for(reader, timeout=2)
    assert seen[-1] == "stream.closed", "the stream must terminate, not hang"
    assert "workflow.cancelled" in seen


async def test_cancelling_sets_the_status(job):
    assert (await main_mod.cancel_job("j1"))["status"] == "cancelled"
    assert job["status"] == "cancelled"


async def test_cancelling_a_finished_job_does_not_rewrite_its_outcome(job):
    """A completed job is a real result; cancel must not overwrite it with a lie."""
    job["status"] = "completed"
    assert (await main_mod.cancel_job("j1"))["status"] == "completed"
    assert job["status"] == "completed"


async def test_cancelling_a_failed_job_keeps_the_failure(job):
    job["status"] = "failed"
    await main_mod.cancel_job("j1")
    assert job["status"] == "failed"


async def test_a_worker_job_says_the_render_continues(job):
    """Cancelling only stops the local relay; claiming otherwise would be a lie."""
    job["enqueued"] = True
    result = await main_mod.cancel_job("j1")
    assert "worker" in result.get("note", "")


# ── double publish ──────────────────────────────────────────────────────────


def test_a_second_publish_of_the_same_video_is_refused():
    """The source stays `completed` and the web button re-enables, so a second
    click uploaded the same video to YouTube twice — 1,600 units and a duplicate
    public video each time."""
    JOBS.clear()
    JOBS["src"] = {"id": "src", "status": "completed", "inputs": {}}
    JOBS["pub"] = {"id": "pub", "status": "running", "inputs": {"source_job_id": "src"}}

    assert main_mod._existing_publish("src") == ("pub", "running")


def test_a_completed_publish_also_blocks():
    JOBS.clear()
    JOBS["pub"] = {"id": "pub", "status": "completed", "inputs": {"source_job_id": "src"}}
    assert main_mod._existing_publish("src") is not None


def test_a_failed_publish_does_not_block_a_retry():
    """Refusing here would strand a video whose upload died halfway."""
    JOBS.clear()
    JOBS["pub"] = {"id": "pub", "status": "failed", "inputs": {"source_job_id": "src"}}
    assert main_mod._existing_publish("src") is None


def test_another_videos_publish_is_not_mistaken_for_this_ones():
    JOBS.clear()
    JOBS["pub"] = {"id": "pub", "status": "running", "inputs": {"source_job_id": "other"}}
    assert main_mod._existing_publish("src") is None


def test_force_is_an_explicit_parameter_not_a_default():
    """Re-publishing has to be asked for, never assumed."""
    import inspect

    signature = inspect.signature(main_mod.publish_job)
    assert signature.parameters["force"].default is False


# ── the weak-script blocker ─────────────────────────────────────────────────


def test_the_critique_severity_is_read_off_a_dict():
    """`getattr(critique, "severity", 0)` on a dict always returns the default.

    CritiqueStage returns the parsed JSON verbatim and `decode_value` hands back a
    plain dict, so this read 0 every time — the weak_script blocker could never fire
    once in the entire history of the code. Invisible to the tests that covered it,
    because they construct VideoState directly and skip this conversion.
    """
    assert getattr({"severity": 5}, "severity", 0) == 0, "the bug, pinned"
    assert int(({"severity": 5} or {}).get("severity", 0) or 0) == 5


def test_the_threshold_is_on_the_scale_the_prompt_asks_for():
    """The prompt asks for 1-5; the threshold was 5 and the message said /10."""
    from engine import automation

    assert 1 <= automation._WEAK_SCRIPT_THRESHOLD <= 5
    assert automation._WEAK_SCRIPT_THRESHOLD < 5, "5 fires only at the maximum"


def _series():
    from engine.automation import Series

    return Series(id="s", name="S", niche="n", monthly_budget_usd=100.0)


def _ready(**overrides):
    """A video that clears every other blocker, so only the one under test fires."""
    from engine.automation import VideoState

    return VideoState(
        id="v",
        series_id="s",
        title="A perfectly reasonable title",
        has_sources=True,
        source_count=3,
        has_thumbnail=True,
        has_seo=True,
        keyword_grounded=True,
        render_ok=True,
        **overrides,
    )


def test_a_weak_script_actually_blocks_now():
    from engine.automation import publish_blockers

    weak = _ready(critique_severity=4)
    assert any("severity" in str(b).lower() for b in publish_blockers(weak, _series()))


def test_the_blocker_message_states_the_right_scale():
    from engine.automation import publish_blockers

    weak = _ready(critique_severity=5)
    message = next(
        str(b) for b in publish_blockers(weak, _series()) if "severity" in str(b).lower()
    )
    assert "/5" in message and "/10" not in message


@pytest.mark.parametrize(
    ("critique", "expected"),
    [
        ({"severity": 4}, 4),
        ({"severity": "3"}, 3),
        ({}, 0),
        (None, 0),
        ("a stage output edited into a string", 0),
        ({"severity": None}, 0),
        ({"severity": "not a number"}, 0),
        ([1, 2, 3], 0),
    ],
)
def test_severity_survives_whatever_the_stage_holds(critique, expected):
    """A stage output can be edited through POST /v1/jobs/{id}/edit into any JSON
    value, so a blocker that raises AttributeError is worse than one reading zero."""
    from engine.main import _severity_of

    assert _severity_of(critique) == expected


# ── re-run from a stage ─────────────────────────────────────────────────────
#
# The Create screen's "Re-run from here" called `console.log`. It could not call
# `/edit`: that replaces a stage's *value* and needs the current one, which the API
# never hands the client — `GET /v1/jobs/{id}` returns a summary string, not the
# object. So the control needed an endpoint of its own.


def _client():
    from fastapi.testclient import TestClient

    from engine import main

    return TestClient(main.app), main


def _started(client, main):
    main.JOBS.clear()
    job_id = client.post("/v1/jobs", json={"topic": "a topic that is long enough"}).json()["job_id"]
    return job_id, main.JOBS[job_id]


def test_rerunning_a_live_job_is_refused():
    """Two runs of the same job at once would interleave writes to one state map."""
    client, main = _client()
    job_id, job = _started(client, main)
    job["status"] = "running"
    response = client.post(f"/v1/jobs/{job_id}/rerun", json={"stage": "grounding"})
    assert response.status_code == 409


def test_rerunning_an_unknown_stage_is_a_404_not_a_500():
    client, main = _client()
    job_id, job = _started(client, main)
    job["status"] = "failed"
    assert client.post(f"/v1/jobs/{job_id}/rerun", json={"stage": "nope"}).status_code == 404


def test_rerunning_a_stage_that_never_ran_is_refused():
    """Nothing to re-run, and starting mid-graph would skip its dependencies."""
    client, main = _client()
    job_id, job = _started(client, main)
    job["status"] = "failed"
    response = client.post(f"/v1/jobs/{job_id}/rerun", json={"stage": "render"})
    assert response.status_code == 409


def test_a_rerun_invalidates_the_stage_and_everything_below_it():
    """The control's own caption: everything below regenerates, nothing above is
    touched. The stage itself is included — that is what "re-run" means, and it is
    what distinguishes this from /edit."""
    from engine.workflows.base import Provenance, StageOutput, StageStatus

    client, main = _client()
    job_id, job = _started(client, main)
    job["status"] = "failed"

    for name in ("grounding", "research", "angle"):
        state = job["states"][name]
        state.status = StageStatus.DONE
        state.output = StageOutput(value="x", provenance=Provenance(model="m"))

    response = client.post(f"/v1/jobs/{job_id}/rerun", json={"stage": "research"})
    assert response.status_code == 200

    invalidated = response.json()["invalidated"]
    assert "research" in invalidated, "the stage itself must re-run"
    assert "angle" in invalidated, "downstream must re-run"
    assert "grounding" not in invalidated, "upstream must be left alone"
    assert job["states"]["grounding"].status is StageStatus.DONE
