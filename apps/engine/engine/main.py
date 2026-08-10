"""HTTP surface.

Two things matter here: jobs stream their progress, and a job survives the browser
going away. Everything else is CRUD.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from engine import automation, db, feedback, logs, models, repository, worker
from engine.api import publishing as channels
from engine.api.brief import router as brief_router
from engine.api.channels import router as channels_router
from engine.api.ideas import router as ideas_router
from engine.api.insights import RECORDS
from engine.api.insights import router as insights_router
from engine.api.models import router as models_router
from engine.api.publishing import router as publishing_router
from engine.api.repurpose import router as repurpose_router
from engine.api.setup import router as setup_router
from engine.api.style import router as style_router
from engine.api.thumbnails import router as thumbnails_router
from engine.insights import VideoRecord, analyze, beats_to_payload
from engine.providers import youtube
from engine.quota import QuotaExceeded, ledger
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
    # First, so that anything failing below is written down rather than only
    # printed at a console nobody is watching. Never fatal - see engine/logs.py.
    logs.install(get_settings().storage_root, "engine")

    # Before anything else, and outside the `persist` branch: `routing.save()` is
    # written by the Models screen whether or not the database is on, so the read
    # has to be unconditional too. Without this the singleton every stage resolves
    # its model through started on DEFAULT_ROUTES in every process, so a route the
    # operator set was persisted and then ignored by the engine that persisted it.
    models.hydrate_routing()

    if get_settings().persist:
        try:
            logger.info("database: {}", await db.ensure_schema())
            await ledger.load()
            JOBS.update(await repository.load_jobs(video.get))
            channels.CHANNELS.update(await repository.load_channels())
            channels.SCHEDULE.update(await repository.load_schedule())
            RECORDS.update(await repository.load_performance_records())
        except Exception:
            # A missing migration must not look like an empty database — starting
            # with a blank quota ledger is exactly how the ceiling gets overrun.
            logger.exception("failed to restore state; refusing to start")
            raise
    else:
        logger.warning("STUDIO_PERSIST=false — state is in-process only and dies with it")
        # The ledger reads STUDIO_PERSIST itself. Pinning it here was a leak: it is
        # a process-wide singleton, so a scratch instance's False survived into
        # anything that ran afterwards with persistence genuinely on.

    yield

    if get_settings().persist:
        await db.dispose()


app = FastAPI(title="Studio Engine", version="0.1.0", lifespan=lifespan)
app.include_router(brief_router)
app.include_router(ideas_router)
app.include_router(publishing_router)
app.include_router(insights_router)
app.include_router(channels_router)
app.include_router(models_router)
app.include_router(setup_router)
app.include_router(repurpose_router)
app.include_router(style_router)
app.include_router(thumbnails_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── expected failures, with the status code that says what they are ─────────
#
# Both of these are raised deep in the provider layer and were caught nowhere, so
# each surfaced as a bare 500 with a stack trace in the log and nothing useful in
# the response. They are not bugs — they are the two things that routinely go
# wrong in normal operation — and each has a specific remedy the caller can act
# on. Handled centrally rather than in try/except at every call site, because
# they can be raised from any route that touches YouTube.


@app.exception_handler(QuotaExceeded)
async def _quota_exceeded(_request: Request, exc: QuotaExceeded) -> JSONResponse:
    """429, because it is a rate limit and it will resolve on its own.

    The body carries the numbers so the caller can decide whether to wait or to
    drop something: a 1,600-unit upload against 400 remaining is a wait until
    midnight Pacific, not a retry in a minute.
    """
    return JSONResponse(
        status_code=429,
        content={
            "detail": str(exc),
            "operation": exc.operation,
            "cost": exc.cost,
            "remaining": exc.remaining,
            "resets": "midnight Pacific",
        },
    )


@app.exception_handler(youtube.ChannelDisconnected)
async def _channel_disconnected(
    _request: Request, exc: youtube.ChannelDisconnected
) -> JSONResponse:
    """409, and it names the way out.

    A dead refresh token cannot be retried around — the only fix is a human
    re-authorising — so the response says where to do that rather than leaving
    the caller to infer it from a 500.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"{exc} — reconnect the channel to continue",
            "reconnect_at": "/v1/auth/google",
        },
    )


class RepurposeInputs(BaseModel):
    """What the `repurpose` workflow needs beyond a topic.

    Nested rather than flattened onto `JobRequest`: these eleven fields are
    meaningless to the Create screen, and hanging them off the request every
    generation reads would make the common case harder to see than the rare one.

    `source_ids` is the only required field, and it is required by
    `RightsStage` rather than here — a validator that rejected an empty list would
    produce a 422 saying "field required" where the workflow produces "no clips
    selected — pick at least one on the Repurpose screen".
    """

    source_ids: list[str] = Field(default_factory=list)
    #: Where each clip's media can be fetched. Lane A only; every other lane
    #: supplies media by its own route. Keyed by source id.
    media_urls: dict[str, str] = Field(default_factory=dict)
    project_id: str = ""
    platform: str = "youtube"
    segment_seconds: float = 20.0
    #: Set by the assemble step once it has replaced the source bed. Non-negotiable
    #: for anything with source footage — TikTok's music licences do not extend to
    #: YouTube — so the gate blocks when it is false.
    audio_bed_replaced: bool = False
    attribution_on_screen: bool = False
    attribution_in_description: bool = False
    annotated: bool = False
    cut_count: int = 0
    #: Stretches of our own footage in the finished timeline, as durations. What
    #: makes an edit more than the clips it quotes.
    original_segments: list[dict] = Field(default_factory=list)
    thesis: str = ""


class JobRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    format: str = Field(default="short", pattern="^(short|long)$")
    aspect: str = Field(default="9:16", pattern=r"^(9:16|16:9|1:1)$")
    workflow: str = "video"
    voice: str | None = None
    target_seconds: int | None = None
    #: Present only for `workflow="repurpose"`. Flattened into the job's inputs by
    #: `create_job`, because stages read `ctx.inputs[...]` flat and threading a
    #: nested dict through every one of them would buy nothing.
    repurpose: RepurposeInputs | None = None


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
        "workflows": sorted(video.STARTABLE),
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
    if body.workflow not in video.STARTABLE:
        # Refused here rather than after the render: "publish" needs a live YouTube
        # client that only the publish endpoint supplies, so starting it directly
        # burned a full generation before failing on a KeyError.
        raise HTTPException(
            400,
            f"workflow must be one of {sorted(video.STARTABLE)}; "
            "publishing goes through POST /v1/jobs/{job_id}/publish",
        )
    try:
        wf = video.get(body.workflow)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    job_id = uuid.uuid4().hex[:12]
    wake = asyncio.Event()
    inputs = body.model_dump()

    # Flattened, because stages read `ctx.inputs[...]` flat and threading a nested
    # dict through every one of them would buy nothing. Popped rather than left
    # alongside, so there is one place a stage can read `source_ids` from and no
    # question about which wins.
    repurpose_inputs = inputs.pop("repurpose", None) or {}
    inputs.update(repurpose_inputs)
    # Feed confirmed channel learnings into every new generation automatically.
    # The Create screen should not need a hidden toggle for the core promise of the
    # product: each researched, published and measured video improves the next one.
    try:
        report = analyze(list(RECORDS.values()))
        inputs["insight_guidance"] = {
            "hook": feedback.guidance_for(report, "hook"),
            "titles": feedback.guidance_for(report, "titles"),
            "thumbnail": feedback.guidance_for(report, "thumbnail"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not attach insight guidance to {}: {}", job_id, exc)

    JOBS[job_id] = {
        "id": job_id,
        "workflow": wf,
        "inputs": inputs,
        "states": wf.initial_states(),
        "wake": wake,
        "events": [],
        "status": "running",
        # Present from the start rather than only after a failure writes it. A
        # restored job always carries the key, so a mirror that sometimes did not
        # made `job["error"]` a landmine and `job.get("error")` the only safe read.
        "error": None,
        # Carried on the mirror as well as the row: `GET /v1/jobs` sorts on it, and
        # reading it back from the database on every list would make a screen that
        # polls hit Postgres for something the process already knows.
        "created_at": datetime.now(UTC),
    }

    await _persist(JOBS[job_id])

    # If this topic was on the backlog, it is now spent. Matched on the string
    # because that is the only link there is: the Create screen sends a topic and
    # never mentions which idea it came from — and a backlog that does not deplete
    # when you act on it is a list that grows forever.
    #
    # Never fatal. A job that ran is worth more than a tidy backlog.
    topic = str(inputs.get("topic") or "").strip()
    if topic:
        try:
            await repository.resolve_backlog_idea(topic=topic, status="used", job_id=job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not mark {!r} used on the backlog: {}", topic, exc)

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
        JOBS[job_id]["enqueued"] = True
        JOBS[job_id]["relayed"] = {}
        _WORKER_OWNED.add(job_id)
        JOBS[job_id]["task"] = asyncio.create_task(_relay(job_id))
    else:
        # Deliberately not tied to the request: closing the tab must not cancel a render.
        _WORKER_OWNED.discard(job_id)
        JOBS[job_id]["task"] = asyncio.create_task(_run_job(job_id, start_from))


#: Job ids this process has handed to a worker and not yet seen finish.
#:
#: The guard on `/rerun` and `/edit` is `status == "running"`, and that is not the
#: same question once a worker is involved. `cancel_job` sets a worker-run job to
#: `cancelled` while explicitly logging that the render *continues* — so the guard
#: waved the re-run through, a second arq job was enqueued for the same id, and two
#: workers rendered the same video into the same storage keys at once. Membership
#: here says "somebody else is executing this", which is the question that actually
#: gates a re-run.
#:
#: Process-local, and that is not a gap: after a restart the mirror is re-synced
#: from the row (`_resync`), which reports the worker's own `running` status and
#: trips the ordinary guard instead.
_WORKER_OWNED: set[str] = set()

#: Event types the relay projects onto a per-stage view. `stage.progress` is
#: deliberately absent — it fires hundreds of times per render and moves nothing
#: the Queue and Library read.
_RELAYED_STATUS = {
    "stage.started": StageStatus.RUNNING,
    "stage.completed": StageStatus.DONE,
    "stage.replayed": StageStatus.DONE,
    "stage.failed": StageStatus.FAILED,
    "stage.skipped": StageStatus.SKIPPED,
}


def _project(job: dict, event: dict) -> None:
    """Fold one worker event into this process's view of the job's stages.

    Without this, a worker-run job was `0/17` in the Queue and the Library from
    the moment it started until the moment it finished: the mirror's `states` are
    written only by whichever process runs the workflow, and for a worker job that
    is not this one. Every stage row sat pending behind an SSE stream that was
    reporting each of them completing.

    Kept *beside* `states` rather than written into it, deliberately. The events
    carry a summary and a cost but not the stage's value, so a `StageState` built
    from one is DONE with no output — and `cancel_job` persists whatever is in
    `states`, which would write that valueless "done" over the worker's real row
    and strand every downstream stage as unresumable on the next restart. The
    projection is display-only and the row stays the single source of truth.
    """
    status = _RELAYED_STATUS.get(str(event.get("type")))
    stage = event.get("stage")
    if status is None or not stage:
        return
    view = job.setdefault("relayed", {})
    entry = view.setdefault(stage, {"status": status, "cost_usd": 0.0})
    entry["status"] = status
    if event.get("cost_usd") is not None:
        entry["cost_usd"] = float(event["cost_usd"])
    if event.get("summary") is not None:
        entry["summary"] = event["summary"]


#: How many consecutive failed re-subscriptions before the relay gives up. At the
#: capped backoff below that is a little over four minutes of trying, which
#: comfortably covers a Redis restart without leaving a task spinning for the
#: lifetime of the process.
_RELAY_MAX_ATTEMPTS = 10
_RELAY_MAX_BACKOFF_S = 30

#: How long an SSE subscriber waits on the in-process wake signal before re-reading
#: the row. Only for worker-owned jobs, where the signal can stop arriving without
#: the job having stopped — see `stream_job`.
_ROW_POLL_S = 2.0

#: Statuses that mean the row will not change again.
_TERMINAL = ("completed", "failed", "cancelled")


async def _row_finished(job_id: str) -> bool:
    """Does the job's row already hold a terminal status?

    The relay's answer to "is there any point reconnecting". Failure to read is
    reported as "not finished", because retrying a subscription is the cheaper
    mistake than abandoning a live render's progress.
    """
    if not get_settings().persist:
        return False
    try:
        fresh = (await repository.reload_jobs([job_id], video.get)).get(job_id)
    except Exception:  # noqa: BLE001
        logger.warning("could not read job {}'s row while reconnecting its relay", job_id)
        return False
    return fresh is not None and fresh.get("status") in _TERMINAL


async def _relay(job_id: str) -> None:
    """Mirror a worker's events into this process's log so SSE works unchanged.

    Subscribers read `job["events"]` by cursor and know nothing about where the
    work runs. Appending here keeps that true for both execution paths.

    Reconnecting, because the subscription is the only thing carrying a running
    render's progress into this process and it is not durable: Redis restarting, a
    dropped socket or an idle timeout ended `listen()` without a `__done__`, and
    the relay simply returned. The render carried on for another ten minutes with
    nothing watching, every open SSE stream parked on `waiting.wait()`, and the
    mirror answered `running` until the API was restarted. Between attempts the row
    is consulted — if the job has already finished there is nothing left to
    subscribe to, and reconnecting forever against a dead job would be its own leak.
    """
    import json

    from arq.connections import create_pool

    job = JOBS[job_id]
    pool = None
    finished = False
    attempt = 0
    try:
        while not finished:
            try:
                if pool is None:
                    pool = await create_pool(worker.redis_settings())
                pubsub = pool.pubsub()
                await pubsub.subscribe(worker.CHANNEL.format(job_id))
                # Reset only once a subscription is actually established, so the
                # backoff measures consecutive *failures* rather than reconnections.
                attempt = 0

                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue  # the subscribe confirmation, not an event
                    event = json.loads(message["data"])
                    if event.get("type") == "__done__":
                        finished = True
                        break
                    job["events"].append(event)
                    _project(job, event)
                    _wake(job)

                with suppress(Exception):
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
            except asyncio.CancelledError:
                # `cancel_job` cancels this task on purpose; retrying would fight it.
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("relay for job {} lost its subscription ({})", job_id, exc)
                if pool is not None:
                    with suppress(Exception):
                        await pool.aclose()
                    pool = None

            if finished:
                break
            if await _row_finished(job_id):
                # The worker published `__done__` while this was reconnecting, or
                # died after writing a terminal status. Either way there is nothing
                # more to hear; the `finally` below reads the row for the outcome.
                break

            attempt += 1
            if attempt > _RELAY_MAX_ATTEMPTS:
                logger.error(
                    "giving up on the event relay for job {} after {} attempts; "
                    "its status will be read from the row",
                    job_id,
                    _RELAY_MAX_ATTEMPTS,
                )
                break
            await asyncio.sleep(min(2**attempt, _RELAY_MAX_BACKOFF_S))
    finally:
        if pool is not None:
            with suppress(Exception):
                await pool.aclose()
        _WORKER_OWNED.discard(job_id)
        # The worker owns the row, so re-read it for the final status rather than
        # inferring one from the last event seen. `reload_jobs`, not `load_jobs`:
        # the latter would rewrite a still-"running" row to "interrupted".
        with suppress(Exception):
            fresh = (await repository.reload_jobs([job_id], video.get)).get(job_id)
            if fresh is not None:
                job["status"] = fresh["status"]
                job["states"] = fresh["states"]
                # `error` too. Copying only the status meant every worker-run
                # failure was served by `GET /v1/jobs` as
                # `{status: "failed", error: null}` — the Queue showed a bare
                # "failed" with no reason anywhere in the UI, while the reason sat
                # in the row the whole time. `updated_at` comes with it so the list
                # does not sort a finished job by when it started.
                job["error"] = fresh.get("error")
                job["updated_at"] = fresh.get("updated_at")
                # The row is authoritative again, so the projection would only add
                # a second, staler answer to the same question.
                job["relayed"] = {}
                if job.get("status") == "completed":
                    await _capture_published_record(job)
        _wake(job)


def _needs_resync(job: dict) -> bool:
    """Might this mirror entry be behind its row?

    Only when nothing *here* is following the job. `task` is set by `_dispatch` for
    both execution paths — the relay for a worker job, the coroutine for an
    in-process one — so its absence on a job that still claims to be running means
    the row is being written by somebody else: the worker, or this API before it
    restarted.

    Deliberately false while the relay is running, even though the worker is the
    writer there too. The relay's live projection is *ahead* of the row, which is
    only saved on stage boundaries, so re-reading would replace fresher data with
    staler.

    `cancelled` is in the list with the other two, and it has to be: cancelling a
    worker-run job writes `cancelled` to the row while the render carries on, so
    after a restart that status is the one thing standing between a re-run and a
    second worker on the same job. The worker overwrites it at its next stage
    boundary; re-reading is how this process finds out.

    A *finished* follower is no follower. `task is None` alone missed the case that
    matters most: the relay dies — Redis restarts, the worker is killed, the
    subscription drops — and leaves a task object that is `done()`. Nothing was
    following the job any more, but this said otherwise, so the read endpoints kept
    serving a mirror frozen at whatever the last event said. A render that finished
    perfectly well answered `running` forever and `POST /publish` refused it with
    409 until the API was restarted.
    """
    if job.get("status") not in ("running", "interrupted", "cancelled"):
        return False
    task = job.get("task")
    return task is None or task.done()


def _worker_owned(job: dict) -> bool:
    """Is somebody other than this process executing this job right now?

    Two ways to be true, and both are needed. `_WORKER_OWNED` covers this process's
    own lifetime, including the case where `cancel_job` has already overwritten the
    status to `cancelled` while the render carried on. `_needs_resync` covers the
    other one: a mirror restored from a row this process never dispatched.
    """
    return job["id"] in _WORKER_OWNED or _needs_resync(job)


async def _resync(job_ids: list[str]) -> None:
    """Refresh worker-owned mirror entries from their rows.

    The mirror is written only by the process that dispatched the job, so an API
    restarted mid-render answered from the snapshot its lifespan restored and never
    moved again: the render finished, the row said `completed`, and `GET
    /v1/jobs/{id}` still said `interrupted` at 0 stages while `POST .../publish`
    refused it for not being completed. A second, well-timed restart was the only
    cure. Called from the read endpoints and from the gates that gate on status.
    """
    if not job_ids or not get_settings().persist:
        return
    try:
        fresh = await repository.reload_jobs(job_ids, video.get)
    except Exception:  # noqa: BLE001 — a stale answer is better than a 500
        logger.exception("could not re-sync job(s) {}", job_ids)
        return

    for job_id, row in fresh.items():
        job = JOBS.get(job_id)
        if job is None:
            continue
        job["status"] = row["status"]
        job["states"] = row["states"]
        job["error"] = row.get("error")
        job["updated_at"] = row.get("updated_at")
        # Only ever forward: the row's log is what the worker has published so far,
        # and a shorter one would move an SSE subscriber's cursor backwards.
        if len(row.get("events") or []) > len(job.get("events") or []):
            job["events"] = row["events"]
        job["relayed"] = {}
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

    # Cleared up front, so a job that failed, was re-run and then succeeded does
    # not keep reporting the old reason next to a "completed" status.
    job["error"] = None

    async def emit(event: dict) -> None:
        if job.get("status") in _TERMINAL and event["type"] == "stage.progress":
            # The render thread keeps reporting until its next check point, and a
            # cancelled job's stream had already emitted `stream.closed`. Appending
            # progress after that re-woke every subscriber with news about a job the
            # UI has finished with.
            return
        # The event log is the single source of truth. Subscribers read it by
        # cursor, so one append serves every viewer exactly once — see stream_job.
        job["events"].append(event)
        _wake(job)
        # Persist on stage boundaries rather than every progress tick: a render
        # emits hundreds of those, and the resume point only moves on a boundary.
        if event["type"].startswith(("stage.completed", "stage.failed", "workflow.")):
            await _persist(job)

    try:
        # Inside the try, so a disconnected channel is recorded as a failed job with
        # a reason rather than escaping as an unhandled exception in a detached task.
        # Here rather than in `_dispatch` because `/edit` and `POST .../publish` both
        # reach this function without going through it.
        try:
            await channels.attach_youtube_client(job)
        except WorkflowError as exc:
            # `Workflow.run` emits its own `workflow.failed` before raising; this
            # one happens before the run starts, so the frame has to come from here
            # or the browser's last event is a `workflow.started` that never ended.
            await emit({"type": "workflow.failed", "job_id": job_id, "error": str(exc)})
            raise

        await job["workflow"].run(
            job_id=job_id,
            inputs=job["inputs"],
            emit=emit,
            states=job["states"],
            budget_usd=get_settings().max_cost_per_video_usd,
            start_from=start_from,
        )
        job["status"] = "completed"
        await _capture_published_record(job)
    except WorkflowError as exc:
        job["status"] = "failed"
        # Recorded, not just logged. `GET /v1/jobs` reads this, and without it
        # every in-process failure reported `error: null` — so the Queue showed a
        # bare "failed" and the only way to learn why was the server's terminal.
        # That covers budget aborts too: `BudgetExceeded` subclasses this.
        job["error"] = str(exc)
        logger.error("job {} failed: {}", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
        logger.exception("job {} crashed", job_id)
        await emit({"type": "workflow.failed", "job_id": job_id, "error": str(exc)})
    finally:
        # Status is already final here, so a woken subscriber sees "not running"
        # and closes its stream rather than waiting for an event that never comes.
        await _persist(job)
        _wake(job)


def _output_value(job: dict, stage: str) -> Any:
    state = job.get("states", {}).get(stage)
    return state.output.value if state is not None and state.output is not None else None


def _output_model(job: dict, *stages: str) -> str:
    for stage in stages:
        state = job.get("states", {}).get(stage)
        if state is not None and state.output is not None and state.output.provenance.model:
            return state.output.provenance.model
    return ""


def _published_record(job: dict) -> VideoRecord | None:
    if job.get("workflow").name != "publish" or job.get("status") != "completed":
        return None

    video_id = _output_value(job, "upload")
    titles = _output_value(job, "titles") or []
    title_index = int(job.get("inputs", {}).get("chosen_title_index", 0) or 0)
    title = titles[title_index].text if 0 <= title_index < len(titles) else ""
    strategy = titles[title_index].strategy if 0 <= title_index < len(titles) else ""

    hook = _output_value(job, "hook") or {}
    hook_variants = hook.get("variants") if isinstance(hook, dict) else None
    hook_index = int(hook.get("chosen", 0) if isinstance(hook, dict) else 0)
    hook_device = ""
    if isinstance(hook_variants, list) and 0 <= hook_index < len(hook_variants):
        hook_device = str(hook_variants[hook_index].get("device", ""))

    thumbnails = _output_value(job, "thumbnail") or []
    thumb_index = int(job.get("inputs", {}).get("chosen_thumbnail_index", 0) or 0)
    thumbnail_concept = ""
    if 0 <= thumb_index < len(thumbnails) and isinstance(thumbnails[thumb_index], dict):
        thumbnail_concept = str(thumbnails[thumb_index].get("template", ""))

    if not video_id:
        return None

    publish_at = job.get("inputs", {}).get("publish_at")
    published_at = (
        publish_at.isoformat()
        if isinstance(publish_at, datetime)
        else str(publish_at or datetime.now(UTC).isoformat())
    )
    return VideoRecord(
        video_id=str(video_id),
        title=title,
        published_at=published_at,
        title_strategy=strategy,
        hook_device=hook_device,
        thumbnail_concept=thumbnail_concept,
        script_model=_output_model(job, "revision", "draft"),
        format=str(job.get("inputs", {}).get("format", "short")),
        # Without this the retention map and the Shorts selector both read an empty
        # beat list on every published video — the field they read did not exist and
        # `getattr(..., [])` hid it. Carried as plain dicts so the record survives
        # the JSON column unchanged.
        beats=_published_beats(job),
    )


def _published_beats(job: dict) -> list[dict]:
    """The beats the script was written in, in stored form.

    The `beats` stage output is the canonical list; a publish job that was resumed
    from a partial run may not have it, and an empty list is the honest answer there
    rather than a reconstruction.
    """
    raw = _output_value(job, "beats")
    if not raw:
        return []
    try:
        return beats_to_payload(list(raw))
    except (TypeError, ValueError) as exc:
        logger.warning("could not record beats for attribution: {}", exc)
        return []


async def _capture_published_record(job: dict) -> None:
    """Seed the analytics feedback loop as soon as a publish lands."""
    record = _published_record(job)
    if record is None:
        return
    RECORDS[record.video_id] = record
    if get_settings().persist:
        try:
            await repository.save_performance_record(record, job_id=job["id"])
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist performance record for {}", record.video_id)


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


def _stage_view(job: dict):
    """`(status, cost_usd)` per stage, with the worker's relayed events layered on.

    The stored `StageState` wins whenever it has an output, because that is a
    completed stage with a real value behind it. Where it has none — every stage of
    a job this process is only relaying — the projection from the event stream
    answers instead, which is what makes a worker-run job show `4/17 · Voiceover`
    rather than `0/17` until the moment it finishes.
    """
    states = job.get("states", {})
    relayed = job.get("relayed") or {}

    def view(name: str) -> tuple[StageStatus, float]:
        state = states.get(name)
        entry = relayed.get(name)
        if state is not None and state.output is not None:
            return state.status, state.output.cost_usd
        if entry is not None:
            return entry["status"], float(entry.get("cost_usd") or 0.0)
        return (state.status if state is not None else StageStatus.PENDING), 0.0

    return view


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
async def list_jobs(
    status: str | None = None,
    # Declared, not just clamped. The clamp below still guards the slice, but a
    # bound that only exists in the body is invisible to the OpenAPI document —
    # so `?limit=-1` was a 200 returning one row rather than a 422 saying why.
    limit: int = Query(100, ge=1, le=500),
) -> list[JobSummary]:
    """Every job, newest first.

    This did not exist, so the Queue and Library had nothing to read and rendered
    demo data permanently — generate a video and neither screen would ever change.
    They are the two screens someone looks at immediately after pressing Generate.

    `status` filters to one state; the Library asks for `completed` and the Queue
    takes everything.
    """
    # Before the filter, not after: `?status=completed` is how the Library asks, and
    # a worker-finished render whose mirror still said `interrupted` was missing from
    # the one screen it belongs on.
    await _resync([job_id for job_id, job in JOBS.items() if _needs_resync(job)])

    out: list[JobSummary] = []
    for job_id, job in JOBS.items():
        if status and job.get("status") != status:
            continue

        states = job.get("states", {})
        view = _stage_view(job)
        done = sum(1 for name in states if view(name)[0] is StageStatus.DONE)
        running = next((name for name in states if view(name)[0] is StageStatus.RUNNING), None)
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
                cost_usd=round(sum(view(name)[1] for name in states), 4),
                created_at=job.get("created_at"),
                updated_at=job.get("updated_at"),
                stages_done=done,
                stages_total=len(states),
                current_stage=running,
                error=job.get("error") or None,
                # "video" is what `RenderStage` emitted before the key was
                # corrected to match its stage name. Jobs rendered before that
                # are already in people's databases, and dropping the fallback
                # would un-link videos that currently work.
                render_key=artifacts.get("render") or artifacts.get("video"),
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
    if _needs_resync(job):
        await _resync([job_id])
    view = _stage_view(job)
    return {
        "id": job_id,
        "status": job["status"],
        # Through the serialiser, not verbatim: a publish job's inputs carry a live
        # YouTube client holding an access token, so returning them raw both 500s on
        # serialisation and is one annotation away from putting a token in a response.
        "inputs": repository.jsonable(job["inputs"]),
        "stages": _serialize_stages(job),
        "cost_usd": round(sum(view(name)[1] for name in job["states"]), 4),
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

    A stream opened against a stale mirror converges on the row rather than
    trusting the signal. `waiting.wait()` alone assumed something in *this* process
    would eventually fire it, which is true for an in-process job and not for a
    worker-run one: if the relay died, nothing ever wakes the stream and the tab
    spins on a job that finished ten minutes ago. For worker-owned jobs the wait is
    bounded and the row is re-read on each timeout.
    """
    job = _require(job_id)
    if _needs_resync(job):
        await _resync([job_id])

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
                # A terminal frame, then stop. Closing the stream without one is a
                # *reconnect* signal to EventSource, so a finished job replayed its
                # entire log every few seconds — re-dispatching workflow.started and
                # flipping the Publish button's state on a loop, forever.
                yield {
                    "event": "stream.closed",
                    "data": _json({"type": "stream.closed", "status": job["status"]}),
                }
                return

            if _worker_owned(job):
                # Bounded, then re-read. The row is the only thing that can tell
                # this process a worker-run job has ended once the relay is gone.
                with suppress(TimeoutError):
                    await asyncio.wait_for(waiting.wait(), _ROW_POLL_S)
                if _needs_resync(job):
                    await _resync([job_id])
            else:
                await waiting.wait()

    return EventSourceResponse(generator())


@app.post("/v1/jobs/{job_id}/publish", status_code=202)
async def publish_job(job_id: str, body: PublishRequest, force: bool = False) -> dict:
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

    # A render the worker finished while this process was restarting is still
    # `interrupted` in the mirror, and the check below reads the mirror. Without
    # this, a perfectly good video could not be published until the API had been
    # restarted a *second* time.
    if _needs_resync(source):
        await _resync([job_id])

    if source["status"] != "completed":
        raise HTTPException(409, f"job is {source['status']}; only a completed job can publish")
    if source["workflow"].name != "video":
        raise HTTPException(409, f"job ran the '{source['workflow'].name}' workflow, not 'video'")

    # Idempotent unless explicitly overridden. Nothing checked for an existing
    # publish job, and the source stays `completed` while the web Publish button
    # re-enables — so a second click uploaded the same video to YouTube twice, at
    # 1,600 quota units and one duplicate public video each.
    existing = _existing_publish(job_id)
    if existing and not force:
        publish_id, state = existing
        # Differentiated on the upload stage rather than the job status, because
        # the two situations have different recoveries and the old one-line message
        # named neither. "Already published" for a publish that died before the
        # upload is simply wrong, and it sent people to `?force=true` — a second
        # 1,600-unit spend — for a video that was already live.
        if _upload_landed(JOBS[publish_id]):
            video_id = _uploaded_video_id(JOBS[publish_id]) or "an unrecorded id"
            detail = (
                f"video already uploaded as {video_id} by publish job {publish_id} "
                f"({state}); re-run its remaining steps instead of publishing again"
            )
        elif state == "running":
            detail = (
                f"publish job {publish_id} is still running and has not uploaded yet; "
                "wait for it, or pass ?force=true to publish anyway (another 1,600 "
                "quota units)"
            )
        else:
            detail = (
                f"previous publish {publish_id} was {state} before the upload landed; "
                "re-publish with ?force=true (this spends another 1,600 quota units)"
            )
        raise HTTPException(409, detail)

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
    #
    # Re-read first: the worker process does the uploading, so this process's cache
    # is exactly the one that does not see the spend. `UploadStage` reserves
    # atomically further down and is the real ceiling; this is the early, honest
    # refusal, and refusing on a stale cache would either wave through an upload
    # there is no room for or block one there is.
    await ledger.refresh()
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
        "error": None,  # same reason as `create_job`
        # Same reason as `create_job`: `GET /v1/jobs` sorts on this. Without it a
        # publish job sorted to the very bottom of the Queue — the one job someone
        # is actively watching, filed underneath everything they finished last week.
        "created_at": datetime.now(UTC),
    }
    JOBS[publish_id]["task"] = asyncio.create_task(_run_job(publish_id))

    logger.info("publishing job {} as {}", job_id, publish_id)
    return {"job_id": publish_id, "status": "running", "source_job_id": job_id}


def _severity_of(critique: Any) -> int:
    """The critique's severity, however the stage happened to store it.

    This was `getattr(critique, "severity", 0)`. CritiqueStage returns the parsed
    JSON verbatim and `decode_value` hands back a plain dict, and `getattr` on a
    dict always returns the default — so it read 0 every time and the weak-script
    blocker could not fire once in the whole history of the code. The tests that
    covered that blocker construct `VideoState` directly, which is why they never
    saw it.

    Written for a dict but not assuming one: a stage output that has been edited
    through `POST /v1/jobs/{id}/edit` can be any JSON value, and a blocker that
    raises AttributeError is worse than one that reads zero.
    """
    if isinstance(critique, Mapping):
        raw = critique.get("severity", 0)
    else:
        raw = getattr(critique, "severity", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _upload_landed(job: dict) -> bool:
    """Did this publish job's `upload` stage complete?

    The only question that matters when deciding whether a second publish would
    duplicate a video. What the *job* says about itself does not answer it: an
    `interrupted` or `cancelled` publish can be holding a finished upload and a
    live video on the channel.
    """
    state = job.get("states", {}).get("upload")
    return state is not None and state.status is StageStatus.DONE


def _uploaded_video_id(job: dict) -> str:
    state = job.get("states", {}).get("upload")
    return str(state.output.value) if state is not None and state.output else ""


def _existing_publish(source_job_id: str) -> tuple[str, str] | None:
    """A publish job for this source that must block another publish.

    Blocking is the default. `failed` and `cancelled` are exempt only while the
    upload has *not* landed — that is the case `force` exists for, and refusing it
    would strand a video whose upload died halfway.

    This used to test for `("running", "completed")`, which is not the complement
    of "failed": a publish job that was mid-upload when the process died comes back
    from `load_jobs` as **`interrupted`**, and a publish job cancelled after its
    upload landed is **`cancelled`** — `cancel_job` explicitly accepts an
    interrupted job, so cancel-then-republish is an ordinary operator move.

    The third case is **failed-after-upload**, and it is the easiest one to hit:
    `UploadStage` goes DONE and then a *later* stage of the publish workflow —
    thumbnail, captions, playlist — fails, which fails the whole job. The video is
    live on YouTube and the 1,600 units are spent, but `failed` waved the re-publish
    straight through and uploaded it a second time. All three now fall through to
    the `_upload_landed` 409, which says what happened and points at `?force=true`.
    A publish that never got as far as uploading is the one case where re-publishing
    is the right answer, so those still pass.
    """
    for publish_id, job in JOBS.items():
        if job.get("inputs", {}).get("source_job_id") != source_job_id:
            continue
        status = job.get("status")
        if status in ("failed", "cancelled") and not _upload_landed(job):
            continue
        return publish_id, str(status)
    return None


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
        critique_severity=_severity_of(critique),
        # None for a video built from no clips at all — the ordinary case, and not
        # a failure. A wholly original video has nothing for that gate to judge,
        # and inventing a passing report for it would be the wrong default in the
        # one direction that matters.
        originality=value("originality"),
    )


# One-off videos are not part of a series, so they get a permissive series record.
# The quality blockers in publish_blockers() do not depend on it — the parameter
# exists for the auto-publish path, which passes the real series.
_PUBLISH_SERIES = automation.Series(id="", name="ad-hoc", niche="", monthly_budget_usd=float("inf"))


def _refuse_ungated_upload(job: dict, stage: str) -> None:
    """Refuse a re-run that would put `UploadStage` back on the wire.

    `/edit` and `/rerun` re-execute a stage and everything downstream of it. On a
    *publish* job that set can contain `upload`, and neither endpoint runs a single
    one of the publish gate's checks — no `publish_blockers`, no `can_afford`, no
    existing-publish check. Editing the description of a published video therefore
    uploaded the whole thing to YouTube a second time: 1,600 more units, a duplicate
    public video, and CLAUDE.md non-negotiable #3 bypassed entirely.

    Deliberately keyed on the workflow and the invalidated set rather than on
    `_existing_publish`: that helper looks for a *different* job publishing the same
    source, and here the job re-running upload is its own publish job, so it never
    fires. The other three publish stages stay individually re-runnable — a failed
    thumbnail or caption is cheap, deterministic, and exactly what the Queue's
    per-step retry is for.
    """
    if job["workflow"].name != "publish":
        return
    affected = [stage, *job["workflow"].dependents_of(stage)]
    if "upload" not in affected:
        return

    source_job_id = job["inputs"].get("source_job_id", job["id"])
    raise HTTPException(
        409,
        f"'{stage}' cannot be re-run here: it would re-execute the upload stage and "
        "send this video to YouTube again, and this endpoint is not the approval "
        "gate. Publish through "
        f"POST /v1/jobs/{source_job_id}/publish?force=true, which checks the "
        "quality blockers and the quota ledger first.",
    )


async def _refuse_while_a_worker_owns_it(job: dict) -> None:
    """409 if another process is executing this job right now.

    `status == "running"` is not a sufficient gate once a worker is in play, and
    the hole is reachable in two ordinary ways:

      * **Cancel, then re-run.** `cancel_job` sets a worker-run job to `cancelled`
        and says so in its own log — *the render continues*. The status gate then
        let a re-run through, and two workers rendered the same job into the same
        storage keys at once, interleaved.
      * **Restart the API mid-render.** The mirror comes back `interrupted`, which
        is likewise not `running`. `_resync` here reads the row the worker is
        writing and restores the truth before the gate is checked.

    Both endpoints that call this mutate stage state immediately afterwards, which
    is why the refusal has to come first.
    """
    if not _worker_owned(job):
        return
    if _needs_resync(job):
        await _resync([job["id"]])
    if job["id"] in _WORKER_OWNED or job.get("status") == "running":
        raise HTTPException(
            409,
            f"job {job['id']} is being executed by a worker; cancelling only stops "
            "this process's event relay, not the render. Wait for it to finish, "
            "then re-run.",
        )


@app.post("/v1/jobs/{job_id}/edit")
async def edit_stage(job_id: str, body: EditRequest) -> dict:
    """Accept a user edit and re-run from that point.

    This is the interaction the Create screen is built around: change the hook, and
    everything downstream regenerates while the research above it is left alone.
    """
    job = _require(job_id)
    await _refuse_while_a_worker_owns_it(job)
    if job["status"] == "running":
        raise HTTPException(409, "job is still running; wait or cancel first")

    # Before `mark_edited`, which writes the new value in place: refusing after it
    # would leave the edit applied and its downstream stages STALE with nothing
    # coming to re-run them.
    _refuse_ungated_upload(job, body.stage)

    try:
        invalidated = job["workflow"].mark_edited(job["states"], body.stage, body.value)
    except (KeyError, WorkflowError) as exc:
        raise HTTPException(400, str(exc)) from exc

    job["status"] = "running"
    job["task"] = asyncio.create_task(
        _run_job(job_id, start_from=invalidated[0] if invalidated else None)
    )
    return {"invalidated": invalidated, "status": "running"}


class RerunRequest(BaseModel):
    stage: str


@app.post("/v1/jobs/{job_id}/rerun")
async def rerun_stage(job_id: str, body: RerunRequest) -> dict:
    """Re-run one stage and everything downstream of it.

    Distinct from `/edit`, which *replaces* a stage's value and keeps it DONE. This
    discards the value and regenerates — which is what the Create screen's "Re-run
    from here" means, and what its own caption promises: "Everything below this
    stage regenerates. Nothing above it is touched."

    That control existed and called `console.log`. It could not call `/edit`,
    because doing so needs the stage's current value and the API never gives the
    client one — `GET /v1/jobs/{id}` returns a `summary` string, not the object.
    """
    job = _require(job_id)
    # Before anything is invalidated below: a refusal after the STALE writes would
    # leave the job with nothing coming to re-run it.
    await _refuse_while_a_worker_owns_it(job)
    if job["status"] == "running":
        raise HTTPException(409, "job is still running; wait or cancel first")

    states = job["states"]
    if body.stage not in states:
        raise HTTPException(404, f"unknown stage '{body.stage}'")
    if states[body.stage].status is StageStatus.PENDING:
        raise HTTPException(409, f"stage '{body.stage}' has not run yet")

    _refuse_ungated_upload(job, body.stage)

    invalidated = [body.stage, *job["workflow"].dependents_of(body.stage)]
    for name in invalidated:
        states[name].status = StageStatus.STALE
        states[name].output = None
        states[name].error = None

    job["status"] = "running"
    job["error"] = None
    await _persist(job)
    await _dispatch(job_id, start_from=body.stage)
    return {"invalidated": invalidated, "status": "running"}


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    """Stop a job and tell everyone watching.

    The status change alone was not enough. `stream_job` parks on
    `await waiting.wait()` and only re-checks the status when that Event fires, so
    without the `_wake` below every open SSE connection hung forever — the browser
    tab sat on a spinner for a job that had already stopped. And the row was never
    written, so the cancellation survived only until the next restart, where it came
    back as `interrupted`.
    """
    job = _require(job_id)

    if job["status"] not in ("running", "interrupted"):
        # Cancelling a finished job would rewrite a real outcome with a false one.
        return {"status": job["status"], "note": "already finished"}

    if not job.get("enqueued"):
        # Before the cancel below, not after: `task.cancel()` unwinds the coroutine
        # but the render is in a thread that has to be *asked*, and `compose_video`
        # will not return until that thread has gone. Setting the flag first is what
        # makes the await below finish in seconds instead of at the end of the encode.
        from engine.workflows.media import abort_render

        if abort_render(job_id):
            logger.info("asked job {}'s render thread to stop", job_id)

    task = job.get("task")
    if task and not task.done() and not job.get("enqueued"):
        # Only the in-process path has a task worth cancelling. For a worker-run
        # job `task` is the *relay*, and cancelling it stops nothing that matters
        # while costing two things that do: the browser's event stream goes silent
        # even though the render is still publishing frames, and `_relay`'s finally
        # drops the job out of `_WORKER_OWNED` — which is what tells `/rerun` and
        # `/edit` that somebody else is still executing it. Dropping it was how
        # cancel-then-re-run started a second worker on the same job.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    job["status"] = "cancelled"
    event = {"type": "workflow.cancelled", "job_id": job_id}
    job["events"].append(event)
    # Load-bearing: this is what lets the SSE generator notice and close.
    _wake(job)

    if _worker_owned(job) or job.get("enqueued"):
        # A status-only write, because this process is not the one doing the work.
        # `_persist` would push the mirror's *whole* state — all-PENDING stages and
        # a zero cost, since the worker's stage outputs never leave the worker — over
        # a row that holds real finished stages. Cancelling a render at stage twelve
        # therefore erased eleven completed stages and the money they cost, and left
        # a job nothing could resume.
        try:
            await repository.update_job_status(job_id, "cancelled", event)
        except Exception:  # noqa: BLE001 — the in-memory cancel already stands
            logger.exception("could not record the cancellation of job {}", job_id)
    else:
        await _persist(job)

    if job.get("enqueued"):
        # A job running in the worker process is not reachable by cancelling a local
        # task — that only stops the relay. The render continues and its `finally`
        # will overwrite this status. Say so rather than implying it stopped.
        logger.warning(
            "job {} is running in a worker; the local relay stopped but the render "
            "continues until it finishes",
            job_id,
        )
        return {"status": "cancelled", "note": "worker render continues; see KNOWN-ISSUES"}

    return {"status": "cancelled"}


def _require(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


def _serialize_stages(job: dict) -> list[dict]:
    from engine.workflows.base import summarize

    view = _stage_view(job)
    relayed = job.get("relayed") or {}
    out = []
    for stage in job["workflow"].stages:
        state = job["states"][stage.name]
        status, cost = view(stage.name)
        out.append(
            {
                "name": stage.name,
                "title": stage.title,
                "status": status.value,
                # The relayed summary is the worker's own `summarize(output.value)`,
                # sent on the completion event — the only version of it this process
                # can have, since the value itself never leaves the worker.
                "summary": summarize(state.output.value)
                if state.output
                else (relayed.get(stage.name) or {}).get("summary"),
                "cost_usd": round(cost, 4),
                "elapsed_ms": state.elapsed_ms,
                "error": state.error,
                # Both conditions, not just "it finished". The stage also has to
                # hold something a person can meaningfully retype — most hold
                # dataclasses, and offering an edit the endpoint will refuse is
                # how a UI teaches people not to trust it.
                #
                # `state.status`, not the relayed one: a stage the worker has
                # finished is DONE in the projection but has no *value* here yet,
                # and `mark_edited` refuses a stage with no output.
                "editable": state.status is StageStatus.DONE and stage.editable,
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
