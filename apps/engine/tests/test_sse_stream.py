"""Tests for the job event stream.

Two bugs these exist for, both found by watching a real stream:

1. **Every pre-subscribe event arrived twice.** `emit()` wrote to both an event log
   and a queue; `stream_job` replayed the log and *then* drained the queue, which
   still held those same events.
2. **The queue was single-consumer.** Two browser tabs on one job split the stream
   between them — each event went to whichever generator called `get()` first — so
   both rendered an incomplete pipeline.

The fix makes the log the single source of truth and gives each subscriber its own
cursor. These tests cover both failures and the wake-up race between them.
"""

from __future__ import annotations

import asyncio

import pytest

from engine import main as main_mod
from engine.main import JOBS, _wake


@pytest.fixture
def job():
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


async def _emit(job: dict, kind: str) -> None:
    job["events"].append({"type": kind, "job_id": job["id"]})
    _wake(job)


async def _drain(job_id: str, *, stop_after: int | None = None) -> list[str]:
    """Read the SSE generator the way the endpoint does."""
    response = await main_mod.stream_job(job_id)
    seen: list[str] = []
    async for chunk in response.body_iterator:
        seen.append(chunk["event"])
        if stop_after is not None and len(seen) >= stop_after:
            break
    return seen


# ── no duplicates ───────────────────────────────────────────────────────────


async def test_pre_subscribe_events_arrive_exactly_once(job):
    """The original bug: replay + queue-drain delivered each of these twice."""
    for kind in ("workflow.started", "stage.started", "stage.progress"):
        await _emit(job, kind)
    job["status"] = "completed"

    assert await _drain("j1") == ["workflow.started", "stage.started", "stage.progress"]


async def test_a_finished_job_replays_its_whole_log_and_closes(job):
    for i in range(5):
        await _emit(job, f"stage.{i}")
    job["status"] = "failed"

    seen = await _drain("j1")
    assert seen == [f"stage.{i}" for i in range(5)]


async def test_an_empty_finished_job_closes_immediately(job):
    job["status"] = "completed"
    assert await _drain("j1") == []


# ── multiple subscribers ────────────────────────────────────────────────────


async def test_two_subscribers_each_see_every_event(job):
    """The queue version split events between tabs; both views were wrong."""
    for kind in ("a", "b", "c"):
        await _emit(job, kind)
    job["status"] = "completed"

    first, second = await asyncio.gather(_drain("j1"), _drain("j1"))
    assert first == ["a", "b", "c"]
    assert second == ["a", "b", "c"]


async def test_a_late_subscriber_gets_the_backlog_then_live_events(job):
    await _emit(job, "early")

    reader = asyncio.create_task(_drain("j1", stop_after=2))
    await asyncio.sleep(0)  # let it drain the backlog and start waiting

    await _emit(job, "late")
    assert await asyncio.wait_for(reader, timeout=2) == ["early", "late"]


async def test_two_live_subscribers_both_wake_on_one_event(job):
    a = asyncio.create_task(_drain("j1", stop_after=1))
    b = asyncio.create_task(_drain("j1", stop_after=1))
    await asyncio.sleep(0)

    await _emit(job, "only-one-event")

    assert await asyncio.wait_for(a, timeout=2) == ["only-one-event"]
    assert await asyncio.wait_for(b, timeout=2) == ["only-one-event"]


# ── the wake race ───────────────────────────────────────────────────────────


async def test_an_event_emitted_during_the_drain_gap_is_not_missed(job):
    """_wake swaps the Event rather than set/clear, so this cannot hang.

    With set()+clear() a subscriber that drained but had not yet awaited would
    miss the signal and block until the *next* event — on the last event of a
    run, that is forever.
    """
    reader = asyncio.create_task(_drain("j1", stop_after=1))
    await asyncio.sleep(0)

    await _emit(job, "raced")
    assert await asyncio.wait_for(reader, timeout=2) == ["raced"]


async def test_completion_wakes_a_waiting_subscriber(job):
    """A subscriber parked on a job that then finishes must close, not hang."""
    reader = asyncio.create_task(_drain("j1"))
    await asyncio.sleep(0)

    job["status"] = "completed"
    _wake(job)

    assert await asyncio.wait_for(reader, timeout=2) == []


async def test_wake_arms_a_fresh_event_each_time(job):
    first = job["wake"]
    _wake(job)
    assert first.is_set()
    assert job["wake"] is not first
    assert not job["wake"].is_set()


async def test_unknown_job_raises_before_streaming():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await main_mod.stream_job("nope")
    assert exc.value.status_code == 404
