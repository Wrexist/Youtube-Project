"""Persistence for the things that used to be module-level dicts.

`JOBS`, `CHANNELS` and `SCHEDULE` were dicts, so a restart lost every job, channel
and booking — and, worst of all, the day's quota spend. The dict shapes were kept
compatible with this on purpose, so these functions are mostly a serialise/
deserialise pair rather than a redesign.

**`LAUNCHES` is the exception and this docstring used to imply otherwise.**
`save_launch`/`load_launches` exist and work, but nothing in the application calls
either — only a test does — so a channel launch is still lost on restart. It is not a
one-line fix: `load_launches` returns a flattened dict that does not match the mirror
shape `api/channels.py` reads (which needs `states`, `events`, `inputs`), so wiring it
up means rewriting the loader. What is lost is a regenerable LLM artifact on a flow
whose manual channel-creation step is a documented gap anyway, which is why this is
recorded rather than fixed. See KNOWN-ISSUES §5.8.

Jobs keep a **live in-process mirror** as well as their row. A running job holds
things that cannot go in a database — the `asyncio.Event` subscribers wait on,
the `asyncio.Task`, an instantiated `YouTube` client — so the row is the durable
record and the mirror is the runtime handle. `load_jobs()` rebuilds the mirror on
startup so a job that was mid-render when the process died comes back as
`interrupted` rather than silently vanishing.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from engine.db import session
from engine.insights import VideoRecord

if TYPE_CHECKING:  # `review` imports this module at call time; keep the cycle unrun.
    from engine.review import Snapshot
from engine.tables import (
    BacklogIdea,
    Channel,
    ChannelLaunch,
    Job,
    PerformanceRecord,
    ReviewSnapshot,
    ScheduleSlot,
)
from engine.workflows.base import StageState, StageStatus

# ── stage state (de)serialisation ───────────────────────────────────────────


class Unencodable(Exception):
    """A stage value that cannot be represented as JSON."""


@lru_cache(maxsize=1)
def _value_types() -> dict[str, type]:
    """Every dataclass a stage can produce, by class name.

    Discovered rather than listed, so adding a stage output type does not mean
    remembering to register it here — forgetting would silently downgrade that
    stage to "re-run on restore", which is the kind of thing nobody notices.
    """
    from dataclasses import is_dataclass

    from engine.research import keywords
    from engine.workflows import media, script, seo

    found: dict[str, type] = {}
    for module in (script, seo, media, keywords):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and is_dataclass(obj):
                found[name] = obj
    return found


def encode_value(value: Any) -> Any:
    """A stage output value as JSON, preserving dataclass identity.

    `dataclasses.asdict` is not usable here: it recurses into nested dataclasses
    and flattens them to plain dicts, so `Script.beats` would come back as a list
    of dicts and nothing downstream could tell it had ever been a `Beat`. Walking
    the fields keeps the type tag at every level.

    Raises `Unencodable` rather than guessing. A value that silently round-trips
    to something *different* is far worse than one that re-runs its stage.
    """
    from dataclasses import fields, is_dataclass

    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, list | tuple):
        return [encode_value(v) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise Unencodable("dict with non-string keys")
        return {k: encode_value(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            "__fields__": {f.name: encode_value(getattr(value, f.name)) for f in fields(value)},
        }
    raise Unencodable(f"{type(value).__name__} has no JSON form")


def decode_value(raw: Any) -> Any:
    """Inverse of `encode_value`. Raises `Unencodable` on an unknown type tag."""
    if isinstance(raw, list):
        return [decode_value(v) for v in raw]
    if isinstance(raw, dict):
        tag = raw.get("__type__")
        if tag is None:
            return {k: decode_value(v) for k, v in raw.items()}
        cls = _value_types().get(tag)
        if cls is None:
            # The class was renamed or removed since the row was written. Re-run
            # the stage rather than hand a later stage something it cannot use.
            raise Unencodable(f"unknown stage value type {tag!r}")
        try:
            return cls(**{k: decode_value(v) for k, v in raw["__fields__"].items()})
        except TypeError as exc:
            # Fields were added or removed. Same reasoning as above.
            raise Unencodable(f"{tag} no longer accepts the stored fields: {exc}") from exc
    return raw


def dump_states(states: dict[str, StageState]) -> dict:
    """Stage states as JSON, **including each completed stage's output value**.

    The values have to be here. `Workflow.run` skips a stage whose status is DONE,
    and every later stage reads its dependencies through `ctx.get()`, which raises
    when `output` is None — so a restored job with status but no values fails with
    "stage 'render' has not completed (status=done)", which is both wrong and
    self-contradictory. It also broke the worker path outright: `_relay` reloads
    states from the row when the job finishes, so a perfectly good video would be
    refused by the publish gate for having no thumbnail and no sources.

    A value that cannot be encoded is stored as `{"unencodable": ...}` and the
    stage re-runs on restore. That costs time; handing a later stage a
    half-reconstructed object would cost correctness.
    """
    out: dict = {}
    for name, state in states.items():
        entry = {
            "status": state.status.value,
            "error": state.error,
            "attempts": state.attempts,
            "elapsed_ms": state.elapsed_ms,
            "cost_usd": state.output.cost_usd if state.output else 0.0,
            "summary": _summary(state),
        }
        if state.output is not None:
            try:
                entry["value"] = encode_value(state.output.value)
                entry["artifacts"] = state.output.artifacts
                entry["provenance"] = _dump_provenance(state.output.provenance)
            except Unencodable as exc:
                logger.warning("stage {} output is not storable ({}); it will re-run", name, exc)
                entry["unencodable"] = str(exc)
        out[name] = entry
    return out


def _dump_provenance(provenance) -> dict:
    """Provenance is the Phase 8 audit trail — non-negotiable #2 — so it is stored
    with the value rather than regenerated, which would lose the model that ran."""
    try:
        return {
            "model": getattr(provenance, "model", None),
            "prompt": getattr(provenance, "prompt", None),
            "sources": list(getattr(provenance, "sources", []) or []),
            "params": encode_value(getattr(provenance, "params", {}) or {}),
        }
    except Unencodable:
        return {}


def _load_provenance(raw: dict):
    from engine.workflows.base import Provenance

    provenance = Provenance()
    for key in ("model", "prompt"):
        if raw.get(key) is not None:
            setattr(provenance, key, raw[key])
    if raw.get("sources"):
        provenance.sources = list(raw["sources"])
    if raw.get("params"):
        provenance.params = raw["params"]
    return provenance


def _summary(state: StageState) -> str:
    if state.output is None:
        return ""
    value = state.output.value
    try:
        return value.summary() if hasattr(value, "summary") else ""
    except Exception:  # noqa: BLE001 — a summary is cosmetic, never fail a save for it
        return ""


def load_states(
    raw: dict, template: dict[str, StageState]
) -> tuple[dict[str, StageState], list[str]]:
    """Rebuild stage states onto a fresh workflow's state map.

    `template` comes from `Workflow.initial_states()`, so a stage added since the
    row was written starts PENDING instead of raising a KeyError — a deploy in
    the middle of a long render should not orphan the job.

    Returns the states and the names of any stage that has to re-run because its
    output could not be rebuilt. The caller propagates staleness to their
    dependents: a stage that re-runs may produce a different value, so anything
    computed from the old one is no longer trustworthy.
    """
    from engine.workflows.base import StageOutput

    needs_rerun: list[str] = []

    for name, state in template.items():
        stored = raw.get(name)
        if not stored:
            continue
        try:
            state.status = StageStatus(stored["status"])
        except ValueError:
            # A status this build does not know — a downgrade, or a hand-edited
            # row. Re-running the stage is the safe reading of "unrecognised".
            state.status = StageStatus.PENDING
        state.error = stored.get("error") or ""
        state.attempts = stored.get("attempts") or 0

        if state.status is StageStatus.DONE:
            if "value" not in stored:
                # Either the value was unencodable, or the row predates values
                # being stored at all. Both mean the same thing downstream.
                state.status = StageStatus.STALE
                state.output = None
                needs_rerun.append(name)
            else:
                try:
                    state.output = StageOutput(
                        value=decode_value(stored["value"]),
                        provenance=_load_provenance(stored.get("provenance") or {}),
                        cost_usd=stored.get("cost_usd") or 0.0,
                        artifacts=stored.get("artifacts") or {},
                    )
                except Unencodable as exc:
                    logger.warning(
                        "stage {} output could not be rebuilt ({}); re-running", name, exc
                    )
                    state.status = StageStatus.STALE
                    state.output = None
                    needs_rerun.append(name)

        # `elapsed_ms` is derived from `time.monotonic()`, which is meaningless
        # across processes — there is no clock to restore it against. The stored
        # duration is reconstructed as a monotonic *interval* so the Queue screen
        # still shows how long the stage took; if the stage re-runs, `_run_stage`
        # overwrites both ends anyway.
        elapsed = stored.get("elapsed_ms") or 0
        if elapsed:
            state.started_at = 0.0
            state.finished_at = elapsed / 1000
    return template, needs_rerun


# ── jobs ────────────────────────────────────────────────────────────────────


async def save_job(job: dict) -> None:
    """Upsert a job row from the in-process mirror.

    Called after every stage. The write is small — three JSON columns — and it is
    what makes a render resumable, so it is not batched or deferred.
    """
    async with session() as s:
        row = await s.get(Job, job["id"])
        if row is None:
            row = Job(id=job["id"])
            s.add(row)
        row.workflow = job["workflow"].name
        row.status = job["status"]
        row.inputs = _jsonable(job.get("inputs", {}))

        states = dump_states(job.get("states", {}))
        if job.get("enqueued") and _is_pristine(states) and not _is_pristine(row.states or {}):
            # Belt and braces for the API process's mirror of a *worker* job. It
            # never executes the workflow, so its `states` stay all-PENDING while
            # the worker writes the real ones — and a full save from this side
            # replaced a row holding six finished stages with a blank one, which
            # makes the job unresumable and zeroes its recorded cost. The caller
            # that used to do this (`cancel_job`) now writes through
            # `update_job_status`; this is what catches the next one.
            logger.warning(
                "refusing to blank job {}'s stages from a worker-job mirror; writing status only",
                job["id"],
            )
        else:
            row.states = states
            row.cost_usd = _cost_of(job)

        # Only ever forward, for the same reason `main._resync` only grows its log:
        # the worker's row may already carry events this mirror never saw.
        events = job.get("events", [])
        if len(events) >= len(row.events or []):
            row.events = events

        row.error = job.get("error", "") or ""
        row.source_job_id = job.get("inputs", {}).get("source_job_id")
        row.updated_at = datetime.now(UTC)


def _is_pristine(states: dict) -> bool:
    """True when no stage in this map has started. An empty map counts."""
    return all(
        str((state or {}).get("status", "pending")) == StageStatus.PENDING.value
        for state in states.values()
    )


async def update_job_status(job_id: str, status: str, extra_event: dict | None = None) -> bool:
    """Write a job's status, and optionally append one event. Nothing else.

    For the case where this process knows the *outcome* but not the work:
    cancelling a job the render worker is executing. A full `save_job` there wrote
    the API's pristine mirror — no stage outputs, zero cost — straight over the
    worker's row, so a cancel at stage twelve threw away eleven finished stages and
    the money they cost. The narrow write leaves `states`, `cost_usd` and the
    worker's own events exactly as they are.

    Returns False when there is no row to update, so the caller can tell "wrote it"
    from "there was nothing there".
    """
    if not _persistence_enabled():
        return False
    async with session() as s:
        row = await s.get(Job, job_id)
        if row is None:
            return False
        row.status = status
        if extra_event is not None:
            # Reassigned rather than appended in place: `events` is a JSON column
            # and SQLAlchemy does not track mutation of the list it handed back, so
            # an in-place append is simply not written.
            row.events = [*(row.events or []), extra_event]
        row.updated_at = datetime.now(UTC)
        return True


def _cost_of(job: dict) -> float:
    return round(
        sum(s.output.cost_usd for s in job.get("states", {}).values() if s.output),
        4,
    )


#: Input keys that are datetimes. Stored as ISO strings and parsed back on load —
#: without this they were dropped entirely by the json-safety filter, so a publish
#: job that survived a restart lost its `publish_at` and `UploadStage` read None,
#: which means "publish now, publicly" instead of "private until the scheduled time".
_DATETIME_INPUTS = ("publish_at",)


def jsonable(inputs: dict) -> dict:
    """Inputs reduced to what can be stored and served.

    `youtube_client` is a live client instance that a publish job carries — it holds
    an access token, so it must reach neither the database nor an HTTP response.

    It is rebuilt at *dispatch* time, not on restore: `main._run_job` and
    `worker.run_job_task` both call `api.publishing.attach_youtube_client` before
    starting the workflow, so both execution paths get one and the restored mirror
    — which is the dict this function serialises — never holds a live token. This
    docstring used to claim the rebuild happened "on resume" when nothing rebuilt it
    at all, and the missing key surfaced as a bare KeyError that `CaptionsStage`,
    being optional, swallowed into SKIPPED on an already-live video.
    """
    out = {}
    for key, value in inputs.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif _is_json_safe(value):
            out[key] = value
    return out


# ── performance records ─────────────────────────────────────────────────────


#: Fields renamed since rows were first written, old name -> new name.
#:
#: `retention_30s` only ever held `averageViewPercentage`, so it was renamed to
#: say so. The rename alone silently destroyed history: `VideoRecord(**payload)`
#: raises TypeError on the unknown key, `_record_from_payload` caught it, and
#: every performance record written before the rename was dropped at startup with
#: one warning line. The attribution loop's entire sample, gone on upgrade.
_RENAMED_FIELDS = {"retention_30s": "avd_percent"}


def _record_from_payload(payload: dict) -> VideoRecord | None:
    """Rebuild a stored record, tolerating the shapes older rows were written in.

    Unknown keys are dropped rather than fatal. A field removed in a later version
    should cost that field, not the row — losing the row loses the provenance that
    is the only reason this table exists, and it does so quietly.
    """
    known = {f.name for f in fields(VideoRecord)}
    migrated: dict[str, Any] = {}
    unknown: list[str] = []

    # Canonical names first, then the renamed ones fill what is still missing.
    # A single pass with `setdefault` let key *order* decide: a payload carrying
    # both `retention_30s` and `avd_percent` took whichever came first, so a
    # migrated legacy value could silently beat the real one. Rare, but the rule
    # should be stated rather than fall out of dict ordering.
    for key, value in payload.items():
        if key in known:
            migrated[key] = value
        elif key not in _RENAMED_FIELDS:
            unknown.append(key)

    for old, new in _RENAMED_FIELDS.items():
        if old in payload and new not in migrated:
            migrated[new] = payload[old]

    if unknown:
        logger.info("ignoring {} unknown field(s) on a stored record: {}", len(unknown), unknown)

    try:
        return VideoRecord(**migrated)
    except TypeError as exc:
        # Only a *missing required* field reaches here now.
        logger.warning("dropping malformed performance record: {}", exc)
        return None


async def save_performance_record(record: VideoRecord, *, job_id: str | None = None) -> None:
    """Persist one published video's attribution seed for the feedback loop."""
    async with session() as db:
        existing = await db.get(PerformanceRecord, record.video_id)
        payload = asdict(record)
        if existing is None:
            db.add(PerformanceRecord(video_id=record.video_id, job_id=job_id, payload=payload))
        else:
            existing.job_id = job_id or existing.job_id
            existing.payload = payload
            existing.measured_at = datetime.now(UTC)


