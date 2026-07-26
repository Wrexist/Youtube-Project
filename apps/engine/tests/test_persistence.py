"""Persistence tests against a real database.

`JOBS`, `CHANNELS`, `SCHEDULE` and the quota ledger were module-level dicts, so a
restart lost all of it. The worst case was the ledger: forget the day's spend and
the next upload silently overruns Google's 10,000-unit ceiling, which cannot be
undone and costs a day of publishing.

These run against `STUDIO_TEST_DATABASE_URL` if set, otherwise an on-disk SQLite
file — on disk rather than `:memory:` precisely because the point is to prove the
data outlives the process that wrote it. CI sets the Postgres URL.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from engine import db, repository
from engine.quota import Entry, QuotaLedger
from engine.settings import get_settings
from engine.tables import Base
from engine.workflows.base import StageStatus


@pytest.fixture
async def database(tmp_path, monkeypatch):
    """A migrated, empty database, torn down after each test."""
    url = os.environ.get("STUDIO_TEST_DATABASE_URL") or (
        f"sqlite+aiosqlite:///{tmp_path / 'studio.db'}"
    )
    get_settings.cache_clear()
    await db.dispose()
    monkeypatch.setenv("STUDIO_DATABASE_URL", url)

    async with db.engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield url

    await db.dispose()
    get_settings.cache_clear()


# ── the quota ledger: the one that must not be lost ─────────────────────────


async def test_spend_survives_a_restart(database):
    """The whole reason §5.1 was P1.

    Losing this means the next upload overruns Google's ceiling, and there is no
    way to un-spend the units.
    """
    before = QuotaLedger()
    await before.record("videos.insert")
    await before.record("captions.insert")
    assert before.spent() == 2000

    # A new ledger is a new process.
    after = QuotaLedger()
    assert after.spent() == 0, "a fresh ledger starts empty"
    await after.load()
    assert after.spent() == 2000
    assert after.remaining() == after.limit - 2000


async def test_the_breakdown_survives_too(database):
    led = QuotaLedger()
    await led.record("videos.insert")
    await led.record("search.list")
    await led.record("search.list")

    restored = QuotaLedger()
    await restored.load()
    assert restored.breakdown() == {"videos.insert": 1600, "search.list": 200}


async def test_uploads_left_is_correct_after_a_restart(database):
    led = QuotaLedger()
    for _ in range(2):
        await led.record("videos.insert")
        await led.record("thumbnails.set")
        await led.record("captions.insert")

    restored = QuotaLedger()
    await restored.load()
    assert restored.uploads_left() == 2  # 10000 - 4100 = 5900, // 2050


async def test_load_ignores_entries_outside_the_window(database):
    """`usage_by_day` charts 28 days; loading all history would grow without bound."""
    led = QuotaLedger()
    await led.record("videos.insert", at=datetime.now(UTC) - timedelta(days=120))
    await led.record("videos.insert")

    restored = QuotaLedger()
    await restored.load(days=35)
    assert len(restored.entries) == 1


async def test_a_persistence_failure_keeps_the_in_memory_entry(database, monkeypatch):
    """The units were already spent at Google.

    Dropping the entry because the write failed would over-report the remaining
    budget, which is the more dangerous direction to be wrong in.
    """
    led = QuotaLedger()

    def explode(*_a, **_kw):
        raise RuntimeError("database gone")

    monkeypatch.setattr(db, "session", explode)
    await led.record("videos.insert")
    assert led.spent() == 1600


async def test_persist_false_writes_nothing(database):
    led = QuotaLedger(persist=False)
    await led.record("videos.insert")
    assert led.spent() == 1600

    restored = QuotaLedger()
    await restored.load()
    assert restored.spent() == 0


# ── jobs ────────────────────────────────────────────────────────────────────


def _job(job_id: str = "j1", *, status: str = "running") -> dict:
    import asyncio

    from engine.workflows import video

    wf = video.get("video")
    return {
        "id": job_id,
        "workflow": wf,
        "inputs": {"topic": "why bridges collapse", "aspect": "9:16"},
        "states": wf.initial_states(),
        "events": [{"type": "workflow.started", "job_id": job_id}],
        "wake": asyncio.Event(),
        "status": status,
    }


async def test_a_job_round_trips(database):
    from engine.workflows import video

    job = _job()
    job["states"]["grounding"].status = StageStatus.DONE
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    assert set(restored) == {"j1"}
    assert restored["j1"]["inputs"]["topic"] == "why bridges collapse"
    assert restored["j1"]["states"]["grounding"].status is StageStatus.DONE
    assert restored["j1"]["events"][0]["type"] == "workflow.started"


async def test_a_job_running_at_shutdown_comes_back_interrupted(database):
    """It cannot be running — this process just started. Saying so lets the Queue
    screen offer a resume instead of showing a spinner that never resolves."""
    from engine.workflows import video

    await repository.save_job(_job(status="running"))
    restored = await repository.load_jobs(video.get)
    assert restored["j1"]["status"] == "interrupted"


async def test_a_finished_job_keeps_its_status(database):
    from engine.workflows import video

    await repository.save_job(_job(status="completed"))
    restored = await repository.load_jobs(video.get)
    assert restored["j1"]["status"] == "completed"


async def test_saving_twice_updates_rather_than_duplicates(database):
    from engine.workflows import video

    job = _job()
    await repository.save_job(job)
    job["status"] = "completed"
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    assert len(restored) == 1
    assert restored["j1"]["status"] == "completed"


async def test_a_live_client_in_inputs_is_not_serialised(database):
    """A publish job carries an instantiated YouTube client. It is rebuilt from the
    channel row on resume; trying to JSON it would fail the whole save."""
    from engine.workflows import video

    job = _job()
    job["inputs"]["youtube_client"] = object()
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    assert "youtube_client" not in restored["j1"]["inputs"]
    assert restored["j1"]["inputs"]["topic"] == "why bridges collapse"


async def test_a_stage_added_since_the_row_was_written_starts_pending(database):
    """A deploy mid-render must not orphan the job with a KeyError."""
    from engine.workflows import video

    wf = video.get("video")
    states = repository.load_states({"grounding": {"status": "done"}}, wf.initial_states())
    assert states["grounding"].status is StageStatus.DONE
    assert states["render"].status is StageStatus.PENDING


async def test_an_unknown_status_in_the_row_does_not_crash_the_restore(database):
    from engine.workflows import video

    wf = video.get("video")
    states = repository.load_states({"grounding": {"status": "banana"}}, wf.initial_states())
    assert states["grounding"].status is StageStatus.PENDING


# ── channels and schedule ───────────────────────────────────────────────────


async def test_a_channel_round_trips_without_a_plaintext_token(database):
    from engine.providers.youtube import Credentials

    creds = Credentials(
        refresh_token_encrypted="ENCRYPTED", access_token="live-token", channel_id="UC123"
    )
    await repository.save_channel("default", creds)

    restored = await repository.load_channels()
    assert restored["default"].channel_id == "UC123"
    assert restored["default"].refresh_token_encrypted == "ENCRYPTED"
    # There is no column for a plaintext refresh token, so one cannot be written.
    assert not hasattr(restored["default"], "refresh_token")


async def test_the_schedule_round_trips(database):
    at = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
    await repository.save_slot("vid1", at)
    await repository.save_slot("vid2", at + timedelta(days=1))

    restored = await repository.load_schedule()
    assert restored["vid1"] == at
    assert len(restored) == 2


async def test_unscheduling_removes_the_row(database):
    at = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
    await repository.save_slot("vid1", at)
    await repository.delete_slot("vid1")
    assert await repository.load_schedule() == {}


async def test_rescheduling_moves_rather_than_duplicates(database):
    first = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
    second = first + timedelta(days=2)
    await repository.save_slot("vid1", first)
    await repository.save_slot("vid1", second)

    restored = await repository.load_schedule()
    assert restored == {"vid1": second}


# ── serialisation helpers ───────────────────────────────────────────────────


def test_only_json_safe_inputs_are_kept():
    assert repository._is_json_safe({"a": [1, "x", None, True]})
    assert not repository._is_json_safe({"a": object()})
    assert not repository._is_json_safe([object()])
    assert not repository._is_json_safe({1: "non-string key"})


def test_dump_states_records_status_and_cost():
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    states = video.get("video").initial_states()
    states["grounding"].status = StageStatus.DONE
    states["grounding"].output = StageOutput(value=None, provenance=Provenance(), cost_usd=0.25)

    dumped = repository.dump_states(states)
    assert dumped["grounding"]["status"] == "done"
    assert dumped["grounding"]["cost_usd"] == 0.25


def test_a_summary_that_raises_does_not_fail_the_save():
    """A summary is cosmetic. Losing the whole job row over one is not a trade."""
    from engine.workflows.base import Provenance, StageOutput, StageState

    class Angry:
        def summary(self):
            raise ValueError("nope")

    state = StageState(name="x")
    state.output = StageOutput(value=Angry(), provenance=Provenance())
    assert repository._summary(state) == ""


def test_entry_round_trips_through_the_ledger_shape():
    """`Entry` is what `load()` rebuilds rows into; drift here breaks the restore."""
    entry = Entry(operation="videos.insert", cost=1600, at=datetime.now(UTC), note="x")
    assert entry.cost == 1600
    assert entry.channel_id == ""
