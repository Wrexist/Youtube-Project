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
from typing import Any

from loguru import logger
from sqlalchemy import delete, select

from engine.db import session
from engine.tables import Channel, ChannelLaunch, Job, ScheduleSlot
from engine.workflows.base import StageState, StageStatus

# ── stage state (de)serialisation ───────────────────────────────────────────


def dump_states(states: dict[str, StageState]) -> dict:
    """Stage states as JSON.

    Stage *values* are not stored — a `Voiceover`, a `KeywordEvidence` or a list
    of `TitleVariant` are Python objects, and pickling them into a column would
    make every dataclass change a migration. What is stored is enough to redraw
    the pipeline and to know what still has to run; a resumed job replays from
    the last completed stage rather than reconstructing its outputs.
    """
    return {
        name: {
            "status": state.status.value,
            "error": state.error,
            "attempts": state.attempts,
            "elapsed_ms": state.elapsed_ms,
            "cost_usd": state.output.cost_usd if state.output else 0.0,
            "summary": _summary(state),
        }
        for name, state in states.items()
    }


def _summary(state: StageState) -> str:
    if state.output is None:
        return ""
    value = state.output.value
    try:
        return value.summary() if hasattr(value, "summary") else ""
    except Exception:  # noqa: BLE001 — a summary is cosmetic, never fail a save for it
        return ""


def load_states(raw: dict, template: dict[str, StageState]) -> dict[str, StageState]:
    """Rebuild stage states onto a fresh workflow's state map.

    `template` comes from `Workflow.initial_states()`, so a stage added since the
    row was written starts PENDING instead of raising a KeyError — a deploy in
    the middle of a long render should not orphan the job.
    """
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

        # `elapsed_ms` is derived from `time.monotonic()`, which is meaningless
        # across processes — there is no clock to restore it against. The stored
        # duration is reconstructed as a monotonic *interval* so the Queue screen
        # still shows how long the stage took; if the stage re-runs, `_run_stage`
        # overwrites both ends anyway.
        elapsed = stored.get("elapsed_ms") or 0
        if elapsed:
            state.started_at = 0.0
            state.finished_at = elapsed / 1000
    return template


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

        mirror[row.id] = {
            "id": row.id,
            "workflow": workflow,
            "inputs": row.inputs or {},
            "states": load_states(row.states or {}, workflow.initial_states()),
            "events": row.events or [],
            "wake": asyncio.Event(),
            "status": status,
            "error": row.error,
        }

    if interrupted:
        logger.warning("{} job(s) were mid-run at shutdown; marked interrupted", interrupted)
    logger.info("restored {} job(s)", len(mirror))
    return mirror


# ── channels ────────────────────────────────────────────────────────────────


async def save_channel(key: str, creds) -> None:
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
    async with session() as s:
        row = await s.get(ScheduleSlot, video_id)
        if row is None:
            row = ScheduleSlot(video_id=video_id, at=at)
            s.add(row)
        row.at = at
        if job_id:
            row.job_id = job_id


async def delete_slot(video_id: str) -> None:
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
