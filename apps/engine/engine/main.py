"""HTTP surface.

Two things matter here: jobs stream their progress, and a job survives the browser
going away. Everything else is CRUD.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from engine import automation, db, repository, worker
from engine.api import publishing as channels
from engine.api.channels import router as channels_router
from engine.api.insights import router as insights_router
from engine.api.models import router as models_router
from engine.api.publishing import router as publishing_router
from engine.providers import youtube
from engine.quota import ledger
from engine.settings import get_settings
from engine.storage import store
from engine.workflows import video
from engine.workflows.base import StageStatus, WorkflowError


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Restore state, then release the pool on the way out.

    Everything below used to live in module-level dicts, so a restart forgot every
    job, channel, booking — and the day's quota spend, which then silently
    overran Google's ceiling on the next upload. `STUDIO_PERSIST=false` skips it
    for tests and for anyone who genuinely wants a scratch instance.
    """
    if get_settings().persist:
        try:
            logger.info("database: {}", await db.ensure_schema())
            await ledger.load()
            JOBS.update(await repository.load_jobs(video.get))
            channels.CHANNELS.update(await repository.load_channels())
            channels.SCHEDULE.update(await repository.load_schedule())
        except Exception:
            # A missing migration must not look like an empty database — starting
            # with a blank quota ledger is exactly how the ceiling gets overrun.
            logger.exception("failed to restore state; refusing to start")
            raise
    else:
        logger.warning("STUDIO_PERSIST=false — state is in-process only and dies with it")
        ledger.persist = False

    yield

    if get_settings().persist:
        await db.dispose()


