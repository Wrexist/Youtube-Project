"""Auth, calendar, and scheduling endpoints."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from loguru import logger
from pydantic import AwareDatetime, BaseModel, Field

from engine import repository
from engine.providers import youtube
from engine.quota import ledger, quota_day
from engine.scheduling import (
    AudienceProfile,
    Constraints,
    Pending,
    auto_schedule,
    candidate_slots,
    validate_move,
)
from engine.settings import get_settings
from engine.workflows.base import WorkflowError

router = APIRouter(prefix="/v1", tags=["publishing"])

# In-process mirrors of the `channels` and `schedule` tables, hydrated by the
# lifespan handler and written through on every mutation. Reads stay synchronous
# and local; the row is the durable record.
CHANNELS: dict[str, youtube.Credentials] = {}
SCHEDULE: dict[str, datetime] = {}  # video_id -> publish time

# Deliberately *not* persisted: an in-flight OAuth state is meaningless after a
# restart, and keeping it would widen the window for a replayed callback.
#
# Bounded and expiring, because it used to be a plain set that only ever grew:
# `GET /v1/auth/google` needs no credentials, so anything that could reach the
# engine could add entries until the process ran out of memory. An OAuth round trip
# is a browser redirect, so ten minutes is generous.
_STATES: dict[str, float] = {}
_STATE_TTL_S = 600.0
_MAX_STATES = 64


def _remember_state(state: str) -> None:
    now = time.monotonic()
    for key, born in list(_STATES.items()):
        if now - born > _STATE_TTL_S:
            del _STATES[key]
    # Still full of live entries: drop the oldest rather than refuse to start an
    # auth the user is standing in front of.
    while len(_STATES) >= _MAX_STATES:
        del _STATES[min(_STATES, key=_STATES.get)]  # type: ignore[arg-type]
    _STATES[state] = now


def _claim_state(state: str) -> bool:
    """Single-use, and only if it has not expired."""
    born = _STATES.pop(state, None)
    return born is not None and time.monotonic() - born <= _STATE_TTL_S


# ── the connected channel, for code that is not an endpoint ─────────────────


async def credentials_for(key: str = "default") -> youtube.Credentials | None:
    """A channel's credentials, from the mirror or — failing that — from the row.

    The arq worker never runs the API's lifespan, so `CHANNELS` in that process is
    empty forever and reading it alone would report every channel disconnected.
    The API has the mirror already and must not pay for a query per job, hence the
    order.
    """
    creds = CHANNELS.get(key)
    if creds is not None:
        return creds
    if not get_settings().persist:
        return None
    try:
        loaded = await repository.load_channels()
    except Exception:  # noqa: BLE001 — an unreadable channel is "not connected"
        logger.exception("could not load channel {!r}", key)
        return None
    # `setdefault`, not `update`. The rows carry `access_token=""` by design — it is
    # not persisted — so a blanket update would replace a *different* channel's live,
    # refreshed credentials with a blank-token copy and force a needless `refresh()`
    # on its next publish. Only fill in what the mirror is missing.
    for name, value in loaded.items():
        CHANNELS.setdefault(name, value)
    return CHANNELS.get(key)


async def attach_youtube_client(job: dict) -> None:
    """Give a publish job that is about to run a live YouTube client.

    `repository.jsonable` strips `youtube_client` out of the inputs before they are
    stored — it holds an access token, which belongs in neither a row nor an HTTP
    response — so a publish job that comes back from its row, or is handed to the
    worker (which only ever receives the id), has no client at all. Every publish
    stage reads `ctx.inputs["youtube_client"]`, and the failure was quiet in the
    worst possible place: `CaptionsStage` is `optional=True`, so the framework
    turned its bare `KeyError` into SKIPPED and the job reported success while the
    captions of an already-live video were never uploaded.

    Rebuilt here — at the moment the workflow starts — rather than in the lifespan
    restore, deliberately. The restored mirror *is* the dict `save_job` serialises;
    putting a live token back into it is one missing filter away from writing an
    access token to the database.

    A disconnected channel raises rather than leaving the key absent, because a
    named failure is the only version of this an optional stage cannot swallow.
    """
    if job["workflow"].name != "publish" or "youtube_client" in job.get("inputs", {}):
        return

    creds = await credentials_for("default")
    if creds is None:
        raise WorkflowError(
            f"publish job {job['id']} cannot resume: no YouTube channel is connected "
            "in this process. Reconnect at /v1/auth/google, then re-run the job."
        )
    job["inputs"]["youtube_client"] = youtube.YouTube(creds)


class QuotaResponse(BaseModel):
    """The daily YouTube budget.

    A response model rather than a bare dict because this is the one payload the
    web app does arithmetic on — without it the generated TypeScript types every
    field as `unknown` and the UI has to cast, which is the hand-written mirror
    `packages/contracts` exists to prevent.
    """

    day: str
    limit: int
    spent: int
    remaining: int
    uploads_left: int
    breakdown: dict[str, int]
    by_day: dict[str, int]


class ScheduledVideo(BaseModel):
    video_id: str
    at: str


class CalendarResponse(BaseModel):
    scheduled: list[ScheduledVideo]
    quota_by_day: dict[str, int]


class ScheduleRequest(BaseModel):
    video_id: str
    #: Aware — this reaches the same `validate_move` comparison as `Assignment.at`.
    at: AwareDatetime


class PendingVideo(BaseModel):
    """One video waiting for a slot.

    Was `dict`, which meant a missing `id` reached `auto_schedule` and came back
    as a 500 with a KeyError — four ordinary malformed payloads all did. Declaring
    the shape turns each of them into a 422 naming the field.
    """

    id: str = Field(min_length=1)
    title: str = ""
    format: Literal["short", "long"] = "short"
    ready_at: AwareDatetime | None = None


class AutoScheduleRequest(BaseModel):
    videos: list[PendingVideo]
    shorts_per_week: int = Field(default=3, ge=0, le=50)
    long_per_week: int = Field(default=1, ge=0, le=50)
    horizon_days: int = Field(default=28, ge=1, le=365)


class Assignment(BaseModel):
    video_id: str = Field(min_length=1)
    #: Aware, and rejected outright if not.
    #:
    #: `scheduling.validate_move` compares `at` against an aware `now` and calls
    #: `astimezone` on it, so a payload without an offset — `"2026-08-01T10:00:00"` —
    #: raised `TypeError: can't compare offset-naive and offset-aware datetimes` and
    #: came back as a 500 on a request that looked perfectly valid.
    #:
    #: Rejecting rather than assuming UTC, deliberately. This field decides the minute
    #: a video goes public; a silent assumption puts it hours out on the operator's
    #: calendar with nothing to indicate why. A 422 naming the field is the honest
    #: answer, and no real client sends naive — `Date.toISOString()` always carries `Z`.
    at: AwareDatetime


class ApplyRequest(BaseModel):
    assignments: list[Assignment]


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
    _remember_state(state)
    return {"url": youtube.authorize_url(state)}


@router.get("/auth/google/callback")
async def finish_auth(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    web = get_settings().web_url.rstrip("/")

    # Google's own refusal, which arrives here as a redirect carrying `error=` and no
    # code. Overwhelmingly `access_denied`, which does not mean the operator declined
    # — it means the Cloud project is still in Testing and the account that just
    # signed in is not one of its test users. That distinction is the whole fix, so
    # it is handed to the Setup screen, where the button they pressed is, rather than
    # rendered as a bare error by the API.
    if error:
        return RedirectResponse(f"{web}/setup?connect_error={quote(error)}")

    # Opened by hand rather than arrived at — someone following the redirect URI out
    # of the setup instructions to see what is there. FastAPI's own answer to the
    # missing query parameters is a validation dump
    # (`{"detail":[{"type":"missing","loc":["query","code"]...`) which reads like a
    # bug in Studio rather than a page that was never meant to be visited.
    if not code or not state:
        return PlainTextResponse(
            "This address is not a page. It is where Google sends you back once you "
            "have approved access, and it needs the parameters Google adds.\n\n"
            f"Start the connection from Connect YouTube on {web}/setup.\n",
            status_code=400,
        )

    # Reject a callback we did not initiate — without this the endpoint accepts a
    # code from anywhere and binds someone else's channel to this install.
    if not _claim_state(state):
        raise HTTPException(400, "unrecognised, expired or reused state")

    try:
        creds = await youtube.exchange_code(code)
    except youtube.YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc

    CHANNELS["default"] = creds
    await repository.save_channel("default", creds)
    # Back to the screen that sent them, not to the calendar. Connecting a channel
    # is the last step of setup, and landing on an unrelated screen left someone
    # with no confirmation that the thing they just did had worked.
    #
    # The origin is configurable because the hardcoded localhost:3000 was wrong for
    # every install that is not the developer's laptop: behind docker compose, on a
    # LAN address, or on any port but 3000, Google returned the operator to a page
    # that does not exist and the connection looked like it had failed.
    return RedirectResponse(f"{get_settings().web_url.rstrip('/')}/setup?connected=1")


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
async def quota() -> QuotaResponse:
    # The worker uploads, so it is the process that spends. Without this the screen
    # shows whatever this process happened to book since it started.
    await ledger.refresh()
    return QuotaResponse(
        day=quota_day().isoformat(),
        limit=ledger.limit,
        spent=ledger.spent(),
        remaining=ledger.remaining(),
        uploads_left=ledger.uploads_left(),
        breakdown=ledger.breakdown(),
        by_day={d.isoformat(): v for d, v in ledger.usage_by_day().items()},
    )


# ── calendar ────────────────────────────────────────────────────────────────


@router.get("/calendar")
async def calendar() -> CalendarResponse:
    await ledger.refresh()
    return CalendarResponse(
        scheduled=[ScheduledVideo(video_id=vid, at=at.isoformat()) for vid, at in SCHEDULE.items()],
        quota_by_day={d.isoformat(): v for d, v in ledger.usage_by_day().items()},
    )


@router.get("/calendar/slots")
async def slots(
    # Bounded, and synchronously so. `candidate_slots` builds every slot in the
    # horizon before ranking: `days=5000` produced 120,000 of them in 0.75s on
    # this machine, so `days=100000` blocks the event loop for tens of seconds —
    # stalling every SSE stream and every in-process render relay in the process.
    # The endpoint returns at most 40 slots, so a longer horizon buys nothing.
    days: int = Query(14, ge=1, le=90),
) -> dict:
    """Ranked publish times, so the calendar can show *why* a slot is good."""
    # Slot ranking refuses days that are already over budget, so it needs the
    # worker's spend as much as the quota screen does.
    await ledger.refresh()
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
    await repository.save_slot(body.video_id, body.at)

    # Cheap (50 units), so rescheduling can be used freely once the video is up.
    creds = CHANNELS.get("default")
    if creds and body.video_id.startswith("yt:"):
        await youtube.YouTube(creds).reschedule(body.video_id[3:], body.at)

    return {"video_id": body.video_id, "at": body.at.isoformat(), "warning": message}


@router.delete("/calendar/schedule/{video_id}")
async def unschedule(video_id: str) -> dict:
    SCHEDULE.pop(video_id, None)
    await repository.delete_slot(video_id)
    return {"video_id": video_id, "scheduled": False}


@router.post("/calendar/auto")
async def auto(body: AutoScheduleRequest) -> dict:
    """Fill the calendar automatically.

    Returns a *plan*, and does not apply it. Scheduling a month of uploads is exactly
    the kind of thing that should be reviewed before it happens.
    """
    await ledger.refresh()
    profile = AudienceProfile()
    plan = auto_schedule(
        # Straight through now that `videos` is typed: the hand-rolled `v["id"]`,
        # `v.get(...)` and `datetime.fromisoformat` were each a 500 waiting for a
        # payload that omitted a key or spelled a date wrong. Pydantic does all
        # three, and reports the field that was wrong instead of the line that
        # raised.
        [
            Pending(id=v.id, title=v.title, format=v.format, ready_at=v.ready_at)
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
async def apply_plan(body: ApplyRequest) -> dict:
    """Book a whole plan, or as much of it as the rules allow.

    Two things were wrong with the old version, and they compounded.

    It **wrote as it went**, so a malformed entry at position three left one and
    two booked and the caller with a 500 — no way to know what had landed short
    of re-reading the calendar. Everything is now checked before the first write,
    so the outcome is all-or-reported, never half-applied-and-unexplained.

    And it **never called `validate_move`**, which `schedule_one` twenty lines
    above does on every manual drag. The same times that endpoint 409s — in the
    past, too close to another upload, over the day's quota — were persisted here
    without complaint, which is how a calendar ends up describing a schedule
    YouTube will refuse.

    Rejected entries come back in `skipped` with a reason rather than failing the
    request: a fourteen-video plan with one bad slot should book thirteen and say
    which one it did not.
    """
    await ledger.refresh()

    planned: list[tuple[str, datetime]] = []
    skipped: list[dict] = []
    #: Permitted, but worth saying — two uploads close enough to split their own
    #: audience. Reported rather than dropped: `validate_move` distinguishes a
    #: block from a warning, and collapsing the two would throw away the only
    #: signal that an accepted plan is still a poor one.
    warnings: list[dict] = []

    # Validated against the schedule *as it is being built*, not just as it was:
    # two assignments an hour apart are both fine against the stored calendar and
    # not fine against each other, and checking only the former is how a plan
    # double-books itself.
    projected = dict(SCHEDULE)

    for assignment in body.assignments:
        ok, message = validate_move(
            assignment.at,
            existing=[t for vid, t in projected.items() if vid != assignment.video_id],
            quota_used_by_day=ledger.usage_by_day(),
        )
        if not ok:
            skipped.append({"video_id": assignment.video_id, "reason": message})
            continue
        if message:
            warnings.append({"video_id": assignment.video_id, "warning": message})
        projected[assignment.video_id] = assignment.at
        planned.append((assignment.video_id, assignment.at))

    for video_id, at in planned:
        SCHEDULE[video_id] = at
        await repository.save_slot(video_id, at)

    return {"applied": len(planned), "skipped": skipped, "warnings": warnings}
