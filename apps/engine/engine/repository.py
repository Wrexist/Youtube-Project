"""Persistence for the things that used to be module-level dicts.

`JOBS`, `CHANNELS` and `SCHEDULE` were dicts, so a restart lost every job, channel
and booking — and, worst of all, the day's quota spend. The dict shapes were kept
compatible with this on purpose, so these functions are mostly a serialise/
deserialise pair rather than a redesign.

`LAUNCHES` was the long-standing exception — `save_launch`/`load_launches` existed
with no application caller, and the loader returned a flattened dict that could not
be assigned into the mirror shape `api/channels.py` reads. Both halves are fixed:
the loader hands back the payload unflattened and `api/channels.py` saves after
every stage and restores at startup, so a launch now survives a restart like
everything else here.

Jobs keep a **live in-process mirror** as well as their row. A running job holds
things that cannot go in a database — the `asyncio.Event` subscribers wait on,
the `asyncio.Task`, an instantiated `YouTube` client — so the row is the durable
record and the mirror is the runtime handle. `load_jobs()` rebuilds the mirror on
startup so a job that was mid-render when the process died comes back as
`interrupted` rather than silently vanishing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from engine.db import session
from engine.insights import VideoRecord

# Safe at module level: `repurpose.rights` imports only the standard library, so
# there is no cycle back through here.
from engine.repurpose.rights import Grant, Lane

if TYPE_CHECKING:  # `review` imports this module at call time; keep the cycle unrun.
    from engine.review import Snapshot
from engine.tables import (
    BacklogIdea,
    Channel,
    ChannelLaunch,
    ClipAsset,
    ClipGrant,
    ClipSource,
    Job,
    KeywordSnapshot,
    PerformanceRecord,
    RepurposeProject,
    ReviewSnapshot,
    ScheduleSlot,
    Series,
    ThumbnailSwap,
    TikTokAccount,
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


async def get_keyword_snapshot(seed: str) -> list[str]:
    """The autocomplete terms seen for `seed` last time trend monitoring polled it.

    `[]` covers three different situations on purpose — persistence disabled, no
    seed, first-ever poll — because `engine.trending.rising_autocomplete_terms`
    treats all three the same way: nothing to diff against, so today's terms are
    all "new".
    """
    if not _persistence_enabled() or not seed:
        return []
    async with session() as db:
        row = (
            await db.execute(select(KeywordSnapshot).where(KeywordSnapshot.seed == seed))
        ).scalar_one_or_none()
        return list(row.terms) if row else []


async def save_keyword_snapshot(seed: str, terms: list[str]) -> None:
    """Overwrite what `seed` last saw. One row per seed — see `KeywordSnapshot`."""
    if not _persistence_enabled() or not seed:
        return
    async with session() as db:
        row = (
            await db.execute(select(KeywordSnapshot).where(KeywordSnapshot.seed == seed))
        ).scalar_one_or_none()
        if row:
            row.terms = terms
            row.captured_at = datetime.now(UTC)
        else:
            db.add(KeywordSnapshot(seed=seed, terms=terms))


async def job_id_for_video(video_id: str) -> str | None:
    """The job that produced `video_id`, so a later feature can re-read its stage
    output — `thumbnail_ab.sweep` uses this to find the render's thumbnail
    variants. `PerformanceRecord.job_id` is a real column precisely so this does
    not need to round-trip through `payload`, which never carried it."""
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = await db.get(PerformanceRecord, video_id)
        return row.job_id if row else None


async def record_thumbnail_swap(
    *,
    video_id: str,
    from_concept: str,
    to_concept: str,
    variant_key: str,
    reason: str,
    at: datetime | None = None,
) -> None:
    """Log one thumbnail A/B swap. Every swap is kept — see `ThumbnailSwap`."""
    if not _persistence_enabled():
        return
    async with session() as db:
        db.add(
            ThumbnailSwap(
                video_id=video_id,
                from_concept=from_concept,
                to_concept=to_concept,
                variant_key=variant_key,
                reason=reason,
                at=at or datetime.now(UTC),
            )
        )


async def last_thumbnail_swap(video_id: str) -> ThumbnailSwap | None:
    """The most recent swap on this video, or `None` if it has never been
    swapped — what `thumbnail_ab.should_swap`'s 14-day guardrail checks against."""
    if not _persistence_enabled():
        return None
    async with session() as db:
        return (
            await db.execute(
                select(ThumbnailSwap)
                .where(ThumbnailSwap.video_id == video_id)
                .order_by(ThumbnailSwap.at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def thumbnail_swaps_for(video_id: str) -> list[ThumbnailSwap]:
    """Every swap on this video, oldest first — the full history `thumbnail_ab.
    pick_next_variant` uses to avoid re-trying a concept already tried, and what
    Phase 8 attribution reads to segment CTR at each swap date."""
    if not _persistence_enabled():
        return []
    async with session() as db:
        rows = (
            (
                await db.execute(
                    select(ThumbnailSwap)
                    .where(ThumbnailSwap.video_id == video_id)
                    .order_by(ThumbnailSwap.at)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


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
# Wired: `api/channels.py` saves after every stage and the lifespan handler calls
# `api.channels.restore()` at startup, so a launch now survives a restart. The
# payload is the serialised half of the in-process mirror — `states` (via
# `dump_states`), `events` and `inputs` — and `load_launches` hands it back
# *unflattened* so the caller can rebuild `states` onto a fresh workflow template
# with `load_states`. The old loader spread the payload into the top level, which
# is exactly the shape mismatch that kept this section unwired for so long.


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
        r.id: {"id": r.id, "status": r.status, "niche": r.niche, "payload": r.payload or {}}
        for r in rows
    }


# ── series ──────────────────────────────────────────────────────────────────


def _series_dict(row: Series) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "niche": row.niche,
        "monthly_budget_usd": row.monthly_budget_usd,
        "shorts_per_week": row.shorts_per_week,
        "long_per_week": row.long_per_week,
        "auto_publish": row.auto_publish,
        "paused": row.paused,
        "created_at": _aware(row.created_at),
    }


async def save_series(data: dict) -> None:
    """Upsert one series config. `data` carries the `_series_dict` keys."""
    if not _persistence_enabled():
        return
    async with session() as s:
        row = await s.get(Series, data["id"])
        if row is None:
            row = Series(id=data["id"])
            s.add(row)
        row.name = data["name"]
        row.niche = data.get("niche", "")
        row.monthly_budget_usd = float(data.get("monthly_budget_usd", 0.0))
        row.shorts_per_week = int(data.get("shorts_per_week", 3))
        row.long_per_week = int(data.get("long_per_week", 1))
        row.auto_publish = bool(data.get("auto_publish", False))
        row.paused = bool(data.get("paused", False))


async def list_series() -> list[dict]:
    if not _persistence_enabled():
        return []
    async with session() as s:
        rows = (await s.execute(select(Series).order_by(Series.created_at))).scalars().all()
    return [_series_dict(r) for r in rows]


async def get_series(series_id: str) -> dict | None:
    if not _persistence_enabled():
        return None
    async with session() as s:
        row = await s.get(Series, series_id)
    return _series_dict(row) if row else None


async def delete_series(series_id: str) -> bool:
    if not _persistence_enabled():
        return False
    async with session() as s:
        row = await s.get(Series, series_id)
        if row is None:
            return False
        await s.delete(row)
        return True


async def series_usage() -> dict[str, dict]:
    """Per-series spend and output, read off the jobs table.

    The same reasoning as `spend_by_day`: `automation.SpendLedger` is in-memory
    and written by nothing, while `jobs.cost_usd` is written at the same stage
    boundary that spends the money. A job belongs to a series when its inputs
    carry `series_id`, which `POST /v1/jobs` accepts and stores.

    Returns `{series_id: {spent_today, spent_this_month, produced_this_week}}`.
    Weeks start Monday UTC, matching the weekly review's cadence; months are
    calendar months, matching `SpendLedger.spent_this_month`.
    """
    if not _persistence_enabled():
        return {}
    now = datetime.now(UTC)
    since = now - timedelta(days=45)
    async with session() as db:
        rows = (
            await db.execute(
                select(Job.created_at, Job.cost_usd, Job.inputs, Job.status).where(
                    Job.created_at >= since
                )
            )
        ).all()

    week_start = (now - timedelta(days=now.weekday())).date()
    out: dict[str, dict] = {}
    for created_at, cost, inputs, status in rows:
        series_id = (inputs or {}).get("series_id")
        if not series_id:
            continue
        moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        usage = out.setdefault(
            series_id, {"spent_today": 0.0, "spent_this_month": 0.0, "produced_this_week": 0}
        )
        if moment.date() == now.date():
            usage["spent_today"] += cost or 0.0
        if (moment.year, moment.month) == (now.year, now.month):
            usage["spent_this_month"] += cost or 0.0
        if moment.date() >= week_start and status in ("completed", "running", "queued"):
            usage["produced_this_week"] += 1

    for usage in out.values():
        usage["spent_today"] = round(usage["spent_today"], 4)
        usage["spent_this_month"] = round(usage["spent_this_month"], 4)
    return out


# ── repurpose: clips, grants, assets ────────────────────────────────────────
#
# The split enforced here is the one from `engine/repurpose/rights.py`: metadata
# about a public post is free to keep, media is not. `record_asset` refuses to
# write without a live grant, so the invariant is a property of the persistence
# layer rather than a rule the acquire stage is trusted to remember.


def _grant_from_row(row: ClipGrant) -> Grant:
    """A stored grant as the rights module sees it."""
    return Grant(
        lane=Lane(row.lane),
        grantor=row.grantor,
        evidence_kind=row.evidence_kind,
        evidence_ref=row.evidence_ref,
        granted_at=row.granted_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        platforms=frozenset(row.platforms or ()),
        rules=row.rules,
    )


async def upsert_clip_sources(sources: list[dict], *, channel_key: str = "") -> int:
    """Record discovered clips. Returns how many were new.

    **Measurements refresh; decisions do not.** A known clip has its `stats` and
    `fit_score` brought up to date, and nothing else. That split is the whole rule:

      * Refreshing everything resets `status`, resurrecting every clip the operator
        already dismissed — the mistake `add_backlog_ideas` avoids for the same
        reason. A dismissal is a decision and re-running discovery is not new
        information about it.
      * Refreshing nothing freezes each clip's view count at the moment it was
        first seen, and `fit.score_clip` weights reach. The clip that took off
        *after* discovery first noticed it is the single best repurposing
        candidate there is, and a sweep that can never re-rank it would leave it
        buried under whatever was popular a month ago, for ever.

    Per-row inside a savepoint: the pre-query is a read, so two sweeps running at
    once can both pass it and collide on `(platform, external_id)`. Losing one clip
    is fine; a 500 on the whole sweep is not.
    """
    if not _persistence_enabled() or not sources:
        return 0

    unique: dict[tuple[str, str], dict] = {}
    for source in sources:
        platform = str(source.get("platform") or "tiktok")
        external = str(source.get("external_id") or "").strip()
        if external:
            unique.setdefault((platform, external), source)

    added = 0
    async with session() as db:
        known = {
            (row.platform, row.external_id): row
            for row in (
                await db.execute(
                    select(ClipSource).where(ClipSource.external_id.in_([e for _, e in unique]))
                )
            )
            .scalars()
            .all()
        }
        for (platform, external), source in unique.items():
            row = known.get((platform, external))
            if row is not None:
                _refresh_clip_measurements(row, source)
                continue
            try:
                async with db.begin_nested():
                    db.add(
                        ClipSource(
                            id=uuid.uuid4().hex[:12],
                            platform=platform,
                            external_id=external,
                            url=str(source.get("url") or ""),
                            creator_handle=str(source.get("creator_handle") or ""),
                            caption=str(source.get("caption") or ""),
                            hashtags=list(source.get("hashtags") or []),
                            sound_id=str(source.get("sound_id") or ""),
                            stats=dict(source.get("stats") or {}),
                            region=str(source.get("region") or ""),
                            duration_s=float(source.get("duration_s") or 0.0),
                            fit_score=float(source.get("fit_score") or 0.0),
                            fit_reasons=list(source.get("fit_reasons") or []),
                            channel_key=channel_key,
                        )
                    )
                added += 1
            except IntegrityError:
                logger.debug("clip {}:{} was discovered concurrently", platform, external)
    return added


def _refresh_clip_measurements(row: ClipSource, source: dict) -> None:
    """Bring a known clip's numbers up to date, and touch nothing else.

    Explicitly field-by-field rather than a loop over `source`: the danger here is
    writing a field that carries a decision, and an allowlist of three is the only
    version of this that stays safe when someone adds a column later.

    `fit_score` is only written when the sweep actually computed one — a discovery
    pass that could not reach the keyword provider scores zero, and letting that
    overwrite a real score would push good clips off the front of the grid.
    """
    stats = source.get("stats")
    if isinstance(stats, dict) and stats:
        row.stats = dict(stats)

    score = float(source.get("fit_score") or 0.0)
    if score > 0:
        row.fit_score = score
        row.fit_reasons = list(source.get("fit_reasons") or [])


async def clip_sources(
    *, channel_key: str = "", status: str = "discovered", limit: int = 50
) -> list[dict]:
    """Discovered clips for a channel, best fit first, each with its grant if any.

    The grant travels with the clip because the card cannot be drawn without it:
    the rights chip is the one thing that decides whether the clip is usable, and
    a second round trip per card to find out would make the grid useless.
    """
    if not _persistence_enabled():
        return []
    async with session() as db:
        query = select(ClipSource).where(ClipSource.status == status)
        if channel_key:
            query = query.where(ClipSource.channel_key == channel_key)
        rows = (
            (await db.execute(query.order_by(ClipSource.fit_score.desc()).limit(limit)))
            .scalars()
            .all()
        )
        # Ascending, so the *last* row written for a source is the one left standing
        # in the dict — the same rule `grants_for` and `latest_grant` follow.
        #
        # This used to order descending, which inverted it: the dict comprehension
        # keeps whatever it sees last, so the oldest grant won. Grants append rather
        # than replace, so that is precisely the superseded one. A revoked clip came
        # back from here with `cleared: True` while `record_asset` refused its media
        # — and `api.clips` re-reads the grant itself through `latest_grant`, so the
        # card contradicted itself: a fatal "revoked" problem next to a green chip.
        grants = {
            g.source_id: g
            for g in (
                await db.execute(
                    select(ClipGrant)
                    .where(ClipGrant.source_id.in_([r.id for r in rows]))
                    .order_by(ClipGrant.created_at, ClipGrant.id)
                )
            )
            .scalars()
            .all()
        }
        assets = {
            a.source_id
            for a in (
                await db.execute(
                    select(ClipAsset).where(ClipAsset.source_id.in_([r.id for r in rows]))
                )
            )
            .scalars()
            .all()
        }

    out = []
    for row in rows:
        grant_row = grants.get(row.id)
        grant = _grant_from_row(grant_row) if grant_row else None
        out.append(
            {
                "id": row.id,
                "platform": row.platform,
                "external_id": row.external_id,
                "url": row.url,
                "creator_handle": row.creator_handle,
                "caption": row.caption,
                "hashtags": row.hashtags or [],
                "stats": row.stats or {},
                "duration_s": row.duration_s,
                "fit_score": row.fit_score,
                "fit_reasons": row.fit_reasons or [],
                "status": row.status,
                "grant": grant.as_dict() if grant else None,
                "cleared": bool(grant and grant.cleared()),
                "acquired": row.id in assets,
            }
        )
    return out


async def set_clip_status(source_id: str, status: str) -> bool:
    """Select or dismiss a clip. Rows are kept, never deleted — a dismissal is a
    fact worth remembering, and the next sweep would otherwise re-propose it."""
    if not _persistence_enabled():
        return False
    async with session() as db:
        row = await db.get(ClipSource, source_id)
        if row is None:
            return False
        row.status = status
    return True


async def record_grant(source_id: str, grant: Grant) -> int | None:
    """Store authority to use a clip. Returns the grant id.

    Appends rather than replaces. A superseded grant is history — it is what
    answers "were we allowed to publish that, at the time we published it", and
    an update would erase exactly that.
    """
    if not _persistence_enabled():
        return None
    async with session() as db:
        if await db.get(ClipSource, source_id) is None:
            raise KeyError(f"no clip source {source_id!r}")
        row = ClipGrant(
            source_id=source_id,
            lane=grant.lane.value,
            grantor=grant.grantor,
            evidence_kind=grant.evidence_kind,
            evidence_ref=grant.evidence_ref,
            granted_at=grant.granted_at or datetime.now(UTC),
            expires_at=grant.expires_at,
            revoked_at=grant.revoked_at,
            platforms=sorted(grant.platforms),
            rules=grant.rules,
        )
        db.add(row)
        await db.flush()
        return row.id


async def latest_grant(source_id: str) -> Grant | None:
    """The current grant for a clip, or None if it has never had one."""
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = (
            (
                await db.execute(
                    select(ClipGrant)
                    .where(ClipGrant.source_id == source_id)
                    .order_by(ClipGrant.created_at.desc(), ClipGrant.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    return _grant_from_row(row) if row else None


async def grants_for(source_ids: list[str]) -> dict[str, Grant]:
    """Current grants for several clips at once — what the gate needs."""
    if not _persistence_enabled() or not source_ids:
        return {}
    async with session() as db:
        rows = (
            (
                await db.execute(
                    select(ClipGrant)
                    .where(ClipGrant.source_id.in_(source_ids))
                    .order_by(ClipGrant.created_at, ClipGrant.id)
                )
            )
            .scalars()
            .all()
        )
    # Later rows win: the query is ascending, so the last write for each source is
    # the one left standing.
    return {row.source_id: _grant_from_row(row) for row in rows}


async def record_asset(source_id: str, asset: dict) -> int:
    """Store media for a cleared clip.

    **Refuses without a live grant.** This is the enforcement point for the rule in
    `repurpose/rights.py` — putting it here rather than only in the acquire stage
    means a future caller that forgets cannot quietly create the situation the
    whole rights model exists to prevent: a directory of other people's video with
    no record of why any of it is there.
    """
    grant = await latest_grant(source_id)
    if grant is None:
        raise PermissionError(
            f"clip {source_id!r} has no grant — media cannot be stored for it. "
            "Record how this clip may be used first."
        )
    if not grant.permits_acquisition():
        raise PermissionError(
            f"the {grant.lane.value} grant on clip {source_id!r} is no longer live "
            "(expired or revoked), so its media must not be fetched or kept."
        )

    async with session() as db:
        row = ClipAsset(
            source_id=source_id,
            storage_key=str(asset.get("storage_key") or ""),
            sha256=str(asset.get("sha256") or ""),
            duration_s=float(asset.get("duration_s") or 0.0),
            width=int(asset.get("width") or 0),
            height=int(asset.get("height") or 0),
            has_watermark=bool(asset.get("has_watermark")),
            watermark_regions=list(asset.get("watermark_regions") or []),
        )
        db.add(row)
        await db.flush()
        return row.id


async def save_project(
    project_id: str,
    *,
    channel_key: str = "",
    thesis: str = "",
    segments: list | None = None,
    job_id: str | None = None,
    report: dict | None = None,
) -> None:
    """Create or update an episode.

    `report` is stored verbatim rather than recomputed. It carries the threshold
    version that judged the video, and "what did we check, and when" is the
    question a channel review asks — one that cannot be answered after the fact if
    the thresholds have since moved.
    """
    if not _persistence_enabled():
        return
    async with session() as db:
        row = await db.get(RepurposeProject, project_id)
        if row is None:
            row = RepurposeProject(id=project_id)
            db.add(row)
        row.channel_key = channel_key or row.channel_key
        row.thesis = thesis or row.thesis
        if segments is not None:
            row.segments = segments
        if job_id is not None:
            row.job_id = job_id
        if report is not None:
            row.report = report


async def load_project(project_id: str) -> dict | None:
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = await db.get(RepurposeProject, project_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "channel_key": row.channel_key,
            "thesis": row.thesis,
            "segments": row.segments or [],
            "job_id": row.job_id,
            "report": row.report,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ── TikTok accounts (Lane A) ────────────────────────────────────────────────
#
# The refresh token is encrypted at rest, like YouTube's. `access_token` is not:
# it lives 24 hours, it is replaced on every refresh, and encrypting a value that
# short-lived buys nothing while making the "is it still valid" check a decrypt.


async def save_tiktok_account(
    tokens,
    *,
    key: str = "default",
    handle: str = "",
) -> None:
    """Store or replace the connection for an account.

    Upsert on `key` rather than insert: reconnecting is the normal way to recover
    from an expired refresh token, and a second row would leave `load_tiktok_tokens`
    picking between two credentials with no way to know which is live.
    """
    if not _persistence_enabled():
        return

    from engine.crypto import encrypt

    async with session() as db:
        row = await db.get(TikTokAccount, key)
        if row is None:
            row = TikTokAccount(key=key, refresh_token_encrypted="")
            db.add(row)
        row.open_id = tokens.open_id or row.open_id
        row.handle = handle or row.handle
        if tokens.refresh_token:
            row.refresh_token_encrypted = encrypt(tokens.refresh_token)
        row.access_token = tokens.access_token
        row.expires_at = tokens.expires_at
        row.refresh_expires_at = tokens.refresh_expires_at
        row.scope = tokens.scope or row.scope


async def load_tiktok_account(key: str = "default") -> dict | None:
    """The stored connection, without decrypting anything.

    Deliberately does not return the refresh token: this is what the Setup screen
    reads to say whether an account is connected, and a status endpoint has no
    business handling a credential. `tiktok_access_token` is the one path that
    decrypts, and it is called by the sweep.
    """
    if not _persistence_enabled():
        return None
    async with session() as db:
        row = await db.get(TikTokAccount, key)
        if row is None:
            return None
        return {
            "key": row.key,
            "open_id": row.open_id,
            "handle": row.handle,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "refresh_expires_at": (
                row.refresh_expires_at.isoformat() if row.refresh_expires_at else None
            ),
            "scope": row.scope,
            "connected": bool(row.refresh_token_encrypted),
        }


async def tiktok_access_token(key: str = "default") -> str:
    """A *live* access token, refreshing it first if it is close to expiring.

    This is the function every read path should call. Callers must not cache what
    it returns beyond the request they need it for: the whole point is that the
    token in the database is 24 hours from useless at all times, and a cached one
    is a sweep that fails tomorrow for a reason nobody can see today.

    **Refreshes are serialised.** TikTok rotates the refresh token on refresh, so
    two callers refreshing at once — which is the ordinary shape of this system,
    with the worker sweeping on a schedule while someone presses Discover — both
    spend the same stored token, and the second spends one TikTok has already
    retired. The loser then writes its failure over the winner's good token and
    the connection is dead until a human reconnects. The lock plus the re-read
    inside it means the second caller finds the fresh token and makes no call at
    all.

    Raises `TikTokAuthExpired` when only a human can fix it — no stored account, a
    refresh token past its own expiry, or a refresh TikTok refused.
    """
    from engine.providers import tiktok

    if not _persistence_enabled():
        raise tiktok.TikTokAuthExpired("persistence is off, so no TikTok account is stored")

    live = await _stored_tiktok_token(key)
    if live is not None:
        return live

    async with _tiktok_refresh_lock():
        # Re-read: whoever held the lock has very likely just refreshed, and this
        # is the check that turns a stampede into one call rather than N.
        live = await _stored_tiktok_token(key)
        if live is not None:
            return live

        refresh_token = await _tiktok_refresh_token(key)
        tokens = await tiktok.refresh(refresh_token)
        await save_tiktok_account(tokens, key=key)
        return tokens.access_token


async def _stored_tiktok_token(key: str) -> str | None:
    """The stored access token if it is comfortably live, else None.

    None means "needs refreshing", not "broken" — the two cases that are broken
    raise instead, because no amount of refreshing fixes a missing account.
    """
    from engine.providers import tiktok

    async with session() as db:
        row = await db.get(TikTokAccount, key)
        if row is None or not row.refresh_token_encrypted:
            raise tiktok.TikTokAuthExpired("no TikTok account connected")

        # SQLite drops the timezone (see `repurpose/rights._aware` for the same
        # trap and why it only bites outside CI).
        expires_at = _aware_utc(row.expires_at)
        refresh_expires_at = _aware_utc(row.refresh_expires_at)
        now = datetime.now(UTC)

        # Checked here rather than left to TikTok: a refresh token past its own
        # expiry cannot be refreshed, so calling anyway spends a round trip to be
        # told what the row already said. Refresh tokens last about a year, so
        # this fires on an install nobody has swept in a long time.
        if refresh_expires_at is not None and now >= refresh_expires_at:
            raise tiktok.TikTokAuthExpired(
                "the TikTok connection has expired — reconnect the account"
            )

        if row.access_token and expires_at and now < expires_at - _REFRESH_MARGIN:
            return row.access_token
        return None


async def _tiktok_refresh_token(key: str) -> str:
    from engine.crypto import DecryptionFailed, decrypt
    from engine.providers import tiktok

    async with session() as db:
        row = await db.get(TikTokAccount, key)
        if row is None or not row.refresh_token_encrypted:
            raise tiktok.TikTokAuthExpired("no TikTok account connected")
        try:
            return decrypt(row.refresh_token_encrypted)
        except DecryptionFailed as exc:
            # The secret key changed. The stored token is unrecoverable and the
            # only fix is reconnecting, so say that rather than reporting a
            # decrypt error nobody can act on.
            raise tiktok.TikTokAuthExpired(
                "the stored TikTok token cannot be decrypted — reconnect the account"
            ) from exc


def _aware_utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


_refresh_lock: asyncio.Lock | None = None
_refresh_lock_loop: Any = None


def _tiktok_refresh_lock() -> asyncio.Lock:
    """The refresh lock for the running loop.

    Rebound when the loop changes rather than created at import, for the reason
    `quota.Ledger._serialised` sets out at length: an `asyncio.Lock` binds to the
    first loop that awaits it, and the test suite and the CLI both call in through
    separate `asyncio.run`s. Serialising within one loop is what is needed; two
    loops racing over one database row is not a shape this system has.
    """
    global _refresh_lock, _refresh_lock_loop

    loop = asyncio.get_running_loop()
    if _refresh_lock is None or _refresh_lock_loop is not loop:
        _refresh_lock, _refresh_lock_loop = asyncio.Lock(), loop
    return _refresh_lock


#: Mirrors `tiktok.REFRESH_MARGIN`. Imported lazily there, restated here so this
#: module does not import the provider at module scope.
_REFRESH_MARGIN = timedelta(minutes=5)


async def disconnect_tiktok(key: str = "default") -> bool:
    """Forget an account. True if there was one."""
    if not _persistence_enabled():
        return False
    async with session() as db:
        row = await db.get(TikTokAccount, key)
        if row is None:
            return False
        await db.delete(row)
    return True
