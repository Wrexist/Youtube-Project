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

from datetime import UTC, datetime, timedelta

import pytest

from engine import db, repository
from engine.quota import Entry, QuotaLedger
from engine.settings import get_settings
from engine.workflows.base import StageState, StageStatus

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
    from engine.workflows.base import Provenance, StageOutput

    job = _job()
    job["states"]["grounding"].status = StageStatus.DONE
    # With an output, deliberately: a DONE stage carrying none is the very state
    # the restore now refuses, because nothing downstream could read it.
    job["states"]["grounding"].output = StageOutput(
        value={"suggestions": ["why bridges collapse"]}, provenance=Provenance()
    )
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


async def test_the_stage_that_was_running_comes_back_actionable(database):
    """Marking the *job* interrupted was only half of it.

    The stage it died inside stayed `running`, and the UI does not let you touch a
    running stage — the row does not expand, so "Re-run from here" is unreachable.
    A render interrupted at sixteen of seventeen stages, with every expensive
    stage above it saved and paid for, had no way to ask for the last one again.
    """
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    job = _job(status="running")
    job["states"]["grounding"].status = StageStatus.DONE
    # With an output: a DONE stage carrying none is refused by the restore and
    # comes back STALE, which would make this test about the wrong thing.
    job["states"]["grounding"].output = StageOutput(value={"a": 1}, provenance=Provenance())
    job["states"]["render"].status = StageStatus.RUNNING
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    render = restored["j1"]["states"]["render"]
    assert render.status is StageStatus.FAILED, "a running stage must not survive a restart"
    assert "interrupted" in (render.error or "")
    # And the work above it is untouched — that is the whole point of resuming.
    assert restored["j1"]["states"]["grounding"].status is StageStatus.DONE


async def test_the_interruption_is_written_back_to_the_row(database):
    """Correcting the mirror alone did not survive contact with a read.

    `_resync` re-reads the row on the read endpoints and takes its status at face
    value — correctly, because for a worker-run job the row is the truth. So a row
    left saying "running" reverted the mirror moments after startup fixed it, and
    the correction never reached a screen. The row has to change too.
    """
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    job = _job(status="running")
    job["states"]["grounding"].status = StageStatus.DONE
    job["states"]["grounding"].output = StageOutput(value={"a": 1}, provenance=Provenance())
    job["states"]["render"].status = StageStatus.RUNNING
    await repository.save_job(job)

    await repository.load_jobs(video.get)

    # A second read, as a fresh process would do it: the row itself must now say
    # interrupted, not just the mirror the first call happened to return.
    again = await repository.reload_jobs(["j1"], video.get)
    assert again["j1"]["status"] == "interrupted"
    assert again["j1"]["states"]["render"].status is StageStatus.FAILED


async def test_the_event_log_ends_the_interrupted_stage(database):
    """The pipeline view rebuilds every row from the replayed *event log*, not from
    `states`. Fixing only the states left a spinner on a stage that stopped hours
    ago — which is how a recoverable job goes on looking unrecoverable."""
    from engine.workflows import video

    job = _job(status="running")
    job["states"]["render"].status = StageStatus.RUNNING
    job["events"].append({"type": "stage.started", "job_id": "j1", "stage": "render"})
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    render_events = [e for e in restored["j1"]["events"] if e.get("stage") == "render"]
    assert render_events[-1]["type"] == "stage.failed"
    assert "interrupted" in render_events[-1]["error"]


async def test_stages_that_were_not_running_are_left_alone(database):
    """Only the one that died. A pending stage is still pending."""
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    job = _job(status="running")
    job["states"]["grounding"].status = StageStatus.DONE
    job["states"]["grounding"].output = StageOutput(value={"a": 1}, provenance=Provenance())
    await repository.save_job(job)

    restored = await repository.load_jobs(video.get)
    assert restored["j1"]["states"]["grounding"].status is StageStatus.DONE
    assert restored["j1"]["states"]["render"].status is StageStatus.PENDING


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
    states, _ = repository.load_states(
        {"grounding": {"status": "done", "value": "x"}}, wf.initial_states()
    )
    assert states["grounding"].status is StageStatus.DONE
    assert states["render"].status is StageStatus.PENDING


async def test_an_unknown_status_in_the_row_does_not_crash_the_restore(database):
    from engine.workflows import video

    wf = video.get("video")
    states, _ = repository.load_states({"grounding": {"status": "banana"}}, wf.initial_states())
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


