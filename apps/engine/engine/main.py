"""HTTP surface.

Two things matter here: jobs stream their progress, and a job survives the browser
going away. Everything else is CRUD.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from engine import automation
from engine.api import publishing as channels
from engine.api.channels import router as channels_router
from engine.api.insights import router as insights_router
from engine.api.models import router as models_router
from engine.api.publishing import router as publishing_router
from engine.providers import youtube
from engine.quota import ledger
from engine.settings import get_settings
from engine.workflows import video
from engine.workflows.base import StageStatus, WorkflowError

app = FastAPI(title="Studio Engine", version="0.1.0")
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


# In-process job registry. Phase 1 replaces this with Postgres-backed records; the
# shape is kept identical so the swap is contained to this module.
JOBS: dict[str, dict[str, Any]] = {}


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "ok": True,
        "env": s.env,
        "llm_model": s.llm_model,
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
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    JOBS[job_id] = {
        "id": job_id,
        "workflow": wf,
        "inputs": body.model_dump(),
        "states": wf.initial_states(),
        "queue": queue,
        "events": [],
        "status": "running",
    }

    # Deliberately not tied to the request: closing the tab must not cancel a render.
    JOBS[job_id]["task"] = asyncio.create_task(_run_job(job_id))
    return {"job_id": job_id, "status": "running"}


async def _run_job(job_id: str, start_from: str | None = None) -> None:
    job = JOBS[job_id]

    async def emit(event: dict) -> None:
        job["events"].append(event)  # replayed to late subscribers
        await job["queue"].put(event)

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
        await job["queue"].put(None)


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
    """
    job = _require(job_id)

    async def generator():
        for event in list(job["events"]):
            yield {"event": event["type"], "data": _json(event)}
        if job["status"] != "running":
            return
        while True:
            event = await job["queue"].get()
            if event is None:
                break
            yield {"event": event["type"], "data": _json(event)}

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
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

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
        "queue": queue,
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
