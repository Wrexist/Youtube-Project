"""Persistence for the things that used to be module-level dicts.

`JOBS`, `CHANNELS`, `SCHEDULE` and `LAUNCHES` were dicts, so a restart lost every
job, channel, booking and launch — and, worst of all, the day's quota spend. The
dict shapes were kept compatible with this on purpose, so these functions are
mostly a serialise/deserialise pair rather than a redesign.

Jobs keep a **live in-process mirror** as well as their row. A running job holds
things that cannot go in a database — the `asyncio.Event` subscribers wait on,
the `asyncio.Task`, an instantiated `YouTube` client — so the row is the durable
record and the mirror is the runtime handle. `load_jobs()` rebuilds the mirror on
startup so a job that was mid-render when the process died comes back as
`interrupted` rather than silently vanishing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from loguru import logger
from sqlalchemy import delete, select

from engine.db import session
from engine.tables import Channel, ChannelLaunch, Job, ScheduleSlot
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
        row.states = dump_states(job.get("states", {}))
        row.events = job.get("events", [])
        row.cost_usd = _cost_of(job)
        row.error = job.get("error", "") or ""
        row.source_job_id = job.get("inputs", {}).get("source_job_id")
        row.updated_at = datetime.now(UTC)


def _cost_of(job: dict) -> float:
    return round(
        sum(s.output.cost_usd for s in job.get("states", {}).values() if s.output),
        4,
    )


def _jsonable(inputs: dict) -> dict:
    """Inputs minus anything that is not data.

    `youtube_client` is a live client instance that a publish job carries; it is
    rebuilt from the channel row on resume, never serialised.
    """
    return {k: v for k, v in inputs.items() if _is_json_safe(v)}


def _is_json_safe(value: Any) -> bool:
    if isinstance(value, str | int | float | bool | type(None)):
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False


async def load_jobs(get_workflow) -> dict[str, dict]:
    """Rebuild the in-process job mirror from rows. Called once at startup.

    A job whose row says "running" cannot actually be running — this process just
    started — so it is marked `interrupted`. That is honest, and it is what lets
    the Queue screen offer a resume instead of showing a spinner forever.
    """
    import asyncio

    mirror: dict[str, dict] = {}
    async with session() as s:
        rows = (await s.execute(select(Job).order_by(Job.created_at))).scalars().all()

    interrupted = 0
    for row in rows:
        try:
            workflow = get_workflow(row.workflow)
        except KeyError:
            logger.warning("job {} ran unknown workflow {!r}; skipping", row.id, row.workflow)
            continue

        status = row.status
        if status == "running":
            status = "interrupted"
            interrupted += 1

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

        mirror[row.id] = {
            "id": row.id,
            "workflow": workflow,
            "inputs": row.inputs or {},
            "states": states,
            "events": row.events or [],
            "wake": asyncio.Event(),
            "status": status,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    if interrupted:
        logger.warning("{} job(s) were mid-run at shutdown; marked interrupted", interrupted)
    logger.info("restored {} job(s)", len(mirror))
    return mirror


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
        row.access_token = creds.access_token
        row.expires_at = creds.expires_at


async def load_channels() -> dict:
    from engine.providers.youtube import Credentials

    async with session() as s:
        rows = (await s.execute(select(Channel))).scalars().all()

    out = {}
    for row in rows:
        out[row.key] = Credentials(
            refresh_token_encrypted=row.refresh_token_encrypted,
            access_token=row.access_token,
            expires_at=row.expires_at,
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
