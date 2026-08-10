"""The Repurpose tab's endpoints.

Shaped around the one thing the screen is actually for: deciding whether a clip may
be used, and recording the answer. Discovery ranks candidates, but a ranked list of
clips nobody may touch is a browsing toy — the grant endpoints are the product.

`POST /grant` is the only write that matters. Everything downstream keys off it:
media cannot be stored without one (`repository.record_asset` refuses), and the
gate reads it back to decide whether the finished video may publish.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from engine import repository
from engine.providers import tiktok
from engine.repurpose.gate import Corpus, Timeline, TimelineSegment, evaluate
from engine.repurpose.rights import Grant, Lane

router = APIRouter(prefix="/v1/repurpose", tags=["repurpose"])


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


@router.get("/clips")
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


@router.post("/clips/{source_id}/grant")
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


@router.post("/clips/{source_id}/dismiss", status_code=204)
async def dismiss(source_id: str) -> None:
    """Refuse a clip, durably.

    Kept rather than deleted: discovery re-runs on the same trend data and would
    cheerfully re-propose it tomorrow.
    """
    if not await repository.set_clip_status(source_id, "dismissed"):
        raise HTTPException(404, f"no clip {source_id}")


@router.post("/clips/{source_id}/select", status_code=204)
async def select(source_id: str) -> None:
    if not await repository.set_clip_status(source_id, "selected"):
        raise HTTPException(404, f"no clip {source_id}")


class DiscoverRequest(BaseModel):
    """Sweep Lane A for clips worth building from.

    `access_token` is passed in rather than read from a store because TikTok
    connection is not yet persisted — see the note on the endpoint. When it is,
    this field goes and the token comes from the channel row, like YouTube's.
    """

    channel_key: str = "main"
    access_token: str = ""
    limit: int = Field(default=20, ge=1, le=20)


class Discovered(BaseModel):
    clips: list[ClipOut]
    #: What the scoring compared against. Empty means the channel has no history,
    #: which is reported rather than quietly scoring every clip identically.
    based_on: list[str]
    configured: bool


@router.post("/discover")
async def discover(body: DiscoverRequest) -> Discovered:
    """Lane A: sweep the operator's own TikToks, score them for this channel.

    **Only their own.** TikTok's Display API returns the authenticated user's
    content and nothing else, and the Research API is closed to non-academics, so
    there is no endpoint here that sweeps other creators — see
    `providers/tiktok.py`. Lane B material does not arrive this way at all: a
    campaign supplies its own source and its own rules.

    Returns `configured: false` rather than erroring when TikTok credentials are
    absent, so the screen can say what is missing instead of showing a failure.
    """
    from engine.repurpose import discover as discovery

    topics = _channel_topics()

    if not tiktok.configured() or not body.access_token:
        return Discovered(clips=[], based_on=topics, configured=tiktok.configured())

    try:
        await discovery.discover_own(
            body.access_token,
            channel_key=body.channel_key,
            channel_topics=topics,
            limit=body.limit,
        )
    except tiktok.TikTokUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc

    rows = await repository.clip_sources(channel_key=body.channel_key, limit=body.limit)
    out = []
    for row in rows:
        row.pop("grant", None)
        grant = await repository.latest_grant(row["id"])
        out.append(ClipOut(**row, grant=_grant_out(grant)))
    return Discovered(clips=out, based_on=topics, configured=True)


@router.get("/auth/tiktok")
async def begin_tiktok_auth(redirect_uri: str) -> dict:
    """Where the browser goes to connect a TikTok account for Lane A.

    Returns the URL rather than redirecting, for the reason `beginYouTubeAuth`
    already documents: a server following the redirect would authorise the server
    rather than the person sitting in front of it.
    """
    import uuid

    if not tiktok.configured():
        raise HTTPException(
            409,
            "TikTok is not configured. Set STUDIO_TIKTOK_CLIENT_KEY and "
            "STUDIO_TIKTOK_CLIENT_SECRET, then restart the engine.",
        )
    return {"url": tiktok.authorize_url(redirect_uri, uuid.uuid4().hex)}


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


@router.post("/evaluate")
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