async def test_a_channel_with_an_expiry_comes_back_needing_a_refresh(database):
    """A restored credential is deliberately *not* fresh.

    This used to assert the opposite, and pinned a real fix while it did: SQLite has
    no timezone type, so a stored expiry came back naive while `is_fresh` compares
    it with `datetime.now(UTC)` — TypeError, not "stale", so the refresh that would
    have healed it never ran and every publish after a restart died on the
    comparison.

    The access token is no longer stored at all (it was plaintext OAuth in a
    column), so there is nothing left for the expiry to date and the loader returns
    neither. That makes `is_fresh` answer False, which is the same self-healing path
    the timezone fix was reaching for — the first publish refreshes from the
    encrypted refresh token. `test_a_naive_expiry_is_judged_rather_than_raised_on`
    below keeps the coercion itself pinned, independently of any store.
    """
    from engine.providers.youtube import Credentials

    expires = datetime.now(UTC) + timedelta(hours=1)
    await repository.save_channel(
        "default",
        Credentials(
            refresh_token_encrypted="ENCRYPTED",
            access_token="live-token",
            expires_at=expires,
            channel_id="UC123",
        ),
    )

    loaded = (await repository.load_channels())["default"]
    assert loaded.refresh_token_encrypted == "ENCRYPTED", "the durable half must survive"
    assert loaded.access_token == "", "a plaintext OAuth token came back out of the row"
    assert loaded.is_fresh is False, "a restored credential must be refreshed before use"


def test_a_naive_expiry_is_judged_rather_than_raised_on():
    """The second half of the fix, independent of any store.

    `load_channels` is not the only way a naive datetime reaches `Credentials` —
    anything that reconstructs one from JSON does the same. Raising there is the
    worst outcome available: it is not a refusal, so nothing retries, and it is not
    a refresh, so nothing heals.
    """
    from engine.providers.youtube import Credentials

    fresh = Credentials(
        refresh_token_encrypted="ENCRYPTED",
        access_token="live-token",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None),
    )
    assert fresh.is_fresh is True

    stale = Credentials(
        refresh_token_encrypted="ENCRYPTED",
        access_token="live-token",
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None),
    )
    assert stale.is_fresh is False, "expired is expired; it must route to refresh()"


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


# ── stage outputs must survive, or the stage must re-run ────────────────────
#
# Codex review, P1: persisting only status/error/timing left every restored DONE
# stage with `output=None`. `Workflow.run` skips a DONE stage, and the next stage
# reads it through `ctx.get()`, which raises when output is None — so a restored
# job died with "stage 'render' has not completed (status=done)". It also broke
# the worker path outright: `_relay` reloads states from the row on completion,
# so a good video was refused by the publish gate for having no thumbnail.


def test_ctx_get_rejects_a_done_stage_with_no_output():
    """The mechanism behind the bug, pinned so the fix cannot silently regress."""
    from engine.workflows.base import WorkflowContext, WorkflowError

    states = {"render": StageState(name="render", status=StageStatus.DONE, output=None)}
    ctx = WorkflowContext("j", {}, states, lambda _e: None, 8.0)

    with pytest.raises(WorkflowError, match="has not completed"):
        ctx.get("render")


def test_dataclass_values_round_trip_with_their_type():
    """`asdict` would flatten nested dataclasses to dicts and lose the type."""
    from engine.workflows.script import Beat, Script

    original = Script(
        hook="The bridge collapsed.",
        body="Here is why.",
        beats=[
            Beat(
                purpose="hook",
                text_direction="open on the collapse",
                visual_direction="a bridge",
                energy="high",
                est_seconds=4.0,
            )
        ],
    )
    restored = repository.decode_value(repository.encode_value(original))

    assert isinstance(restored, Script)
    assert isinstance(restored.beats[0], Beat), "nested dataclass lost its type"
    assert restored.beats[0].est_seconds == 4.0
    assert restored.full_text == original.full_text
    assert restored.beats[0].purpose == "hook"


def test_plain_values_round_trip():
    for value in ("renders/x.mp4", ["a", "b"], {"k": [1, 2]}, 3.5, None, True):
        assert repository.decode_value(repository.encode_value(value)) == value


def test_an_unencodable_value_is_refused_rather_than_guessed():
    with pytest.raises(repository.Unencodable):
        repository.encode_value(object())
    with pytest.raises(repository.Unencodable):
        repository.encode_value({1: "non-string key"})


def test_an_unknown_type_tag_refuses_to_decode():
    """A renamed class must re-run its stage, not produce a plausible wrong object."""
    with pytest.raises(repository.Unencodable, match="unknown stage value type"):
        repository.decode_value({"__type__": "ClassFromTheFuture", "__fields__": {}})


