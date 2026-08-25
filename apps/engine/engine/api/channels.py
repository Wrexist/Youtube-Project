"""Channel launch endpoints.

The route names avoid the word "create" deliberately: this designs a channel, it
does not create one. YouTube has no API for that.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from engine import channel as ch
from engine import repository
from engine.api.publishing import CHANNELS, credentials_for
from engine.providers.youtube import YouTube
from engine.workflows.base import Workflow
from engine.workflows.channel_launch import CHANNEL_LAUNCH_STAGES, assemble

router = APIRouter(prefix="/v1/channels", tags=["channels"])

LAUNCH_WORKFLOW = Workflow("channel_launch", CHANNEL_LAUNCH_STAGES)
LAUNCHES: dict[str, dict] = {}


async def restore() -> None:
    """Hydrate the launch mirror from its rows, called by the lifespan handler.

    A launch that was mid-run at shutdown comes back `interrupted` rather than
    `running` — nothing is executing it any more, and a status that promises
    progress which will never arrive is the lie the jobs mirror already refuses
    to tell. The design is a regenerable LLM artifact; re-run it.
    """
    for launch_id, row in (await repository.load_launches()).items():
        payload = row.get("payload") or {}
        states, _ = repository.load_states(
            payload.get("states") or {}, LAUNCH_WORKFLOW.initial_states()
        )
        status = row["status"]
        LAUNCHES[launch_id] = {
            "id": launch_id,
            "niche": row["niche"],
            "states": states,
            "events": payload.get("events") or [],
            "status": "interrupted" if status == "running" else status,
            "inputs": payload.get("inputs") or {},
        }
    if LAUNCHES:
        logger.info("restored {} channel launch(es)", len(LAUNCHES))


async def _save(launch_id: str) -> None:
    record = LAUNCHES[launch_id]
    await repository.save_launch(
        launch_id,
        record["status"],
        record["niche"],
        {
            "states": repository.dump_states(record["states"]),
            "events": record["events"],
            "inputs": record["inputs"],
        },
    )


class LaunchRequest(BaseModel):
    niche: str = Field(min_length=3, max_length=200)
    country: str = "US"
    language: str = "en"


class ApplyRequest(BaseModel):
    launch_id: str
    confirm_channel_created: bool = False


class LaunchStage(BaseModel):
    """One row of the design pipeline, as the New channel screen draws it."""

    name: str
    title: str
    status: str
    summary: str = ""
    error: str | None = None


class LaunchIdentity(BaseModel):
    name: str
    handle: str
    tagline: str
    description: str
    keywords: list[str]
    keywords_string: str
    avatar_concept: str
    banner_concept: str
    palette: list[str]


class LaunchProblem(BaseModel):
    field: str
    message: str
    fatal: bool


class LaunchBacklogItem(BaseModel):
    topic: str
    score: float
    duplicate_of: str | None = None


class ManualStep(BaseModel):
    id: str
    title: str
    detail: str
    url: str | None = None


class LaunchOut(BaseModel):
    """A launch design in full — response models rather than a bare dict, so the
    contract package generates real types instead of `Record<string, unknown>`."""

    id: str
    status: str
    error: str | None = None
    stages: list[LaunchStage]
    niche: str
    identity: LaunchIdentity | None = None
    #: LLM-shaped stage outputs. Kept as dicts: their shape is the prompt's,
    #: and freezing it into a model would break on every prompt iteration.
    positioning: dict | None = None
    name_options: dict | None = None
    visuals: dict | None = None
    series: dict | None = None
    backlog: list[LaunchBacklogItem]
    problems: list[LaunchProblem]
    blocked: bool
    manual_steps: list[ManualStep]
    cost_usd: float


class LaunchSummary(BaseModel):
    id: str
    niche: str
    status: str
    stages_done: int
    stages_total: int


class Playlist(BaseModel):
    id: str
    title: str
    #: How many videos are already in it. The one number that tells a picker which
    #: of five similarly-named playlists is the live one.
    count: int


@router.get("/playlists")
async def playlists() -> list[Playlist]:
    """The connected channel's playlists, so a publish can pick one.

    `PlaylistStage` has been able to add a video to a playlist since it was
    written, and skipped on every run, because `playlist_id` was never set by
    anything — there was no way to learn an id short of reading it out of a YouTube
    URL. One quota unit.

    An empty list when no channel is connected, rather than an error: the publish
    screen asks for this before it knows whether it will need it, and a 4xx there
    would surface as a failure on a screen where nothing has failed.
    """
    creds = await credentials_for("default")
    if creds is None:
        return []
    try:
        found = await YouTube(creds).playlists()
    except Exception as exc:  # noqa: BLE001 - a picker is not worth failing a screen for
        logger.warning("could not list playlists: {}", exc)
        return []
    return [Playlist(**item) for item in found]


@router.get("/limits")
async def limits() -> dict:
    """The constraints the designer works within, so the UI can show live counters."""
    return {
        "name_max": ch.NAME_MAX,
        "handle_min": ch.HANDLE_MIN,
        "handle_max": ch.HANDLE_MAX,
        "description_max": ch.DESCRIPTION_MAX,
        "keywords_max": ch.KEYWORDS_MAX,
        "banner_size": ch.BANNER_SIZE,
        "banner_safe_area": ch.BANNER_SAFE_AREA,
        "avatar_size": ch.AVATAR_SIZE,
        "manual_steps": ch.MANUAL_STEPS,
        "note": (
            "YouTube has no API for creating a channel. Everything here is designed "
            "and validated automatically; the manual steps are the ones no API can "
            "perform."
        ),
    }


#: Strong references to running launch tasks. `asyncio.create_task` alone lets the
#: event loop garbage-collect a task nothing holds, killing a launch mid-stage.
_TASKS: dict[str, asyncio.Task] = {}


async def _run_launch(launch_id: str, body: LaunchRequest) -> None:
    record = LAUNCHES[launch_id]

    async def emit(event: dict) -> None:
        record["events"].append(event)
        # Persist on stage boundaries, not on every progress frame — a launch is
        # seven stages, so this is seven small writes, and a crash loses at most
        # the stage that was running.
        if event.get("type", "").startswith(("stage.completed", "stage.failed", "workflow.")):
            await _save(launch_id)

    try:
        await LAUNCH_WORKFLOW.run(
            job_id=launch_id,
            inputs={"niche": body.niche, **body.model_dump()},
            emit=emit,
            states=record["states"],
            budget_usd=2.0,
        )
        record["status"] = "completed"
    except Exception as exc:  # noqa: BLE001 — the status carries the outcome; nothing to re-raise into
        record["status"] = "failed"
        record["error"] = str(exc)
        logger.warning("launch {} failed: {}", launch_id, exc)
    finally:
        await _save(launch_id)
        _TASKS.pop(launch_id, None)


@router.post("/launch", status_code=202)
async def launch(body: LaunchRequest) -> LaunchOut:
    """Start designing a channel. Returns immediately; poll `GET /launch/{id}`.

    This used to run the whole seven-stage LLM chain inside the request, which
    meant a 202 that actually blocked for minutes — past every sane client
    timeout, with no way to show progress. Now it runs like a job: the record is
    visible at once, each finished stage is persisted, and the screen polls.
    """
    launch_id = uuid.uuid4().hex[:12]
    LAUNCHES[launch_id] = {
        "id": launch_id,
        "niche": body.niche,
        "states": LAUNCH_WORKFLOW.initial_states(),
        "events": [],
        "status": "running",
        "inputs": body.model_dump(),
    }
    await _save(launch_id)
    _TASKS[launch_id] = asyncio.create_task(_run_launch(launch_id, body))
    return await get_launch(launch_id)


@router.get("/launches")
async def list_launches() -> list[LaunchSummary]:
    """Every stored launch design, newest first, as one-line summaries.

    What lets the New channel screen resume a design after a reload — the manual
    steps take days, and until launches were persisted the screen could only ever
    show the design it had just generated.
    """
    out = []
    for record in LAUNCHES.values():
        states = record["states"]
        done = sum(1 for s in states.values() if s.status.value == "done")
        out.append(
            LaunchSummary(
                id=record["id"],
                niche=record["niche"],
                status=record["status"],
                stages_done=done,
                stages_total=len(states),
            )
        )
    return list(reversed(out))


@router.get("/launch/{launch_id}")
async def get_launch(launch_id: str) -> LaunchOut:
    record = LAUNCHES.get(launch_id)
    if record is None:
        raise HTTPException(404, "unknown launch")

    states = record["states"]
    identity = assemble(states) if record["status"] == "completed" else None
    problems = ch.validate(identity) if identity else []

    def output(name: str):
        state = states.get(name)
        return state.output.value if state and state.output else None

    backlog = output("backlog") or []
    from engine.workflows.base import summarize

    stages = []
    for stage in LAUNCH_WORKFLOW.stages:
        state = states.get(stage.name)
        stages.append(
            {
                "name": stage.name,
                "title": stage.title,
                "status": state.status.value if state else "pending",
                "summary": (
                    summarize(state.output.value) if state and state.output is not None else ""
                ),
                "error": state.error if state else None,
            }
        )

    return {
        "id": launch_id,
        "status": record["status"],
        "error": record.get("error"),
        "stages": stages,
        "niche": record["niche"],
        "identity": (
            {
                "name": identity.name,
                "handle": identity.handle,
                "tagline": identity.tagline,
                "description": identity.description,
                "keywords": identity.keywords,
                "keywords_string": identity.keywords_string(),
                "avatar_concept": identity.avatar_concept,
                "banner_concept": identity.banner_concept,
                "palette": identity.palette,
            }
            if identity
            else None
        ),
        "positioning": output("positioning"),
        "name_options": output("naming"),
        "visuals": output("visuals"),
        "series": output("series"),
        "backlog": [
            {
                "topic": i.topic,
                "score": i.score,
                "duplicate_of": i.duplicate_of,
            }
            for i in backlog
        ],
        "problems": [{"field": p.field, "message": p.message, "fatal": p.fatal} for p in problems],
        "blocked": any(p.fatal for p in problems),
        "manual_steps": ch.MANUAL_STEPS,
        "cost_usd": round(sum(s.output.cost_usd for s in states.values() if s.output), 4),
    }


@router.post("/launch/apply")
async def apply(body: ApplyRequest) -> dict:
    """Push what the API can actually set onto a connected channel.

    Only the description, keywords and country. The name and handle are not settable
    through the Data API at all — those stay on the manual checklist permanently.
    """
    record = LAUNCHES.get(body.launch_id)
    if record is None:
        raise HTTPException(404, "unknown launch")
    if not body.confirm_channel_created:
        raise HTTPException(
            409,
            "create the channel in YouTube Studio first — there is no API for it. "
            "Then re-send with confirm_channel_created=true.",
        )

    creds = CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected; complete the OAuth step first")

    identity = assemble(record["states"])
    fatal = [p for p in ch.validate(identity) if p.fatal]
    if fatal:
        raise HTTPException(400, "; ".join(p.message for p in fatal))

    client = YouTube(creds)
    await client._call(  # noqa: SLF001 — branding has no dedicated helper yet
        "PUT",
        "https://www.googleapis.com/youtube/v3/channels",
        "videos.update",
        params={"part": "brandingSettings"},
        json={"id": creds.channel_id, **ch.branding_payload(identity)},
    )

    return {
        "applied": ["description", "keywords", "country"],
        "still_manual": ["name", "handle", "avatar", "banner"],
    }