async def load_performance_records() -> dict[str, VideoRecord]:
    """Load published-video records used by /v1/insights and new generations."""
    async with session() as db:
        rows = (await db.execute(select(PerformanceRecord))).scalars().all()
    records: dict[str, VideoRecord] = {}
    for row in rows:
        record = _record_from_payload(row.payload or {})
        if record is not None:
            records[record.video_id] = record
    return records


async def save_review_snapshot(
    payload: Snapshot, video_count: int, report: dict | None = None
) -> None:
    """Record what the weekly review believed, and what it said.

    Two things, one row, because they are produced together and a report without
    the snapshot it was diffed against is not interpretable. `report` is optional
    only so the older call signature keeps working.
    """
    if not _persistence_enabled():
        return
    async with session() as db:
        db.add(ReviewSnapshot(payload=payload, video_count=video_count, report=report))


async def add_backlog_ideas(ideas: list[dict], *, model: str = "", prompt: str = "") -> int:
    """Put freshly scored ideas on the backlog. Returns how many were new.

    Anything already on the list — open, made, or refused — is skipped rather than
    updated. Re-scoring an idea the operator already said no to and floating it back
    to the top is the behaviour a backlog exists to stop.

    `model` and `prompt` are what produced the batch, and they are stored on every
    row of it. CLAUDE.md #2 admits no exception for throwaway generations, and an
    idea that shapes a whole video is not throwaway.

    Insertion is per-row inside a savepoint. The pre-query filter is a *read*, so
    two top-ups running at once can both pass it and then collide on the unique
    index — one lost idea is acceptable, a 500 on the whole request is not. The
    batch is also deduplicated first, because `_score` has no reason to guarantee
    distinct topics and two identical ones in a single list would collide with each
    other before any concurrency was involved.
    """
    if not _persistence_enabled() or not ideas:
        return 0

    unique: dict[str, dict] = {}
    for idea in ideas:
        topic = str(idea.get("topic") or "").strip()
        if topic:
            unique.setdefault(topic, idea)

    added = 0
    async with session() as db:
        known = {
            row
            for (row,) in (
                await db.execute(
                    select(BacklogIdea.topic).where(BacklogIdea.topic.in_(list(unique)))
                )
            ).all()
        }
        for topic, idea in unique.items():
            if topic in known:
                continue
            try:
                async with db.begin_nested():
                    db.add(
                        BacklogIdea(
                            topic=topic,
                            score=float(idea.get("score") or 0.0),
                            demand=float(idea.get("demand") or 0.0),
                            competition=float(idea.get("competition") or 0.0),
                            why=idea.get("why") or "",
                            model=model,
                            prompt=prompt,
                        )
                    )
            except IntegrityError:
                # Another top-up won the race for this topic. It is on the list
                # either way, which is the outcome that matters.
                logger.debug("backlog topic {!r} was added concurrently", topic)
                continue
            added += 1
    return added