async def test_a_finished_job_restores_its_outputs(database):
    """The regression itself: a completed job must be publishable after a restart."""
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    job = _job()
    for name, value in (
        ("render", "renders/j1.mp4"),
        ("description", "A description."),
        ("tags", ["bridges", "engineering"]),
    ):
        job["states"][name].status = StageStatus.DONE
        job["states"][name].output = StageOutput(
            value=value, provenance=Provenance(model="claude-opus-4-8"), cost_usd=0.25
        )
    await repository.save_job(job)

    restored = (await repository.load_jobs(video.get))["j1"]
    assert restored["states"]["render"].output.value == "renders/j1.mp4"
    assert restored["states"]["tags"].output.value == ["bridges", "engineering"]
    assert restored["states"]["render"].status is StageStatus.DONE
    # Provenance is non-negotiable #2 — it has to survive with the value.
    assert restored["states"]["render"].output.provenance.model == "claude-opus-4-8"
    assert restored["states"]["render"].output.cost_usd == 0.25


async def test_a_restored_job_can_be_read_through_ctx_get(database):
    """End to end: what the next stage and the publish gate actually do."""
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput, WorkflowContext

    job = _job()
    job["states"]["render"].status = StageStatus.DONE
    job["states"]["render"].output = StageOutput(value="renders/j1.mp4", provenance=Provenance())
    await repository.save_job(job)

    restored = (await repository.load_jobs(video.get))["j1"]
    ctx = WorkflowContext("j1", {}, restored["states"], lambda _e: None, 8.0)
    assert ctx.get("render") == "renders/j1.mp4"


async def test_an_unencodable_output_makes_its_stage_and_dependents_rerun(database):
    """Correct in the other direction: never replay a stage we cannot reconstruct."""
    from engine.workflows import video
    from engine.workflows.base import Provenance, StageOutput

    job = _job()
    for name in ("grounding", "titles"):
        job["states"][name].status = StageStatus.DONE
    job["states"]["grounding"].output = StageOutput(value=object(), provenance=Provenance())
    job["states"]["titles"].output = StageOutput(value=["a title"], provenance=Provenance())

    await repository.save_job(job)
    restored = (await repository.load_jobs(video.get))["j1"]

    assert restored["states"]["grounding"].status is StageStatus.STALE
    # `titles` depends on `grounding`, so a re-run there invalidates it even
    # though its own value stored perfectly well.
    assert restored["states"]["titles"].status is StageStatus.STALE


async def test_a_row_written_before_values_were_stored_reruns(database):
    """Forward compatibility: old rows have status but no `value` key."""
    from engine.workflows import video

    wf = video.get("video")
    states, needs_rerun = repository.load_states(
        {"grounding": {"status": "done"}}, wf.initial_states()
    )
    assert states["grounding"].status is StageStatus.STALE
    assert needs_rerun == ["grounding"]


# ── persistence off must mean off everywhere ───────────────────────────────
#
# Codex review, P2: with STUDIO_PERSIST=false the lifespan handler skips
# `ensure_schema`, but the schedule and channel writes called the database
# unconditionally — so a scratch instance took an in-memory booking and *then*
# raised a missing-table error out of the endpoint.


async def test_schedule_writes_are_skipped_when_persistence_is_off(monkeypatch):

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_PERSIST", "false")
    monkeypatch.setenv("STUDIO_DATABASE_URL", "postgresql+asyncpg://nobody@127.0.0.1:1/none")
    try:
        # Would raise a connection error if it actually tried to write.
        await repository.save_slot("vid1", datetime.now(UTC))
        await repository.delete_slot("vid1")
        await repository.save_launch("l1", "running", "bridges", {})
    finally:
        get_settings.cache_clear()


async def test_channel_writes_are_skipped_when_persistence_is_off(monkeypatch):
    from engine.providers.youtube import Credentials

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_PERSIST", "false")
    monkeypatch.setenv("STUDIO_DATABASE_URL", "postgresql+asyncpg://nobody@127.0.0.1:1/none")
    try:
        await repository.save_channel("default", Credentials(refresh_token_encrypted="x"))
    finally:
        get_settings.cache_clear()


# ── the schema is stamped, so the documented migration command works ─────────
#
# `ensure_schema` built the tables with `create_all` and left `alembic_version`
# empty, so `alembic upgrade head` — documented in README, SETUP.md and CLAUDE.md —
# replayed the initial revision on top of tables that already existed and died with
# `table channel_launches already exists`. Any machine that had started the app once
# hit it, which is every machine.


