"""Auth, calendar, and scheduling endpoints."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from engine.providers import youtube
from engine.quota import DAILY_LIMIT, ledger, quota_day
from engine.scheduling import (
    AudienceProfile,
    Constraints,
    Pending,
    auto_schedule,
    candidate_slots,
    validate_move,
)
from engine.settings import get_settings

router = APIRouter(prefix="/v1", tags=["publishing"])

# Channel store and OAuth state. Postgres-backed in Phase 1; the shape is stable.
CHANNELS: dict[str, youtube.Credentials] = {}
_STATES: set[str] = set()
SCHEDULE: dict[str, datetime] = {}  # video_id -> publish time


class ScheduleRequest(BaseModel):
    video_id: str
    at: datetime


class AutoScheduleRequest(BaseModel):
    videos: list[dict]
    shorts_per_week: int = 3
    long_per_week: int = 1
    horizon_days: int = 28


# ── OAuth ───────────────────────────────────────────────────────────────────


@router.get("/auth/google")
async def begin_auth() -> dict:
    # Without credentials this used to hand back a URL with an empty client_id, and
    # the operator met Google's "invalid client" page with nothing pointing at the
    # cause. Fail here instead, the same way /v1/analytics/* reports no channel.
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", settings.google_client_id),
            ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            409,
            f"{' and '.join(missing)} not set — create an OAuth 2.0 client in Google "
            "Cloud (YouTube Data API v3 + YouTube Analytics API) and add it to .env",
        )

    state = secrets.token_urlsafe(24)
    _STATES.add(state)
    return {"url": youtube.authorize_url(state)}


@router.get("/auth/google/callback")
async def finish_auth(code: str = Query(...), state: str = Query(...)):
    # Reject a callback we did not initiate — without this the endpoint accepts a
    # code from anywhere and binds someone else's channel to this install.
    if state not in _STATES:
        raise HTTPException(400, "unrecognised or reused state")
    _STATES.discard(state)

    try:
        creds = await youtube.exchange_code(code)
    except youtube.YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc

    CHANNELS["default"] = creds
    return RedirectResponse("http://localhost:3000/calendar?connected=1")


@router.get("/channels")
async def list_channels() -> dict:
    # Note the absence of tokens in this payload. That is deliberate and permanent.
    return {
        "channels": [
            {"key": key, "channel_id": c.channel_id, "connected": True}
            for key, c in CHANNELS.items()
        ]
    }


# ── quota ───────────────────────────────────────────────────────────────────


@router.get("/quota")
async def quota() -> dict:
    return {
        "day": quota_day().isoformat(),
        "limit": DAILY_LIMIT,
        "spent": ledger.spent(),
        "remaining": ledger.remaining(),
        "uploads_left": ledger.uploads_left(),
        "breakdown": ledger.breakdown(),
        "by_day": {d.isoformat(): v for d, v in ledger.usage_by_day().items()},
    }


# ── calendar ────────────────────────────────────────────────────────────────


@router.get("/calendar")
async def calendar() -> dict:
    return {
        "scheduled": [{"video_id": vid, "at": at.isoformat()} for vid, at in SCHEDULE.items()],
        "quota_by_day": {d.isoformat(): v for d, v in ledger.usage_by_day().items()},
    }


@router.get("/calendar/slots")
async def slots(days: int = 14) -> dict:
    """Ranked publish times, so the calendar can show *why* a slot is good."""
    profile = AudienceProfile()
    ranked = candidate_slots(datetime.now(UTC), days, profile)[:40]
    return {
        "source": profile.source,
        "measured": profile.is_measured,
        "slots": [{"at": s.at.isoformat(), "score": s.score, "reason": s.reason} for s in ranked],
    }


@router.post("/calendar/schedule")
async def schedule_one(body: ScheduleRequest) -> dict:
    """A manual drag. Validated, and warned about even when permitted."""
    ok, message = validate_move(
        body.at,
        existing=[t for vid, t in SCHEDULE.items() if vid != body.video_id],
        quota_used_by_day=ledger.usage_by_day(),
    )
    if not ok:
        raise HTTPException(409, message)

    SCHEDULE[body.video_id] = body.at

    # Cheap (50 units), so rescheduling can be used freely once the video is up.
    creds = CHANNELS.get("default")
    if creds and body.video_id.startswith("yt:"):
        await youtube.YouTube(creds).reschedule(body.video_id[3:], body.at)

    return {"video_id": body.video_id, "at": body.at.isoformat(), "warning": message}


@router.delete("/calendar/schedule/{video_id}")
async def unschedule(video_id: str) -> dict:
    SCHEDULE.pop(video_id, None)
    return {"video_id": video_id, "scheduled": False}


@router.post("/calendar/auto")
async def auto(body: AutoScheduleRequest) -> dict:
    """Fill the calendar automatically.

    Returns a *plan*, and does not apply it. Scheduling a month of uploads is exactly
    the kind of thing that should be reviewed before it happens.
    """
    profile = AudienceProfile()
    plan = auto_schedule(
        [
            Pending(
                id=v["id"],
                title=v.get("title", ""),
                format=v.get("format", "short"),
                ready_at=(datetime.fromisoformat(v["ready_at"]) if v.get("ready_at") else None),
            )
            for v in body.videos
        ],
        start=datetime.now(UTC),
        profile=profile,
        constraints=Constraints(
            shorts_per_week=body.shorts_per_week,
            long_per_week=body.long_per_week,
            quota_used_by_day=ledger.usage_by_day(),
        ),
        existing=list(SCHEDULE.values()),
        horizon_days=body.horizon_days,
    )

    return {
        "source": profile.source,
        "measured": profile.is_measured,
        "assignments": [
            {
                "video_id": a.video_id,
                "at": a.at.isoformat(),
                "score": a.score,
                "reason": a.reason,
            }
            for a in plan.assignments
        ],
        "unplaced": [{"video_id": v, "reason": r} for v, r in plan.unplaced],
    }


@router.post("/calendar/auto/apply")
async def apply_plan(body: dict) -> dict:
    for assignment in body.get("assignments", []):
        SCHEDULE[assignment["video_id"]] = datetime.fromisoformat(assignment["at"])
    return {"applied": len(body.get("assignments", []))}