async def open_backlog_ideas(limit: int = 20) -> list[dict]:
    """The unmade, unrefused ideas, best first."""
    if not _persistence_enabled():
        return []
    async with session() as db:
        rows = (
            (
                await db.execute(
                    select(BacklogIdea)
                    .where(BacklogIdea.status == "open")
                    .order_by(BacklogIdea.score.desc(), BacklogIdea.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "topic": r.topic,
            "score": r.score,
            "demand": r.demand,
            "competition": r.competition,
            "why": r.why,
        }
        for r in rows
    ]


async def resolve_backlog_idea(
    *, idea_id: int | None = None, topic: str | None = None, status: str, job_id: str | None = None
) -> bool:
    """Take an idea off the list, by id or by topic. True if one was open.

    By topic as well as by id because that is how an idea gets *used*: the Create
    screen sends a topic to `POST /v1/jobs` and never mentions the backlog, so the
    only thing linking the two is the string.
    """
    if not _persistence_enabled() or (idea_id is None and topic is None):
        return False

    # One conditional UPDATE, not select-then-mutate. Two callers racing on the same
    # row would both have seen it `open`, both written, and both returned True — so
    # re-running a topic could overwrite the `job_id` of the job that genuinely
    # consumed the idea. `rowcount` makes exactly one of them win.
    statement = (
        update(BacklogIdea)
        .where(BacklogIdea.status == "open")
        .values(status=status, job_id=job_id, resolved_at=datetime.now(UTC))
    )
    statement = (
        statement.where(BacklogIdea.id == idea_id)
        if idea_id is not None
        else statement.where(BacklogIdea.topic == topic)
    )

    async with session() as db:
        result = await db.execute(statement)
    return bool(result.rowcount)


async def spend_by_day(days: int = 90) -> list[tuple[str, float, int]]:
    """What the channel cost, per UTC day: `(date, usd, jobs)`, oldest first.

    Read off the `jobs` table rather than out of `automation.SpendLedger`. The
    ledger is in-memory, series-scoped and written by nothing, so persisting it
    would mean inventing a second record of a number the jobs table already holds
    — and the jobs table is the one that is actually true, because `cost_usd` is
    written there by the same stage boundary that spends the money.

    Grouped in Python, not in SQL. `date_trunc` is Postgres, `strftime` is SQLite,
    and this runs on both; ninety days of jobs is a few hundred rows.
    """
    if not _persistence_enabled():
        return []
    since = datetime.now(UTC) - timedelta(days=days)
    async with session() as db:
        rows = (
            await db.execute(select(Job.created_at, Job.cost_usd).where(Job.created_at >= since))
        ).all()

    totals: dict[str, tuple[float, int]] = {}
    for created_at, cost in rows:
        # SQLite hands back naive datetimes even for a timezone-aware column, so
        # a bare `.astimezone()` would read them as *local* and shift a late-night
        # job into the next day.
        moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        key = moment.astimezone(UTC).date().isoformat()
        usd, count = totals.get(key, (0.0, 0))
        totals[key] = (usd + (cost or 0.0), count + 1)

    return [(day, round(usd, 4), count) for day, (usd, count) in sorted(totals.items())]


async def completed_video_costs(days: int = 90) -> list[float]:
    """What each finished video actually cost, for the per-video average.

    `workflow == "video"` on purpose. A publish job is a separate row that costs
    almost nothing, and counting it would halve the apparent price of a video by
    adding a near-zero sample rather than by making anything cheaper.
    """
    if not _persistence_enabled():
        return []
    since = datetime.now(UTC) - timedelta(days=days)
    async with session() as db:
        rows = (
            await db.execute(
                select(Job.cost_usd).where(
                    Job.created_at >= since,
                    Job.status == "completed",
                    Job.workflow == "video",
                )
            )
        ).all()
    return [cost or 0.0 for (cost,) in rows]


async def latest_review() -> dict | None:
    """The most recent readable review, or None if none has been stored.

    Distinct from `latest_review_snapshot`, which returns the diff baseline. This
    is the one a screen renders. None covers both "no review has ever run" and
    "the last run predates this column"; the screen says the same thing for both,
    because from the reader's side they are the same thing.
    """
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = (
            await db.execute(
                select(ReviewSnapshot)
                .where(ReviewSnapshot.report.is_not(None))
                .order_by(ReviewSnapshot.generated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row.report if row is not None else None


async def latest_review_snapshot() -> Snapshot | None:
    """The most recent snapshot, or None when no review has ever run.

    None and an empty snapshot are different answers and must stay so: no previous
    review means every finding is reported as new, while a previous review that
    found nothing means a finding appearing now genuinely appeared.
    """
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = (
            await db.execute(
                select(ReviewSnapshot).order_by(ReviewSnapshot.generated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
    return row.payload if row is not None else None


# The old private name, kept because save_job and the tests both use it.
_jsonable = jsonable


def _restore_inputs(inputs: dict) -> dict:
    """Turn the ISO strings from `jsonable` back into datetimes.

    `UploadStage` calls `.astimezone()` on `publish_at`, so handing it a string
    would trade a dropped schedule for an AttributeError mid-upload.
    """
    out = dict(inputs)
    for key in _DATETIME_INPUTS:
        raw = out.get(key)
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                logger.warning("could not parse {}={!r}; dropping it", key, raw)
                out.pop(key)
                continue
            out[key] = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return out


def _is_json_safe(value: Any) -> bool:
    if isinstance(value, str | int | float | bool | type(None)):
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False


def _aware(moment: datetime | None) -> datetime | None:
    """A datetime that can be compared with one from `datetime.now(UTC)`.

    Postgres round-trips the offset; SQLite does not store one at all. Assuming UTC
    for a naive value is correct here because every write goes through
    `datetime.now(UTC)`.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _mirror_of(row: Job, workflow) -> dict:
    """One row as a mirror entry, status taken verbatim.

    Shared by the startup loader and the mid-life re-read below; the difference
    between them is only what "running" is allowed to mean, and that is the
    caller's decision, not this function's.
    """
    import asyncio

    states, needs_rerun = load_states(row.states or {}, workflow.initial_states())

    # A stage that has to re-run may produce a different value, so anything
    # downstream of it is no longer trustworthy either. The framework already
    # models this for user edits; the same rule applies here.
    for name in needs_rerun:
        for dependent in workflow.dependents_of(name):
            states[dependent].status = StageStatus.STALE
            states[dependent].output = None
    if needs_rerun:
        logger.warning(
            "job {}: {} stage(s) will re-run ({})",
            row.id,
            len(needs_rerun),
            ", ".join(needs_rerun),
        )

    return {
        "id": row.id,
        "workflow": workflow,
        "inputs": _restore_inputs(row.inputs or {}),
        "states": states,
        "events": row.events or [],
        "wake": asyncio.Event(),
        "status": row.status,
        "error": row.error,
        # Normalised on the way out: SQLite has no timezone type and hands
        # back naive datetimes, while a job created in this process carries an
        # aware one. Sorting the two together raises TypeError, so `GET
        # /v1/jobs` died the moment a restored job and a new job coexisted.
        "created_at": _aware(row.created_at),
        "updated_at": _aware(row.updated_at),
    }


def _fail_running_stages(entry: dict, job_id: str) -> None:
    """Mark the stage that died with the process, so it can be acted on.

    Marking the *job* interrupted was only half of it. The stage it was inside
    stayed `running` forever, and a running stage is not something the UI lets
    you touch: the row does not expand, so "Re-run from here" is unreachable, and
    the pipeline shows a spinner for work that stopped when the process did.

    That left the one recovery path unreachable on the one job that needed it —
    a render interrupted at sixteen of seventeen stages, with every expensive
    stage above it already paid for and saved, and no way to ask for the last one
    again.

    `FAILED` rather than a new status: it is accurate (the stage did not finish),
    every screen already renders it, and `failed` is exactly the state the
    re-run affordance is built for. The message says which kind of failure it
    was, because "interrupted" and "this stage threw" want different reactions.
    """
    reason = (
        "interrupted — the engine stopped while this stage was running. "
        "Everything above it is still saved; re-run from here."
    )
    for name, state in entry.get("states", {}).items():
        if state.status is StageStatus.RUNNING:
            state.status = StageStatus.FAILED
            state.error = reason
            # And an event to match, because the two are read by different things
            # and disagreeing is worse than either being wrong. `states` answers
            # `GET /v1/jobs/{id}`; the *event log* is what the SSE stream replays,
            # and it is what the pipeline view rebuilds every stage row from. Fix
            # only the states and the Create screen still shows a spinner on a
            # stage that stopped hours ago — which is exactly how a recoverable
            # job goes on looking unrecoverable.
            entry.setdefault("events", []).append(
                {
                    "type": "stage.failed",
                    "job_id": job_id,
                    "stage": name,
                    "error": reason,
                    "message": reason,
                }
            )
            logger.warning("job {}: stage {!r} was interrupted; marked failed", job_id, name)


async def load_jobs(get_workflow) -> dict[str, dict]:
    """Rebuild the in-process job mirror from rows. Called once at startup.

    A job whose row says "running" cannot actually be running — this process just
    started — so it is marked `interrupted`. That is honest, and it is what lets
    the Queue screen offer a resume instead of showing a spinner forever.
    """
    mirror: dict[str, dict] = {}
    async with session() as s:
        rows = (await s.execute(select(Job).order_by(Job.created_at))).scalars().all()

    interrupted = 0
    interrupted_rows: list[dict] = []
    for row in rows:
        try:
            workflow = get_workflow(row.workflow)
        except KeyError:
            logger.warning("job {} ran unknown workflow {!r}; skipping", row.id, row.workflow)
            continue

        entry = _mirror_of(row, workflow)
        if entry["status"] == "running":
            entry["status"] = "interrupted"
            _fail_running_stages(entry, row.id)
            interrupted += 1
            # Written back, not just corrected in memory. The row is the durable
            # record and it is still claiming `running` for a job that provably
            # is not — this process has only just started. Leaving it is not a
            # harmless inaccuracy: `_resync` re-reads the row on the read
            # endpoints and takes its status at face value, so the mirror was
            # being reverted to "running" moments after being fixed, and the
            # correction never survived long enough to reach a screen.
            interrupted_rows.append(entry)
        mirror[row.id] = entry

    for entry in interrupted_rows:
        try:
            await save_job(entry)
        except Exception:  # noqa: BLE001 - a stale row must not stop the engine booting
            logger.exception("could not persist the interrupted status of job {}", entry["id"])

    if interrupted:
        logger.warning("{} job(s) were mid-run at shutdown; marked interrupted", interrupted)
    logger.info("restored {} job(s)", len(mirror))
    return mirror


async def reload_jobs(job_ids: list[str], get_workflow) -> dict[str, dict]:
    """Re-read specific rows **mid-life**, taking their status at face value.

    Not `load_jobs`. That one is the startup loader and rewrites a "running" row
    to "interrupted", which is the right reading exactly once — when the process
    has just begun and therefore cannot be running anything. Every other time it
    is a lie: the arq worker is running the job right now, in another process, and
    it is the only writer of that row.

    This exists because the API's mirror is written only by the process that
    dispatched the job. Restart the API mid-render and every later answer came
    from the frozen snapshot the lifespan restored — the job sat at `interrupted`,
    0 stages done, forever, and the publish gate refused it for not being
    `completed` until somebody restarted the API a *second* time.
    """
    if not job_ids:
        return {}
    async with session() as s:
        rows = (await s.execute(select(Job).where(Job.id.in_(job_ids)))).scalars().all()

    out: dict[str, dict] = {}
    for row in rows:
        try:
            workflow = get_workflow(row.workflow)
        except KeyError:
            logger.warning("job {} ran unknown workflow {!r}; skipping", row.id, row.workflow)
            continue
        out[row.id] = _mirror_of(row, workflow)
    return out


# ── channels ────────────────────────────────────────────────────────────────


def _persistence_enabled() -> bool:
    """Whether writes should touch the database.

    With STUDIO_PERSIST=false the lifespan handler skips `ensure_schema`, so the
    tables may not exist at all. Jobs already checked this before saving; the
    schedule and channel writes did not, so a scratch instance took an in-memory
    booking and *then* raised a missing-table error from the endpoint.
    """
    from engine.settings import get_settings

    return get_settings().persist


async def save_channel(key: str, creds) -> None:
    if not _persistence_enabled():
        return
    async with session() as s:
        row = await s.get(Channel, key)
        if row is None:
            row = Channel(key=key, refresh_token_encrypted=creds.refresh_token_encrypted)
            s.add(row)
        row.channel_id = creds.channel_id
        row.refresh_token_encrypted = creds.refresh_token_encrypted
        # The access token is deliberately *not* written. CLAUDE.md #4 keeps
        # secrets out of anywhere they are not encrypted, and this column was
        # plaintext OAuth — a live credential for the channel, sitting in a table
        # next to the refresh token that is encrypted precisely because it is one.
        #
        # Nothing is lost by dropping it. `save_channel` is not called after
        # `refresh()`, so the stored value was already stale inside the hour, and
        # `load_channels` restoring a dead token is indistinguishable from restoring
        # none: `is_fresh` says no and the provider refreshes on first use. The
        # columns are left in place and cleared rather than migrated away, so a row
        # written by an older build stops carrying a token the moment it is saved.
        row.access_token = ""
        row.expires_at = None


async def load_channels() -> dict:
    from engine.providers.youtube import Credentials

    async with session() as s:
        rows = (await s.execute(select(Channel))).scalars().all()

    out = {}
    for row in rows:
        out[row.key] = Credentials(
            refresh_token_encrypted=row.refresh_token_encrypted,
            # Never read back, even when an old row still holds one — see
            # `save_channel`. Restoring nothing makes `is_fresh` False, which sends
            # the first publish through `refresh()` and heals the credential from
            # the encrypted refresh token, the only thing worth persisting.
            #
            # (This also disposes of the reason `expires_at` needed normalising to
            # an aware datetime here: with no token to date, nothing compares it.)
            access_token="",
            expires_at=None,
            channel_id=row.channel_id,
        )
    logger.info("restored {} channel(s)", len(out))
    return out


# ── schedule ────────────────────────────────────────────────────────────────


async def save_slot(video_id: str, at: datetime, *, job_id: str | None = None) -> None:
    if not _persistence_enabled():
        return
    async with session() as s:
        row = await s.get(ScheduleSlot, video_id)
        if row is None:
            row = ScheduleSlot(video_id=video_id, at=at)
            s.add(row)
        row.at = at
        if job_id:
            row.job_id = job_id


async def delete_slot(video_id: str) -> None:
    if not _persistence_enabled():
        return
    async with session() as s:
        await s.execute(delete(ScheduleSlot).where(ScheduleSlot.video_id == video_id))


async def load_schedule() -> dict[str, datetime]:
    async with session() as s:
        rows = (await s.execute(select(ScheduleSlot))).scalars().all()
    out = {r.video_id: (r.at if r.at.tzinfo else r.at.replace(tzinfo=UTC)) for r in rows}
    logger.info("restored {} scheduled video(s)", len(out))
    return out


# ── channel launches ────────────────────────────────────────────────────────
#
# UNWIRED. Neither function below has an application caller — grep says the only
# call site in the repository is a test. A channel launch therefore does not survive
# a restart, whatever the `ChannelLaunch` table's existence suggests. Do not treat
# this section as working persistence; see the note at the top of the module.


async def save_launch(launch_id: str, status: str, niche: str, payload: dict) -> None:
    if not _persistence_enabled():
        return
    async with session() as s:
        row = await s.get(ChannelLaunch, launch_id)
        if row is None:
            row = ChannelLaunch(id=launch_id)
            s.add(row)
        row.status = status
        row.niche = niche
        row.payload = payload


async def load_launches() -> dict[str, dict]:
    async with session() as s:
        rows = (await s.execute(select(ChannelLaunch))).scalars().all()
    return {
        r.id: {"id": r.id, "status": r.status, "niche": r.niche, **(r.payload or {})} for r in rows
    }