app = FastAPI(title="Studio Engine", version="0.1.0", lifespan=lifespan)
app.include_router(publishing_router)
app.include_router(insights_router)
app.include_router(channels_router)
app.include_router(models_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    format: str = Field(default="short", pattern="^(short|long)$")
    aspect: str = Field(default="9:16", pattern=r"^(9:16|16:9|1:1)$")
    workflow: str = "video"
    voice: str | None = None
    target_seconds: int | None = None


class EditRequest(BaseModel):
    stage: str
    value: Any


class PublishRequest(BaseModel):
    """Choices the operator makes at the approval gate.

    All optional: the defaults publish the top-scored title and first thumbnail
    immediately and publicly, which is the common case.
    """

    chosen_title_index: int = Field(default=0, ge=0)
    chosen_thumbnail_index: int = Field(default=0, ge=0)
    privacy: str = Field(default="public", pattern="^(public|unlisted|private)$")
    publish_at: datetime | None = None
    playlist_id: str | None = None
    made_for_kids: bool = False


# Live mirror of the `jobs` table, hydrated by the lifespan handler and written
# through on every stage boundary. It holds what a row cannot: the asyncio.Event
# subscribers wait on, the running Task, and a publish job's YouTube client.
JOBS: dict[str, dict[str, Any]] = {}


@app.get("/health")
async def health() -> dict:
    # `llm_model` used to come from Settings, which nothing routed on — so this
    # reported a model the engine would never call. Report what will actually run.
    from engine.models import routing

    return {
        "ok": True,
        "env": get_settings().env,
        "models": {
            "draft": routing.spec_for("draft").model,
            "tags": routing.spec_for("tags").model,
        },
        "workflows": sorted(video.WORKFLOWS),
    }


@app.get("/v1/workflows/{name}")
async def describe_workflow(name: str) -> dict:
    """The stage graph, so the UI can render the pipeline before anything runs."""
    try:
        wf = video.get(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "name": wf.name,
        "stages": [
            {
                "name": s.name,
                "title": s.title,
                "depends_on": list(s.depends_on),
                "optional": s.optional,
                "estimated_cost_usd": s.estimated_cost_usd,
            }
            for s in wf.stages
        ],
    }


@app.post("/v1/jobs", status_code=202)
async def create_job(body: JobRequest) -> dict:
    try:
        wf = video.get(body.workflow)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    job_id = uuid.uuid4().hex[:12]
    wake = asyncio.Event()
    JOBS[job_id] = {
        "id": job_id,
        "workflow": wf,
        "inputs": body.model_dump(),
        "states": wf.initial_states(),
        "wake": wake,
        "events": [],
        "status": "running",
        # Carried on the mirror as well as the row: `GET /v1/jobs` sorts on it, and
        # reading it back from the database on every list would make a screen that
        # polls hit Postgres for something the process already knows.
        "created_at": datetime.now(UTC),
    }

    await _persist(JOBS[job_id])
    await _dispatch(job_id)
    return {"job_id": job_id, "status": "running"}


async def _dispatch(job_id: str, start_from: str | None = None) -> None:
    """Hand the job to a worker, or run it here if there is no worker to hand it to.

    The in-process path is not a fallback that shipped by accident — `uvicorn` on
    its own, with no Redis and no worker, is a supported way to run this. It is
    just the one where a render dies with the web process.
    """
    if await worker.enqueue(job_id, start_from):
        # The work happens elsewhere; this process only relays the worker's events
        # to any browser watching.
        JOBS[job_id]["task"] = asyncio.create_task(_relay(job_id))
    else:
        # Deliberately not tied to the request: closing the tab must not cancel a render.
        JOBS[job_id]["task"] = asyncio.create_task(_run_job(job_id, start_from))


async def _relay(job_id: str) -> None:
    """Mirror a worker's events into this process's log so SSE works unchanged.

    Subscribers read `job["events"]` by cursor and know nothing about where the
    work runs. Appending here keeps that true for both execution paths.
    """
    import json

    from arq.connections import create_pool

    job = JOBS[job_id]
    pool = None
    try:
        pool = await create_pool(worker.redis_settings())
        pubsub = pool.pubsub()
        await pubsub.subscribe(worker.CHANNEL.format(job_id))

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue  # the subscribe confirmation, not an event
            event = json.loads(message["data"])
            if event.get("type") == "__done__":
                break
            job["events"].append(event)
            _wake(job)

        await pubsub.unsubscribe()
        await pubsub.aclose()
    except Exception:  # noqa: BLE001
        logger.exception("relay for job {} stopped", job_id)
    finally:
        if pool is not None:
            with suppress(Exception):
                await pool.aclose()
        # The worker owns the row, so re-read it for the final status rather than
        # inferring one from the last event seen.
        with suppress(Exception):
            fresh = await repository.load_jobs(video.get)
            if job_id in fresh:
                job["status"] = fresh[job_id]["status"]
                job["states"] = fresh[job_id]["states"]
        _wake(job)


def _wake(job: dict) -> None:
    """Release every subscriber waiting on this job, then arm a fresh signal.

    Swapping the Event rather than set()/clear() closes the race where a
    subscriber has drained the log but not yet awaited: it captured the old
    Event, which this sets, so its wait returns immediately instead of hanging
    until the next event.
    """
    waiting = job["wake"]
    job["wake"] = asyncio.Event()
    waiting.set()


async def _run_job(job_id: str, start_from: str | None = None) -> None:
    job = JOBS[job_id]

    async def emit(event: dict) -> None:
        # The event log is the single source of truth. Subscribers read it by
        # cursor, so one append serves every viewer exactly once — see stream_job.
        job["events"].append(event)
        _wake(job)
        # Persist on stage boundaries rather than every progress tick: a render
        # emits hundreds of those, and the resume point only moves on a boundary.
        if event["type"].startswith(("stage.completed", "stage.failed", "workflow.")):
            await _persist(job)

    try:
        await job["workflow"].run(
            job_id=job_id,
            inputs=job["inputs"],
            emit=emit,
            states=job["states"],
            budget_usd=get_settings().max_cost_per_video_usd,
            start_from=start_from,
        )
        job["status"] = "completed"
    except WorkflowError as exc:
        job["status"] = "failed"
        logger.error("job {} failed: {}", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        logger.exception("job {} crashed", job_id)
        await emit({"type": "workflow.failed", "job_id": job_id, "error": str(exc)})
    finally:
        # Status is already final here, so a woken subscriber sees "not running"
        # and closes its stream rather than waiting for an event that never comes.
        await _persist(job)
        _wake(job)


async def _persist(job: dict) -> None:
    """Save a job, without ever letting a database problem kill the run.

    A render that completed and then failed to save is a bad outcome; a render
    that was *aborted* because the save failed is a worse one, and the work is
    already done by the time this is called.
    """
    if not get_settings().persist:
        return
    try:
        await repository.save_job(job)
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist job {}", job["id"])


class JobSummary(BaseModel):
    """One job, as the Queue and Library list them.

    A response model rather than a bare dict because both screens do arithmetic and
    filtering on these fields; without it the generated TypeScript types every one
    as `unknown` and the UI has to cast, which is what `packages/contracts` exists
    to prevent.
    """

    id: str
    status: str
    topic: str
    workflow: str
    cost_usd: float
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: Where the pipeline actually is — "4/17 · Voiceover" is the useful summary,
    #: and computing it here keeps both screens from re-deriving it differently.
    stages_done: int
    stages_total: int
    current_stage: str | None = None
    error: str | None = None
    #: Storage keys, so the Library can show a thumbnail and play the render.
    render_key: str | None = None
    thumbnail_keys: list[str] = Field(default_factory=list)


@app.get("/v1/jobs")
async def list_jobs(status: str | None = None, limit: int = 100) -> list[JobSummary]:
    """Every job, newest first.

    This did not exist, so the Queue and Library had nothing to read and rendered
    demo data permanently — generate a video and neither screen would ever change.
    They are the two screens someone looks at immediately after pressing Generate.

    `status` filters to one state; the Library asks for `completed` and the Queue
    takes everything.
    """
    out: list[JobSummary] = []
    for job_id, job in JOBS.items():
        if status and job.get("status") != status:
            continue

        states = job.get("states", {})
        done = sum(1 for s in states.values() if s.status is StageStatus.DONE)
        running = next(
            (name for name, s in states.items() if s.status is StageStatus.RUNNING), None
        )
        artifacts = {}
        for state in states.values():
            if state.output:
                artifacts.update(state.output.artifacts or {})

        out.append(
            JobSummary(
                id=job_id,
                status=job.get("status", "unknown"),
                topic=str(job.get("inputs", {}).get("topic", "")),
                workflow=job["workflow"].name,
                cost_usd=round(sum(s.output.cost_usd for s in states.values() if s.output), 4),
                created_at=job.get("created_at"),
                updated_at=job.get("updated_at"),
                stages_done=done,
                stages_total=len(states),
                current_stage=running,
                error=job.get("error") or None,
                render_key=artifacts.get("render"),
                thumbnail_keys=[
                    v for k, v in sorted(artifacts.items()) if k.startswith("thumbnail")
                ],
            )
        )

    # Newest first, sorted on a plain number rather than on the datetimes
    # themselves. Comparing a naive datetime with an aware one raises TypeError,
    # and both reach here — SQLite has no timezone type, so a restored job is naive
    # while one created in this process is aware. Missing timestamps sort last.
    def _age(job: JobSummary) -> float:
        if job.created_at is None:
            return float("-inf")
        moment = job.created_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.timestamp()

    out.sort(key=_age, reverse=True)
    return out[: max(1, min(limit, 500))]


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = _require(job_id)
    return {
        "id": job_id,
        "status": job["status"],
        "inputs": job["inputs"],
        "stages": _serialize_stages(job),
        "cost_usd": round(sum(s.output.cost_usd for s in job["states"].values() if s.output), 4),
    }


@app.get("/v1/jobs/{job_id}/events")
async def stream_job(job_id: str) -> EventSourceResponse:
    """Live progress.

    Past events are replayed first so a reload mid-render shows the full pipeline
    rather than resuming from a blank screen.

    Each subscriber holds a cursor into `job["events"]` — the log is the only
    source of truth, and reading it by position is what makes this correct for
    more than one viewer. The previous version replayed the log *and then* drained
    a shared queue that still held those same events, so everything before a
    subscriber connected arrived twice; and because a queue hands each item to
    exactly one consumer, two open tabs split the stream between them and both
    rendered an incomplete pipeline.
    """
    job = _require(job_id)

    async def generator():
        cursor = 0
        while True:
            # Captured before draining: if an event lands in the gap between the
            # drain and the await, _wake fires this Event and the wait returns.
            waiting = job["wake"]

            events = job["events"]
            while cursor < len(events):
                event = events[cursor]
                cursor += 1
                yield {"event": event["type"], "data": _json(event)}

            if job["status"] != "running":
                return

            await waiting.wait()

    return EventSourceResponse(generator())


@app.post("/v1/jobs/{job_id}/publish", status_code=202)
async def publish_job(job_id: str, body: PublishRequest) -> dict:
    """Publish a finished video. **This is the approval gate.**

    `CLAUDE.md` non-negotiable #3: nothing publishes without an explicit approval
    gate. That gate is this endpoint — it is never a stage of the `video` workflow,
    because a workflow that publishes as its last step has no gate at all.

    Auto-publish (`automation.py`) calls the same function, so manual and unattended
    publishing share one code path and one set of blockers. Skipping the checks is
    not offered: a series with `auto_publish=True` skips the *waiting*, not the
    *checks*.
    """
    source = _require(job_id)

    if source["status"] != "completed":
        raise HTTPException(409, f"job is {source['status']}; only a completed job can publish")
    if source["workflow"].name != "video":
        raise HTTPException(409, f"job ran the '{source['workflow'].name}' workflow, not 'video'")

    creds = channels.CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected — authorise one at /v1/auth/google")

    blockers = automation.publish_blockers(_video_state(job_id, source), _PUBLISH_SERIES)
    if blockers:
        raise HTTPException(
            409,
            {
                "detail": "this video is not ready to publish",
                "blockers": [{"code": b.code, "message": b.message} for b in blockers],
            },
        )

    # Refuse before the spend, not after. An upload is 1,600 of 10,000 daily units.
    if not ledger.can_afford("videos.insert"):
        raise HTTPException(
            429,
            f"daily YouTube quota exhausted — {ledger.remaining()} units left, "
            f"an upload costs {ledger.cost_of('videos.insert')}",
        )

    wf = video.get("publish")
    publish_id = uuid.uuid4().hex[:12]
    wake = asyncio.Event()

    # Seed with the source job's states so every video stage is already DONE and gets
    # replayed rather than re-run. Only the four publish stages actually execute.
    states = wf.initial_states()
    for name, state in source["states"].items():
        if name in states:
            states[name] = state

    JOBS[publish_id] = {
        "id": publish_id,
        "workflow": wf,
        "inputs": {
            **source["inputs"],
            **body.model_dump(exclude_none=True),
            "youtube_client": youtube.YouTube(creds),
            "source_job_id": job_id,
        },
        "states": states,
        "wake": wake,
        "events": [],
        "status": "running",
    }
    JOBS[publish_id]["task"] = asyncio.create_task(_run_job(publish_id))

    logger.info("publishing job {} as {}", job_id, publish_id)
    return {"job_id": publish_id, "status": "running", "source_job_id": job_id}


def _video_state(job_id: str, job: dict) -> automation.VideoState:
    """Read the approval-gate inputs out of a finished job's stage outputs."""
    states = job["states"]

    def value(name: str, default=None):
        state = states.get(name)
        return state.output.value if state is not None and state.output else default

    titles = value("titles") or []
    sources = (value("research") or {}).get("sources", []) if value("research") else []
    grounding = value("grounding")
    critique = value("critique")

    return automation.VideoState(
        id=job_id,
        series_id=job["inputs"].get("series_id", ""),
        cost_usd=sum(s.output.cost_usd for s in states.values() if s.output),
        has_sources=bool(sources),
        source_count=len(sources),
        has_thumbnail=bool(value("thumbnail")),
        has_seo=bool(value("description") and value("tags")),
        keyword_grounded=bool(grounding and getattr(grounding, "is_grounded", False)),
        render_ok=bool(value("render")),
        title=titles[0].text if titles else "",
        critique_severity=getattr(critique, "severity", 0) or 0,
    )


# One-off videos are not part of a series, so they get a permissive series record.
# The quality blockers in publish_blockers() do not depend on it — the parameter
# exists for the auto-publish path, which passes the real series.
_PUBLISH_SERIES = automation.Series(id="", name="ad-hoc", niche="", monthly_budget_usd=float("inf"))


@app.post("/v1/jobs/{job_id}/edit")
async def edit_stage(job_id: str, body: EditRequest) -> dict:
    """Accept a user edit and re-run from that point.

    This is the interaction the Create screen is built around: change the hook, and
    everything downstream regenerates while the research above it is left alone.
    """
    job = _require(job_id)
    if job["status"] == "running":
        raise HTTPException(409, "job is still running; wait or cancel first")

    try:
        invalidated = job["workflow"].mark_edited(job["states"], body.stage, body.value)
    except (KeyError, WorkflowError) as exc:
        raise HTTPException(400, str(exc)) from exc

    job["status"] = "running"
    job["task"] = asyncio.create_task(
        _run_job(job_id, start_from=invalidated[0] if invalidated else None)
    )
    return {"invalidated": invalidated, "status": "running"}


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = _require(job_id)
    task = job.get("task")
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    job["status"] = "cancelled"
    return {"status": "cancelled"}


def _require(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


def _serialize_stages(job: dict) -> list[dict]:
    from engine.workflows.base import summarize

    out = []
    for stage in job["workflow"].stages:
        state = job["states"][stage.name]
        out.append(
            {
                "name": stage.name,
                "title": stage.title,
                "status": state.status.value,
                "summary": summarize(state.output.value) if state.output else None,
                "cost_usd": round(state.output.cost_usd, 4) if state.output else 0.0,
                "elapsed_ms": state.elapsed_ms,
                "error": state.error,
                "editable": state.status is StageStatus.DONE,
            }
        )
    return out


def _json(event: dict) -> str:
    import json

    return json.dumps(event, default=str)


# ── artifacts ───────────────────────────────────────────────────────────────

#: Only what this app actually produces. An allowlist rather than `mimetypes.guess`
#: because the storage root also holds the database, the encryption key and
#: downloaded footage — none of which is a thing to hand out over HTTP.
_SERVABLE = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

#: Directories a client may read from — every prefix this app actually writes,
#: and no others. `store` already refuses a key that escapes the storage root, but
#: "inside the root" also covers studio.db, .secret_key and `materials/`, which
#: holds third-party stock footage that is not ours to re-serve.
#:
#: Kept honest by `test_every_written_prefix_is_either_servable_or_deliberately_not`:
#: guessing these ("subtitles/", "audio/") silently 404'd the real `captions/` and
#: `voiceover/` output, which is a dead link rather than an error anyone would see.
_SERVABLE_ROOTS = ("thumbnails/", "renders/", "captions/", "voiceover/")


@app.get("/v1/files/{key:path}")
async def get_file(key: str):
    """Serve a generated artifact.

    `ObjectStore.url()` has always pointed here and this route did not exist, so
    nothing could show a thumbnail or play a render — the Library and the variant
    picker had URLs that 404'd.

    Three separate checks, because this is the one endpoint that turns a string from
    a client into a filesystem read: the prefix must be one we publish, the suffix
    must be a media type we produce, and `store` must agree the resolved path is
    still inside the storage root.
    """
    from fastapi.responses import FileResponse

    if not key.startswith(_SERVABLE_ROOTS):
        raise HTTPException(404, "not found")

    suffix = Path(key).suffix.lower()
    if suffix not in _SERVABLE:
        raise HTTPException(404, "not found")

    try:
        path = await store.local_path(key)
    except ValueError:  # escaped the storage root
        raise HTTPException(404, "not found") from None

    if not path.is_file():
        raise HTTPException(404, "not found")

    return FileResponse(
        path,
        media_type=_SERVABLE[suffix],
        # Renders are large and the player needs to seek; without this the browser
        # downloads the whole file before it can start.
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )
