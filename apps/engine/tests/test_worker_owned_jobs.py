"""Jobs this process can see but is not running.

The API and the arq worker are separate processes, and `JOBS` is a *mirror*: the
API dispatches, subscribes to the worker's events, and never touches the stage
outputs. Everything here is a way that mirror and the row it mirrors disagree, and
in every case the row is right and the mirror is what someone is looking at.

Both failures below were invisible to the existing suite for the same reason — it
tests one process, where the mirror and the executor are the same dictionary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from engine import main as main_mod
from engine.main import JOBS
from engine.workflows import video
from engine.workflows.base import Provenance, StageOutput, StageStatus


@pytest.fixture(autouse=True)
def clean_jobs():
    JOBS.clear()
    yield
    JOBS.clear()


def _worker_states(done: tuple[str, ...] = ("grounding", "research", "angle")) -> dict:
    """A `video` state map with real, round-trippable output on the finished stages.

    Plain JSON values rather than the stub classes other modules use, because these
    go through `save_job`/`load_jobs` — `encode_value` cannot store a class local to
    a test module, and the restore would mark the stage STALE and quietly hand the
    test the very blankness it is trying to detect.
    """
    states = video.get("video").initial_states()
    for index, name in enumerate(done):
        states[name].status = StageStatus.DONE
        states[name].output = StageOutput(
            value={"note": f"{name} ran in the worker"},
            provenance=Provenance(model="claude-test"),
            cost_usd=0.25 * (index + 1),
        )
    return states


async def _save_worker_row(job_id: str, *, status: str, states: dict, events: list) -> None:
    """Write the row as the worker would have left it."""
    from engine import repository

    await repository.save_job(
        {
            "id": job_id,
            "workflow": video.get("video"),
            "status": status,
            "inputs": {"topic": "why bridges collapse"},
            "states": states,
            "events": events,
            "created_at": datetime.now(UTC),
        }
    )


def _mirror(job_id: str, *, status: str = "running", task=None) -> dict:
    """What the API process holds for a worker-run job: a blank state map.

    `initial_states()` and nothing else, deliberately — that *is* the mirror. The
    worker's stage outputs never leave the worker process, so anything that writes
    this map back to the database is writing over the real work with nothing.
    """
    JOBS[job_id] = {
        "id": job_id,
        "workflow": video.get("video"),
        "status": status,
        "inputs": {"topic": "why bridges collapse"},
        "states": video.get("video").initial_states(),
        "wake": asyncio.Event(),
        "events": [],
        "enqueued": True,
        "task": task,
        "created_at": datetime.now(UTC),
    }
    return JOBS[job_id]


# ── cancelling what somebody else is running ────────────────────────────────


async def test_cancelling_a_worker_job_does_not_blank_its_row(database):
    """Cancel used to call `_persist`, which writes the *whole* mirror.

    So cancelling a render at stage twelve wrote an all-PENDING state map and a
    zero cost over a row holding eleven finished stages and the money they cost.
    The job then could not be resumed — every stage looked as though it had never
    run — and the recorded spend for the video vanished with it, which is
    non-negotiable #5's whole ledger for that video.
    """
    from engine import repository

    worker_events = [
        {"type": "workflow.started", "job_id": "wj"},
        {"type": "stage.completed", "stage": "grounding", "cost_usd": 0.25},
        {"type": "stage.completed", "stage": "research", "cost_usd": 0.5},
    ]
    await _save_worker_row("wj", status="running", states=_worker_states(), events=worker_events)

    before = (await repository.reload_jobs(["wj"], video.get))["wj"]
    assert before["states"]["research"].status is StageStatus.DONE, "the premise"

    _mirror("wj")
    result = await main_mod.cancel_job("wj")
    assert result["status"] == "cancelled"

    after = (await repository.reload_jobs(["wj"], video.get))["wj"]
    assert after["status"] == "cancelled"

    done = [n for n, s in after["states"].items() if s.status is StageStatus.DONE]
    assert done == ["grounding", "research", "angle"], "the worker's finished stages were erased"
    assert after["states"]["research"].output is not None, "a DONE stage lost its output"

    from sqlalchemy import select

    from engine.db import session
    from engine.tables import Job

    async with session() as s:
        row = (await s.execute(select(Job).where(Job.id == "wj"))).scalar_one()
    assert row.cost_usd == pytest.approx(1.5), "the video's recorded spend was zeroed"
    assert [e["type"] for e in row.events][: len(worker_events)] == [
        e["type"] for e in worker_events
    ], "the worker's event log was replaced by the mirror's empty one"
    assert row.events[-1]["type"] == "workflow.cancelled", "the cancellation was not recorded"


# ── a mirror whose relay has died ───────────────────────────────────────────


def _finished_task() -> asyncio.Task:
    """A task object that is `done()` — a relay that has already returned.

    This is the state `_needs_resync` used to miss. `task is None` means "nothing in
    this process is following the job", and a finished task means exactly the same
    thing, but the old check read it as "the relay is live, trust the mirror".
    """

    async def already_over() -> None:
        return None

    task = asyncio.get_event_loop().create_task(already_over())
    return task


async def _stalled_mirror(job_id: str = "wj") -> dict:
    """A row that finished and a mirror that never heard about it."""
    await _save_worker_row(
        job_id,
        status="completed",
        states=_worker_states(),
        events=[{"type": "workflow.completed", "job_id": job_id}],
    )
    task = _finished_task()
    await task  # the relay has returned; the render finished without it
    return _mirror(job_id, status="running", task=task)


async def test_a_dead_relay_does_not_freeze_the_job_at_running(database):
    """The read endpoint has to converge on the row.

    Redis restarts, the worker is killed, the subscription drops — the relay task
    ends and leaves the mirror frozen at whatever the last event said. A render that
    finished perfectly well then answered `running` forever, and only a restart of
    the API fixed it.
    """
    await _stalled_mirror()
    assert JOBS["wj"]["status"] == "running", "the premise: the mirror is stale"

    assert (await main_mod.get_job("wj"))["status"] == "completed"


async def test_a_dead_relay_does_not_hang_an_open_stream(database):
    """`stream_job` parks on an in-process Event that nothing will ever fire again.

    The tab spins on a job that finished ten minutes ago. The stream must notice on
    its own and send the terminal frame, or the browser never learns the render is
    over.
    """
    await _stalled_mirror()

    response = await main_mod.stream_job("wj")
    seen = []

    async def drain():
        async for chunk in response.body_iterator:
            seen.append(chunk["event"] if isinstance(chunk, dict) else chunk)

    await asyncio.wait_for(drain(), timeout=15)

    assert seen[-1] == "stream.closed", f"the stream never terminated: {seen}"


async def test_a_dead_relay_does_not_block_the_publish_gate(database, monkeypatch):
    """409 "job is running" on a video that is finished and sitting on disk.

    The gate reads the mirror, so a frozen mirror is a video that cannot be
    published until the API is restarted. The quality blockers are stubbed out here
    because this is a test of the *status* check and nothing else — what the other
    blockers do has its own module.
    """
    from engine.api import publishing
    from engine.providers import youtube

    await _stalled_mirror()

    publishing.CHANNELS["default"] = youtube.Credentials(
        refresh_token_encrypted="enc", access_token="tok", channel_id="UC123"
    )
    monkeypatch.setattr(main_mod.automation, "publish_blockers", lambda *_a, **_kw: [])
    monkeypatch.setattr(youtube, "YouTube", lambda _creds: object())

    async def noop(job_id: str, start_from: str | None = None) -> None:
        JOBS[job_id]["status"] = "completed"

    monkeypatch.setattr(main_mod, "_run_job", noop)

    try:
        from engine.main import PublishRequest

        result = await main_mod.publish_job("wj", PublishRequest())
    finally:
        publishing.CHANNELS.clear()

    assert result["source_job_id"] == "wj"