def _head() -> str:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    return ScriptDirectory.from_config(Config(str(ini))).get_current_head()


async def _version_rows(url: str) -> list[str]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            names = await conn.run_sync(
                lambda c: __import__("sqlalchemy").inspect(c).get_table_names()
            )
            if "alembic_version" not in names:
                return []
            return [
                r[0] for r in (await conn.execute(text("SELECT version_num FROM alembic_version")))
            ]
    finally:
        await eng.dispose()


@pytest.fixture
async def fresh_sqlite(tmp_path, monkeypatch):
    """A private SQLite file, unlike the `database` fixture's shared handling.

    SQLite specifically, not `STUDIO_TEST_DATABASE_URL`: these tests are about the
    state of a *brand-new* database, and CI's Postgres is neither new nor private —
    the `database` fixture has already run `create_all` against it.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'stamp.db'}"
    get_settings.cache_clear()
    db.engine.cache_clear()
    monkeypatch.setenv("STUDIO_DATABASE_URL", url)
    monkeypatch.setenv("STUDIO_PERSIST", "true")
    yield url
    await db.engine().dispose()
    get_settings.cache_clear()
    db.engine.cache_clear()


def _table_count() -> int:
    """Read from the metadata rather than written down.

    These two tests are about stamping, not about how many tables exist. Hardcoded,
    the number turns every new table into two unrelated red tests and tells whoever
    added it nothing about what they broke.
    """
    from engine.tables import Base

    return len(Base.metadata.tables)


async def test_a_schema_built_from_metadata_is_stamped_at_head(fresh_sqlite):
    summary = await db.ensure_schema()
    assert _head() in summary, "the startup log should say which revision it landed on"
    assert await _version_rows(fresh_sqlite) == [_head()]


async def test_stamping_is_not_repeated_on_the_second_boot(fresh_sqlite):
    """A second row makes Alembic ambiguous about where the database is."""
    await db.ensure_schema()
    assert await db.ensure_schema() == f"schema present ({_table_count()} tables)"
    assert await _version_rows(fresh_sqlite) == [_head()]


async def test_a_partial_schema_is_not_stamped(fresh_sqlite):
    """The one case where claiming head would be a lie.

    Some tables present and some absent means a revision was probably added and not
    applied. Stamping there would make `upgrade head` skip the very migration that
    is pending — a silently missing column rather than a loud error.
    """
    from sqlalchemy import text

    from engine.tables import Base

    async with db.engine().begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.tables["jobs"].create(c)  # one table, not the set
        )
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    summary = await db.ensure_schema()
    assert "consider `alembic upgrade head`" in summary
    assert await _version_rows(fresh_sqlite) == []


async def test_a_missing_alembic_ini_does_not_break_startup(fresh_sqlite, monkeypatch):
    """Degrade to an unstamped schema, never to a boot failure.

    The engine reads `alembic.ini` from its own parent directory, which is not
    guaranteed to exist — installed as a wheel, it is not there.
    """
    monkeypatch.setattr(db, "_stamp_head", lambda conn: None)
    assert await db.ensure_schema() == f"created schema ({_table_count()} tables)"
    assert await _version_rows(fresh_sqlite) == []


async def test_sqlite_enforces_the_foreign_keys_postgres_does(database):
    """Dev and CI must agree about what the schema *is*.

    SQLite ships with foreign key checks off, so every `ForeignKey` in
    `tables.py` was decoration locally and a real constraint on the Postgres CI
    runs. A write pointing at a job that did not exist therefore passed for the
    length of a feature branch and failed the first time CI saw it.

    Asserted through a real write rather than by reading the pragma back: the
    pragma is per connection, so checking it on one connection proves nothing
    about the one the next session gets.

    **SQLite only, and not merely because Postgres would pass trivially.** A
    deliberately failed transaction leaves asyncpg's pooled connection bound to a
    dead loop, and every later test that borrows it dies with "attached to a
    different loop" — sixteen of them, in a file that had nothing to do with this.
    That cascade is precisely what made the original bug so expensive to read, so
    reproducing it on purpose to assert something Postgres guarantees by
    construction would be paying the cost twice for no information.
    """
    if not database.startswith("sqlite"):
        pytest.skip("asserts SQLite's pragma; Postgres enforces foreign keys natively")

    from sqlalchemy.exc import IntegrityError

    from engine.db import session
    from engine.tables import RepurposeProject

    with pytest.raises(IntegrityError):
        async with session() as db_session:
            db_session.add(RepurposeProject(id="orphan", job_id="no-such-job"))
