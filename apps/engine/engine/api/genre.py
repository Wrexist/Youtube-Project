"""Genre intelligence endpoints — watchlist, sweeps, patterns, gaps.

Read-only mining of public metadata about channels the operator chose to
watch. Nothing here touches competitor media; the rights system that governs
footage (`repurpose.rights`) has no interaction with this module, by design.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine import genre
from engine.api.publishing import CHANNELS

router = APIRouter(prefix="/v1/genre", tags=["genre"])


# ── response models ──────────────────────────────────────────────────────────
#
# Typed on purpose: everything this router returns lands in the generated web
# contract, and a bare dict there is a type error waiting to be discovered in
# the browser console instead of at build time.


class WatchedChannelOut(BaseModel):
    youtube_channel_id: str
    label: str
    note: str
    active: bool
    last_synced_at: str | None = None
    last_error: str = ""
    video_count: int = 0
    created_at: str


class WatchlistResponse(BaseModel):
    channels: list[WatchedChannelOut]


class AddedChannel(BaseModel):
    channel: WatchedChannelOut


class Removed(BaseModel):
    removed: bool


class ToggleResult(BaseModel):
    youtube_channel_id: str
    active: bool


class SyncReport(BaseModel):
    channel_id: str
    ok: bool
    videos_seen: int
    new_videos: int
    error: str


class SyncResponse(BaseModel):
    channels_synced: int
    failures: int
    videos_new: int
    reports: list[SyncReport]


class HookPatternOut(BaseModel):
    pattern: str
    count: int
    share: float
    median_views: float | None = None
    median_views_per_day: float | None = None


class VelocityTitleOut(BaseModel):
    title: str
    channel_label: str
    views: int
    views_per_day: float


class PatternsReport(BaseModel):
    """What `genre.patterns.analyze` computed over the watched corpus."""

    video_count: int
    hook_patterns: list[HookPatternOut]
    median_duration_s: float | None = None
    duration_buckets: dict[str, int] = {}
    uploads_per_week: float | None = None
    top_by_velocity: list[VelocityTitleOut]


class WatchRequest(BaseModel):
    """Add a channel to the watchlist — by id, or by @handle when a YouTube
    credential is connected to resolve it (one quota unit)."""

    channel_id: str | None = None
    handle: str | None = None
    label: str = ""
    note: str = ""


class WatchToggle(BaseModel):
    active: bool


class GapRequest(BaseModel):
    topics: list[str] = Field(min_length=1, max_length=12)


class GapOut(BaseModel):
    topic: str
    autocomplete_matches: int
    watched_videos_on_topic: int
    gap: float


def _default_client():
    creds = CHANNELS.get("default")
    if not creds:
        return None
    from engine.providers.youtube import YouTube

    return YouTube(creds)


@router.get("/watchlist", response_model=WatchlistResponse)
async def watchlist() -> WatchlistResponse:
    """Every watched channel with its corpus size."""
    from engine import repository

    return WatchlistResponse(
        channels=[WatchedChannelOut(**c) for c in await repository.list_watched_channels()]
    )


@router.post("/watchlist", response_model=AddedChannel)
async def add_to_watchlist(req: WatchRequest) -> AddedChannel:
    """Watch a channel. Accepts an id or an @handle (resolved via the Data API
    for one unit when a channel is connected; without one, id only)."""
    from engine import repository

    channel_id = req.channel_id
    label = req.label
    if not channel_id and req.handle:
        yt = _default_client()
        if yt is None:
            raise HTTPException(
                400,
                "no YouTube channel connected: add channels by id, or connect "
                "a channel first and add them by @handle",
            )
        item = await yt.channel_by_handle(req.handle)
        if not item:
            raise HTTPException(404, f"no channel found for @{req.handle}")
        channel_id = item.get("id", "")
        if not label:
            label = item.get("snippet", {}).get("title", "")
    if not channel_id:
        raise HTTPException(400, "channel_id or handle is required")

    await repository.add_watched_channel(channel_id, label=label, note=req.note)
    channels = await repository.list_watched_channels()
    row = next((c for c in channels if c["youtube_channel_id"] == channel_id), None)
    if row is None:
        raise HTTPException(500, "channel was added but could not be read back")
    return AddedChannel(channel=WatchedChannelOut(**row))


@router.delete("/watchlist/{youtube_channel_id}", response_model=Removed)
async def remove_from_watchlist(youtube_channel_id: str) -> Removed:
    from engine import repository

    removed = await repository.remove_watched_channel(youtube_channel_id)
    if not removed:
        raise HTTPException(404, "not on the watchlist")
    return Removed(removed=True)


@router.patch("/watchlist/{youtube_channel_id}", response_model=ToggleResult)
async def toggle_watchlist(youtube_channel_id: str, req: WatchToggle) -> ToggleResult:
    """Pause or resume a channel without losing its mined history."""
    from engine import repository

    existing = await repository.list_watched_channels()
    if not any(c["youtube_channel_id"] == youtube_channel_id for c in existing):
        raise HTTPException(404, "not on the watchlist")
    await repository.set_watched_channel_active(youtube_channel_id, req.active)
    return ToggleResult(youtube_channel_id=youtube_channel_id, active=req.active)


@router.post("/sync", response_model=SyncResponse)
async def sync_watchlist() -> SyncResponse:
    """Sweep every active watched channel (~1 quota unit per channel).

    Per-channel failures are reported inline rather than failing the sweep —
    the response shows exactly which channels are quietly broken.
    """
    reports = await genre.sync.sync_all(_default_client())
    failed = [r for r in reports if not r["ok"]]
    return SyncResponse(
        channels_synced=len(reports),
        failures=len(failed),
        videos_new=sum(r["new_videos"] for r in reports),
        reports=[SyncReport(**r) for r in reports],
    )


@router.get("/patterns", response_model=PatternsReport)
async def patterns() -> PatternsReport:
    """Hook-strategy, duration and cadence aggregates over the mined corpus."""
    from engine import repository

    videos = await repository.watched_videos_for_mining()
    report = genre.patterns.analyze(videos)
    return PatternsReport(
        video_count=report["video_count"],
        hook_patterns=[HookPatternOut(**p) for p in report["hook_patterns"]],
        median_duration_s=report["median_duration_s"],
        duration_buckets=report["duration_buckets"],
        uploads_per_week=report["uploads_per_week"],
        top_by_velocity=[VelocityTitleOut(**t) for t in report["top_by_velocity"]],
    )


@router.post("/gaps", response_model=list[GapOut])
async def gaps(req: GapRequest) -> list[GapOut]:
    """Demand ÷ supply per candidate topic.

    Supply comes from the watchlist corpus — a floor on real competition, not
    a census of YouTube. The screen should say so wherever these numbers
    appear next to idea scores.
    """
    import asyncio

    from engine.research import keywords

    gathered = await asyncio.gather(
        *(keywords.suggest(topic, expand=False) for topic in req.topics),
        return_exceptions=True,
    )
    pooled: list[str] = []
    for result in gathered:
        if isinstance(result, list):
            pooled.extend(result)

    scored = await genre.gaps.score_gaps(req.topics, suggestions=pooled)
    return [GapOut(**row) for row in scored]
