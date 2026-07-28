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
