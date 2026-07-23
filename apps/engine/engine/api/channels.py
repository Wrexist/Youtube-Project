"""Channel launch endpoints.

The route names avoid the word "create" deliberately: this designs a channel, it
does not create one. YouTube has no API for that.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine import channel as ch
from engine.api.publishing import CHANNELS
from engine.providers.youtube import YouTube
from engine.workflows.base import Workflow
from engine.workflows.channel_launch import CHANNEL_LAUNCH_STAGES, assemble

router = APIRouter(prefix="/v1/channels", tags=["channels"])

LAUNCH_WORKFLOW = Workflow("channel_launch", CHANNEL_LAUNCH_STAGES)
LAUNCHES: dict[str, dict] = {}


class LaunchRequest(BaseModel):
    niche: str = Field(min_length=3, max_length=200)
    country: str = "US"
    language: str = "en"


class ApplyRequest(BaseModel):
    launch_id: str
    confirm_channel_created: bool = False


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


@router.post("/launch", status_code=202)
async def launch(body: LaunchRequest) -> dict:
    launch_id = uuid.uuid4().hex[:12]
    LAUNCHES[launch_id] = {
        "id": launch_id,
        "niche": body.niche,
        "states": LAUNCH_WORKFLOW.initial_states(),
        "events": [],
        "status": "running",
        "inputs": body.model_dump(),
    }

    async def emit(event: dict) -> None:
        LAUNCHES[launch_id]["events"].append(event)

    try:
        await LAUNCH_WORKFLOW.run(
            job_id=launch_id,
            inputs={"niche": body.niche, **body.model_dump()},
            emit=emit,
            states=LAUNCHES[launch_id]["states"],
            budget_usd=2.0,
        )
        LAUNCHES[launch_id]["status"] = "completed"
    except Exception as exc:  # noqa: BLE001
        LAUNCHES[launch_id]["status"] = "failed"
        raise HTTPException(500, f"launch design failed: {exc}") from exc

    return await get_launch(launch_id)


@router.get("/launch/{launch_id}")
async def get_launch(launch_id: str) -> dict:
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
    return {
        "id": launch_id,
        "status": record["status"],
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
