"""`run_job_task` — the worker actually executing a job.

`enqueue` was covered both ways and this was not, which left the durable path in an
odd state: the suite proved a job would be *handed over* to a worker and never that
the worker would do anything with it. That gap is easy to keep, because exercising
it for real needs a Redis, and the machine most of this was written on has none.

It does not need one. `ctx["redis"]` is a publisher and nothing else, so a recorder
stands in for it and everything that matters — the workflow running, the events, the
persistence, and above all the terminal marker — is observable without a server.

The terminal marker is the reason this file exists. `worker.py` says an unknown job,
a database that is down and a crash must *all* still publish `__done__`, because it
is the only thing that closes the API's relay; without it every browser tab watching
that job waits for an event that is never coming. Three of those four paths never
run in ordinary use, so nothing else would notice them breaking.
"""

from __future__ import annotations

import json

import pytest

from engine import worker
from engine.workflows import video
from engine.workflows.base import Provenance, StageOutput, StageStatus


class Recorder:
    """Stands in for the arq context's Redis. Records instead of publishing."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, json.loads(payload)))

    def types(self) -> list[str]:
        return [event.get("type") for _channel, event in self.published]


@pytest.fixture
def redis() -> Recorder:
    return Recorder()


@pytest.fixture(autouse=True)
def _no_ledger(monkeypatch):
    """`run_job_task` refreshes the quota ledger first. Not what is under test."""

    async def noop() -> None:
        return None

    from engine.quota import ledger

    monkeypatch.setattr(ledger, "refresh", noop)


async def _store(job_id: str, *, done: tuple[str, ...] = ()) -> None:
    from engine import repository

    states = video.get("video").initial_states()
    for name in done:
        states[name].status = StageStatus.DONE
        states[name].output = StageOutput(value={"note": name}, provenance=Provenance())

    await repository.save_job(
        {
            "id": job_id,
            "workflow": video.get("video"),
            "status": "queued",
            "inputs": {"topic": "why bridges collapse"},
            "states": states,
            "events": [],
            "cost_usd": 0.0,
        }
    )


async def test_an_unknown_job_still_closes_the_stream(redis, database):
    """A worker pointed at a different database, or at one with no rows.

    This used to `return` from above the try, so the marker was never published and
    every open SSE connection for that job stayed open until the browser gave up.
    """
    result = await worker.run_job_task({"redis": redis}, "no-such-job")

    assert result == "unknown"
    assert redis.types() == ["__done__"]


async def test_a_crash_before_the_workflow_still_closes_the_stream(redis, database, monkeypatch):
    """The database being down is the case that skips a `finally` if the marker is
    published anywhere but one."""

    async def explode(_get):
        raise RuntimeError("the database is on fire")

    from engine import repository

    monkeypatch.setattr(repository, "load_jobs", explode)

    with pytest.raises(RuntimeError):
        await worker.run_job_task({"redis": redis}, "job-1")

    assert redis.types() == ["__done__"]


async def test_a_redis_that_cannot_publish_does_not_fail_the_stage(redis, database, monkeypatch):
    """A finished render recorded as failed because Redis blinked while its event
    was being published is strictly worse than a lost event."""
    await _store("job-2")

    class Blinking(Recorder):
        async def publish(self, channel: str, payload: str) -> None:
            raise ConnectionError("Error 111 connecting to redis")

    async def one_stage(**kwargs):
        await kwargs["emit"]({"type": "stage.completed", "stage": "grounding"})

    monkeypatch.setattr(video.get("video"), "run", one_stage)

    # No raise: `emit` swallows both the publish and the persist, by design.
    status = await worker.run_job_task({"redis": Blinking()}, "job-2")

    assert status == "completed"


async def test_a_workflow_failure_is_recorded_and_the_stream_closed(redis, database, monkeypatch):
    from engine.workflows.base import WorkflowError

    await _store("job-3")

    async def fail(**_kwargs):
        raise WorkflowError("research found no usable sources")

    monkeypatch.setattr(video.get("video"), "run", fail)

    status = await worker.run_job_task({"redis": redis}, "job-3")

    assert status == "failed"
    assert redis.types()[-1] == "__done__"

    # And the row says so, which is what the Queue reads after the stream closes.
    from engine import repository

    jobs = await repository.load_jobs(video.get)
    assert jobs["job-3"]["status"] == "failed"
    assert "no usable sources" in jobs["job-3"]["error"]


async def test_a_completed_run_persists_and_publishes_in_order(redis, database, monkeypatch):
    await _store("job-4")
    emitted = [
        {"type": "workflow.started", "job_id": "job-4"},
        {"type": "stage.completed", "stage": "grounding", "cost_usd": 0.1},
        {"type": "workflow.completed", "job_id": "job-4", "cost_usd": 0.1},
    ]

    async def run_all(**kwargs):
        for event in emitted:
            await kwargs["emit"](event)

    monkeypatch.setattr(video.get("video"), "run", run_all)

    status = await worker.run_job_task({"redis": redis}, "job-4")

    assert status == "completed"
    # Every event reaches the channel for that job, and `__done__` is last — the
    # relay re-reads the row the moment it sees it, so anything published after
    # would be read too late.
    assert redis.types() == [
        "workflow.started",
        "stage.completed",
        "workflow.completed",
        "__done__",
    ]
    assert {channel for channel, _ in redis.published} == {worker.CHANNEL.format("job-4")}

    from engine import repository

    assert (await repository.load_jobs(video.get))["job-4"]["status"] == "completed"
