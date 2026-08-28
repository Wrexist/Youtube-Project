"""The Repurpose tab's endpoints.

Shaped around the one thing the screen is actually for: deciding whether a clip may
be used, and recording the answer. Discovery ranks candidates, but a ranked list of
clips nobody may touch is a browsing toy — the grant endpoints are the product.

`POST /grant` is the only write that matters. Everything downstream keys off it:
media cannot be stored without one (`repository.record_asset` refuses), and the
gate reads it back to decide whether the finished video may publish.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from engine import repository
from engine.auth import require_token
from engine.providers import tiktok
from engine.repurpose.gate import Corpus, Timeline, TimelineSegment, evaluate
from engine.repurpose.rights import Grant, Lane

router = APIRouter(prefix="/v1/repurpose", tags=["repurpose"])
#: Every route here is gated except `/auth/tiktok/callback` — TikTok's server
#: redirects a browser straight into that one, carrying no header we asked for and
#: no token we could check. See `main.py`'s comment above `app.include_router` for
#: the full reasoning; `tiktok_callback` below restates it at the point that
#: matters.
_gated = [Depends(require_token)]


class GrantIn(BaseModel):
    """Authority to use a clip, as the rights panel submits it."""

    lane: Lane
    grantor: str = ""
    evidence_kind: str = ""
    evidence_ref: str = ""
    expires_at: datetime | None = None
    platforms: list[str] = Field(default_factory=list)
    rules: str = ""

    def to_grant(self) -> Grant:
        return Grant(
            lane=self.lane,
            grantor=self.grantor.strip(),
            evidence_kind=self.evidence_kind.strip(),
            evidence_ref=self.evidence_ref.strip(),
            granted_at=datetime.now(UTC),
            expires_at=self.expires_at,
            platforms=frozenset(p.lower() for p in self.platforms),
            rules=self.rules,
        )


class ProblemOut(BaseModel):
    code: str
    message: str
    fatal: bool


class GrantOut(BaseModel):
    """A stored grant, with what is wrong with it already worked out.

    The problems travel with the grant so the rights panel never has to decide for
    itself what a lapsed licence means — that judgement lives in one place.
    """

    id: int | None
    lane: str
    grantor: str
    evidence_kind: str
    evidence_ref: str
    granted_at: str | None
    expires_at: str | None
    revoked_at: str | None
    platforms: list[str]
    rules: str
    needs_attribution: bool
    cleared: bool
    problems: list[ProblemOut]


class ClipOut(BaseModel):
    id: str
    platform: str
    external_id: str
    url: str
    creator_handle: str
    caption: str
    hashtags: list[str]
    stats: dict
    duration_s: float
    fit_score: float
    fit_reasons: list[str]
    status: str
    #: None when the clip has never had a grant — which is most of them, and is the
    #: state that disables the build button rather than an error.
    grant: GrantOut | None
    cleared: bool
    acquired: bool


class Clips(BaseModel):
    clips: list[ClipOut]


class SegmentIn(BaseModel):
    start_s: float
    end_s: float
    source_id: str | None = None
    narrated: bool = False
    annotated: bool = False


class TimelineIn(BaseModel):
    segments: list[SegmentIn] = Field(default_factory=list)
    cuts: int = 0
    audio_bed_replaced: bool = False
    watermarked_sources: list[str] = Field(default_factory=list)
    attribution_on_screen: bool = False
    attribution_in_description: bool = False
    is_compilation: bool = False
    #: Corpus context, when the caller has it. Absent means the repetition checks
    #: report that they did not run, which is not the same as passing them.
    max_similarity: float = 0.0
    template_repeats: int = 0
    structure_repeats: int = 0
    compared_against: int = 0


class TikTokAccountOut(BaseModel):
    """A connected account, as the Setup screen reads it.

    Carries no credential. The refresh token is never returned by any endpoint —
    a status read has no business handling one.
    """

    key: str
    open_id: str
    handle: str
    expires_at: str | None
    refresh_expires_at: str | None
    scope: str
    connected: bool


class TikTokStatusOut(BaseModel):
    """Configured and connected are separate answers.

    An install can have both keys and nobody signed in, and the fix differs: one
    is a `.env` edit, the other is a button. Declared rather than returned as a
    bare `dict` for the reason `ReportOut` records — `-> dict` generates
    `Record<string, never>` in TypeScript, which pushes the screen into
    hand-writing the shape CLAUDE.md forbids.
    """

    configured: bool
    account: TikTokAccountOut | None


class SignalOut(BaseModel):
    name: str
    severity: str
    message: str
    value: float | None
    threshold: float | None


class RightsVerdictOut(BaseModel):
    cleared: bool
    ungranted: list[str]
    problems: dict[str, list[ProblemOut]]


class TransformationVerdictOut(BaseModel):
    passed: bool
    signals: list[SignalOut]


class ReportOut(BaseModel):
    """Both verdicts, never blended.

    Declared rather than returned as a bare `dict`: the response model is what
    `packages/contracts` generates from, and an endpoint typed `-> dict` produces a
    TypeScript type of `Record<string, never>` — which is worse than no type,
    because the screen then hand-writes the shape it expects and CLAUDE.md forbids
    exactly that.
    """

    publishable: bool
    headline: str
    thresholds_version: int
    rights: RightsVerdictOut
    transformation: TransformationVerdictOut


def _grant_out(grant: Grant | None, *, platform: str = "youtube") -> GrantOut | None:
    if grant is None:
        return None
    problems = grant.problems(platform=platform)
    return GrantOut(
        id=None,
        **grant.as_dict(),
        cleared=not any(p.fatal for p in problems),
        problems=[ProblemOut(code=p.code, message=p.message, fatal=p.fatal) for p in problems],
    )


@router.get("/clips", dependencies=_gated)
async def clips(
    channel_key: str = "",
    status: str = "discovered",
    limit: int = Query(50, ge=1, le=200),
) -> Clips:
    """Discovered clips for a channel, best fit first.

    Each carries its grant, because the rights chip is what decides whether the
    card is usable at all and a second round trip per card to find that out would
    make the grid useless.
    """
    rows = await repository.clip_sources(channel_key=channel_key, status=status, limit=limit)
    out = []
    for row in rows:
        grant_payload = row.pop("grant", None)
        grant = None
        if grant_payload:
            grant = await repository.latest_grant(row["id"])
        out.append(ClipOut(**row, grant=_grant_out(grant)))
    return Clips(clips=out)


@router.post("/clips/{source_id}/grant", dependencies=_gated)
async def record_grant(source_id: str, body: GrantIn) -> GrantOut:
    """Record how a clip may be used.

    Refuses to store a grant that is already invalid — a lane with no grantor, a
    licence with no evidence. Catching it here rather than at build time means the
    operator finds out while they still have the DM open, instead of forty minutes
    into a render.
    """
    grant = body.to_grant()
    problems = grant.problems()
    if any(p.fatal for p in problems):
        raise HTTPException(
            422,
            {
                "message": "this grant is not usable as recorded",
                "problems": [{"code": p.code, "message": p.message} for p in problems if p.fatal],
            },
        )

    try:
        grant_id = await repository.record_grant(source_id, grant)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    out = _grant_out(grant)
    assert out is not None  # a grant was just constructed
    out.id = grant_id
    return out


@router.post("/clips/{source_id}/dismiss", status_code=204, dependencies=_gated)
async def dismiss(source_id: str) -> None:
    """Refuse a clip, durably.

    Kept rather than deleted: discovery re-runs on the same trend data and would
    cheerfully re-propose it tomorrow.
    """
    if not await repository.set_clip_status(source_id, "dismissed"):
        raise HTTPException(404, f"no clip {source_id}")


@router.post("/clips/{source_id}/select", status_code=204, dependencies=_gated)
async def select(source_id: str) -> None:
    if not await repository.set_clip_status(source_id, "selected"):
        raise HTTPException(404, f"no clip {source_id}")


class DiscoverRequest(BaseModel):
    """Sweep Lane A for clips worth building from.

    No `access_token` field, deliberately. It used to take one in the body, which
    made the caller responsible for a credential that expires every 24 hours —
    so the obvious client caches it and the sweep starts failing the next day for
    a reason invisible from the outside. The token now comes from the stored
    account and is refreshed on the way out.
    """

    channel_key: str = "main"
    #: Up to 200; the provider pages in 20s. Higher than TikTok's own page size on
    #: purpose — the point of pagination is that a sweep is not capped at one page.
    limit: int = Field(default=40, ge=1, le=200)


class Discovered(BaseModel):
    clips: list[ClipOut]
    #: What the scoring compared against. Empty means the channel has no history,
    #: which is reported rather than quietly scoring every clip identically.
    based_on: list[str]
    configured: bool
    #: Whether an account is connected. False with `configured` true means the
    #: credentials are set but nobody has signed in — a different fix from either
    #: "not configured" or "no clips", and the screen says which.
    connected: bool = False


@router.post("/discover", dependencies=_gated)
async def discover(body: DiscoverRequest) -> Discovered:
    """Lane A: sweep the operator's own TikToks, score them for this channel.

    **Only their own.** TikTok's Display API returns the authenticated user's
    content and nothing else, and the Research API is closed to non-academics, so
    there is no endpoint here that sweeps other creators — see
    `providers/tiktok.py`. Lane B material does not arrive this way at all: a
    campaign supplies its own source and its own rules.

    Three distinct not-working states, reported distinctly because they have three
    different fixes: credentials unset, nobody signed in, and a connection that has
    expired. Collapsing them into an empty list is how "it shows nothing" becomes
    unanswerable.
    """
    from engine.repurpose import discover as discovery

    topics = _channel_topics()

    if not tiktok.configured():
        return Discovered(clips=[], based_on=topics, configured=False, connected=False)

    try:
        access_token = await repository.tiktok_access_token()
    except tiktok.TikTokAuthExpired:
        # Not an error response: the screen renders this as "connect your account",
        # and a 4xx would make an ordinary un-connected install look broken.
        return Discovered(clips=[], based_on=topics, configured=True, connected=False)
    except tiktok.TikTokUnavailable as exc:
        # Caught separately, and *after* the subclass above: acquiring the token is
        # itself a call to TikTok, so a 429 or a 5xx while refreshing lands here.
        # Without this it escaped as an unhandled 500 — the same outage reported as
        # a 502 with a sentence when it happens one line later, inside the sweep.
        raise HTTPException(502, str(exc)) from exc

    try:
        await discovery.discover_own(
            access_token,
            channel_key=body.channel_key,
            channel_topics=topics,
            limit=body.limit,
        )
    except tiktok.TikTokAuthExpired as exc:
        # The token was live a moment ago and TikTok refused it anyway — a revoked
        # grant, usually. Surfaced as a 409 with the way out, matching how a dead
        # YouTube refresh token is handled.
        raise HTTPException(
            409, {"detail": str(exc), "reconnect_at": "/v1/repurpose/auth/tiktok"}
        ) from exc
    except tiktok.TikTokUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc

    rows = await repository.clip_sources(channel_key=body.channel_key, limit=body.limit)
    out = []
    for row in rows:
        row.pop("grant", None)
        grant = await repository.latest_grant(row["id"])
        out.append(ClipOut(**row, grant=_grant_out(grant)))
    return Discovered(clips=out, based_on=topics, configured=True, connected=True)


@router.get("/auth/tiktok", dependencies=_gated)
async def begin_tiktok_auth(redirect_uri: str = "") -> dict:
    """Where the browser goes to connect a TikTok account for Lane A.

    Returns the URL rather than redirecting, for the reason `beginYouTubeAuth`
    already documents: a server following the redirect would authorise the server
    rather than the person sitting in front of it.

    `state` is remembered and checked on the way back. Without that the callback
    accepts a code from anywhere, which is the standard OAuth CSRF: an attacker
    walks a victim through a link that connects the *attacker's* TikTok to the
    victim's install, and every clip swept afterwards is the attacker's.
    """
    if not tiktok.configured():
        raise HTTPException(
            409,
            "TikTok is not configured. Set TIKTOK_CLIENT_KEY and "
            "TIKTOK_CLIENT_SECRET in .env, then restart the engine.",
        )

    redirect_uri = redirect_uri or _default_redirect()
    state = secrets.token_urlsafe(24)
    _PENDING_STATES[state] = (datetime.now(UTC), redirect_uri)
    _expire_states()
    return {"url": tiktok.authorize_url(redirect_uri, state)}


@router.get("/auth/tiktok/callback")  # no [Depends] — see the module comment above
async def tiktok_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse:
    """Where TikTok sends the browser back.

    Always redirects to the Setup screen rather than returning JSON: the thing at
    the other end of this is a browser tab a person is looking at, and a page of
    JSON is not an answer to "did that work". The outcome rides in the query
    string so the screen can say which.
    """
    if error:
        return _back(f"tiktok_error={quote(error_description or error)}")

    pending = _PENDING_STATES.pop(state, None)
    _expire_states()
    if pending is None:
        # Unknown or already-used state. Refused rather than accepted, because a
        # code arriving without one is exactly the CSRF the state exists to stop.
        return _back("tiktok_error=" + quote("that sign-in link has expired — try again"))

    _, redirect_uri = pending
    if not code:
        return _back("tiktok_error=" + quote("TikTok returned no authorisation code"))

    try:
        tokens = await tiktok.exchange_code(code, redirect_uri)
        handle = await tiktok.creator_handle(tokens.access_token)
        await repository.save_tiktok_account(tokens, handle=handle)
    except tiktok.TikTokUnavailable as exc:
        return _back("tiktok_error=" + quote(str(exc)))

    return _back("tiktok=connected")


@router.get("/auth/tiktok/status", dependencies=_gated)
async def tiktok_status() -> TikTokStatusOut:
    """Whether an account is connected, without touching a credential.

    `load_tiktok_account` deliberately does not return the refresh token — a
    status endpoint has no business handling one.
    """
    account = await repository.load_tiktok_account()
    return TikTokStatusOut(
        configured=tiktok.configured(),
        account=TikTokAccountOut(**account) if account else None,
    )


@router.delete("/auth/tiktok", status_code=204, dependencies=_gated)
async def disconnect_tiktok() -> None:
    if not await repository.disconnect_tiktok():
        raise HTTPException(404, "no TikTok account is connected")


#: In-flight OAuth states, with the redirect URI each was issued for.
#:
#: In-process rather than a table: they live for one round trip measured in
#: seconds, an engine restart mid-sign-in is a retry rather than a data loss, and
#: a table would need its own sweeper. Bounded by `_expire_states` so a stream of
#: abandoned sign-ins cannot grow it without limit.
_PENDING_STATES: dict[str, tuple[datetime, str]] = {}
_STATE_TTL = timedelta(minutes=10)


def _expire_states() -> None:
    cutoff = datetime.now(UTC) - _STATE_TTL
    for key in [k for k, (issued, _) in _PENDING_STATES.items() if issued < cutoff]:
        _PENDING_STATES.pop(key, None)


def _default_redirect() -> str:
    from engine.settings import get_settings

    settings = get_settings()
    base = str(settings.google_redirect_uri).split("/v1/")[0] or "http://localhost:8080"
    return f"{base}/v1/repurpose/auth/tiktok/callback"


def _back(query: str) -> RedirectResponse:
    from engine.settings import get_settings

    return RedirectResponse(f"{get_settings().web_url}/setup?{query}", status_code=303)


def _channel_topics(count: int = 12) -> list[str]:
    """What this channel has already published, for adjacency scoring.

    Imported inside the function, like `api/ideas.py::_recent_topics` and for the
    same reason: `engine.main` imports this router at module level, so a
    module-level import back would be a cycle.
    """
    from engine.main import JOBS

    seen: list[str] = []
    for job in reversed(list(JOBS.values())):
        topic = str(job.get("inputs", {}).get("topic", "")).strip()
        if topic and topic not in seen:
            seen.append(topic)
        if len(seen) >= count:
            break
    return seen


@router.post("/evaluate", dependencies=_gated)
async def evaluate_timeline(body: TimelineIn) -> ReportOut:
    """Score a proposed edit against both gates, before building it.

    Cheap and side-effect free on purpose. The same evaluation runs as a stage in
    the workflow, but an operator assembling an episode should be able to see
    "62% authored, longest lift 11s" *while* they drag segments around — finding
    out after a render that the edit was never going to pass is the failure this
    endpoint exists to prevent.
    """
    timeline = Timeline(
        segments=tuple(
            TimelineSegment(
                start_s=s.start_s,
                end_s=s.end_s,
                source_id=s.source_id,
                narrated=s.narrated,
                annotated=s.annotated,
            )
            for s in body.segments
        ),
        cuts=body.cuts,
        audio_bed_replaced=body.audio_bed_replaced,
        watermarked_sources=tuple(body.watermarked_sources),
        attribution_on_screen=body.attribution_on_screen,
        attribution_in_description=body.attribution_in_description,
        is_compilation=body.is_compilation,
    )
    source_ids = sorted({s.source_id for s in body.segments if s.source_id})
    grants = await repository.grants_for(source_ids)
    corpus = Corpus(
        max_similarity=body.max_similarity,
        template_repeats=body.template_repeats,
        structure_repeats=body.structure_repeats,
        compared_against=body.compared_against,
    )
    return ReportOut(**evaluate(timeline, grants, corpus=corpus).as_dict())
