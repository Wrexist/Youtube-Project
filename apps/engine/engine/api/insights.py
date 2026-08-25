"""Insight and analytics endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from engine import monetisation as monetisation_progress
from engine import shorts as shorts_from_retention
from engine.api.publishing import CHANNELS
from engine.insights import VideoRecord, analyze, map_retention_to_beats
from engine.providers import youtube
from engine.providers.analytics import Analytics
from engine.review import ReviewPayload
from engine.settings import get_settings

router = APIRouter(prefix="/v1", tags=["insights"])

# Published videos joined to the provenance of what produced them. Postgres-backed
# in Phase 1; the shape is what the attribution needs and does not change.
RECORDS: dict[str, VideoRecord] = {}


async def current_records() -> dict[str, VideoRecord]:
    """The published-video records, re-read from the database when there is one.

    `RECORDS` is a process-local dict that the API filled once at startup. The
    process that *publishes* is the worker, so every video published since this
    process booted was invisible here until someone restarted it — Analytics showed
    a week-old picture and nothing said so.

    Re-reading per request rather than caching with a TTL: the table has one row per
    published video, a channel producing six a day takes years to make this
    interesting, and a stale-but-fast answer is the failure being fixed.
    """
    if not get_settings().persist:
        return RECORDS

    from engine import repository

    try:
        RECORDS.update(await repository.load_performance_records())
    except Exception:  # noqa: BLE001 — a stale view beats a 500 on every screen
        logger.warning("could not refresh performance records; serving what is loaded")
    return RECORDS


def _channel() -> Analytics:
    creds = CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected")
    return Analytics(creds)


class FindingOut(BaseModel):
    """One attribution finding, exactly as `Finding.as_dict` shapes it."""

    dimension: str
    metric: str
    winner: str
    loser: str
    verdict: str
    lift: float
    p_value: float
    n_winner: int
    n_loser: int
    ci95: list[float]
    sentence: str


class InsightsOut(BaseModel):
    findings: list[FindingOut]
    confirmed_count: int
    skipped: list[str]
    video_count: int


@router.get("/insights")
async def insights() -> InsightsOut:
    """Findings, each carrying its verdict and sample size.

    Suggestive findings are returned so the user can see them; only confirmed ones
    are ever fed back into generation.
    """
    records = await current_records()
    report = analyze(list(records.values()))
    return InsightsOut(
        findings=[FindingOut(**f.as_dict()) for f in report.findings],
        confirmed_count=len(report.confirmed),
        skipped=report.skipped,
        video_count=len(records),
    )


@router.post("/insights/refresh")
async def refresh_insights() -> dict:
    """Pull the last 90 days and re-join metrics onto stored provenance."""
    analytics = _channel()
    rows = await analytics.per_video(days=90)

    records = await current_records()
    updated = 0
    for row in rows:
        record = records.get(row["video_id"])
        if record is None:
            continue  # published outside Studio; no provenance to attribute to
        record.ctr = row["ctr"]
        record.avd_seconds = row["avd_seconds"]
        record.views = row["views"]
        record.avd_percent = row["avd_percent"]
        updated += 1

    return {"pulled": len(rows), "matched": updated, "unmatched": len(rows) - updated}


class VideoRow(BaseModel):
    """One published video for the Analytics per-video table."""

    video_id: str
    title: str
    published_at: str
    views: int
    ctr: float
    avd_seconds: float
    avd_percent: float
    format: str
    title_strategy: str
    thumbnail_concept: str
    hook_device: str


@router.get("/analytics/videos")
async def analytics_videos() -> list[VideoRow]:
    """Every published video with its metrics and provenance, newest first.

    The per-video table on Analytics rendered demo data forever because nothing
    served the rows — `Analytics.per_video` was only ever called internally by the
    refresh. This is the stored join of metrics onto provenance, so it answers
    without spending Analytics API quota; `POST /insights/refresh` is what updates
    the numbers.
    """
    records = await current_records()
    rows = [
        VideoRow(
            video_id=r.video_id,
            title=r.title,
            published_at=r.published_at,
            views=r.views,
            ctr=r.ctr,
            avd_seconds=r.avd_seconds,
            avd_percent=r.avd_percent,
            format=r.format,
            title_strategy=r.title_strategy,
            thumbnail_concept=r.thumbnail_concept,
            hook_device=r.hook_device,
        )
        for r in records.values()
    ]
    rows.sort(key=lambda r: r.published_at, reverse=True)
    return rows


class SpendDay(BaseModel):
    date: str
    usd: float
    jobs: int


class Spend(BaseModel):
    """What this channel has cost, over time.

    Cost has always been metered per stage and capped per video, and nothing could
    answer "what have I spent this month" — the one question that decides whether
    the product is usable at volume rather than once.
    """

    days: list[SpendDay]
    total_usd: float
    month_usd: float
    #: Mean cost of a job that reached `completed`. Unfinished and failed runs are
    #: excluded from the *average* but not from the totals: they cost real money and
    #: hiding them would flatter the number, while averaging them in would answer a
    #: different question than "what does a video cost me".
    per_video_usd: float | None
    completed_videos: int


@router.get("/spend")
async def spend(days: int = Query(90, ge=1, le=365)) -> Spend:
    from engine import repository

    rows = await repository.spend_by_day(days)
    total = round(sum(usd for _d, usd, _n in rows), 2)

    now = datetime.now(UTC)
    prefix = f"{now.year:04d}-{now.month:02d}"
    month = round(sum(usd for day, usd, _n in rows if day.startswith(prefix)), 2)

    finished = await repository.completed_video_costs(days)
    per_video = round(sum(finished) / len(finished), 2) if finished else None

    return Spend(
        days=[SpendDay(date=d, usd=usd, jobs=n) for d, usd, n in rows],
        total_usd=total,
        month_usd=month,
        per_video_usd=per_video,
        completed_videos=len(finished),
    )


@router.get("/insights/review")
async def last_review() -> ReviewPayload | None:
    """The most recent weekly review, or null if none has been stored.

    The cron has produced one every Monday since it was written and there was no
    way to read it: the payload went into arq's result store, which keeps results
    for an hour. Running a fresh one was the only alternative, and that consumes
    the baseline the real weekly diff compares against — so looking at the review
    destroyed next week's.

    Null rather than a 404: "no review yet" is the normal state of a new install,
    not a missing resource.
    """
    from engine import repository

    return await repository.latest_review()


@router.post("/insights/review")
async def run_review() -> dict:
    """Run the weekly review now, rather than waiting for Monday.

    The same work the cron job does, including storing this run's snapshot — so a
    manual run genuinely becomes the baseline the next diff compares against,
    instead of producing a report that vanishes and lets Monday re-report
    everything it already showed.
    """
    from engine import review as weekly

    return (await weekly.run()).as_dict()


class DailyDay(BaseModel):
    day: str
    views: int
    avd_seconds: float
    subscribers_gained: int
    #: The last two days are always incomplete. Presenting them as final makes
    #: every trend look like it is collapsing.
    provisional: bool


class DailyOut(BaseModel):
    days: list[DailyDay]


@router.get("/analytics/daily")
async def daily(days: int = 28) -> DailyOut:
    rows = await _channel().daily(days=days)
    return DailyOut(
        days=[
            DailyDay(
                day=row.day.isoformat(),
                views=row.views,
                avd_seconds=row.avd_seconds,
                subscribers_gained=row.subscribers_gained,
                provisional=row.is_provisional,
            )
            for row in rows
        ]
    )


class RetentionBeat(BaseModel):
    at_percent: float
    label: str
    retention_start: float
    retention_end: float
    drop: float
    drop_rate: float
    worst: bool = False


class RetentionOut(BaseModel):
    curve: list[float]
    beats: list[RetentionBeat]


@router.get("/analytics/retention/{video_id}")
async def retention(video_id: str) -> RetentionOut:
    """The retention curve with script beats located on it."""
    curve = await _channel().retention(video_id)
    record = (await current_records()).get(video_id)
    beats = record.beats if record else []

    return RetentionOut(
        curve=curve,
        beats=[
            RetentionBeat(**b)
            for b in map_retention_to_beats(
                curve, beats, duration_s=record.avd_seconds if record else 0
            )
        ],
    )


@router.get("/analytics/audience")
async def audience() -> dict:
    """The publish-time profile the scheduler uses, and where it came from."""
    profile = await _channel().audience_profile()
    return {
        "source": profile.source,
        "measured": profile.is_measured,
        "weekday": profile.daily,
        "hourly": profile.hourly,
        "note": (
            "YouTube exposes no hourly dimension through the public API, so the "
            "hour-of-day shape remains an estimate even once weekday data is measured."
        ),
    }


class ShortCandidateOut(BaseModel):
    start_s: float
    end_s: float
    duration_s: float
    label: str
    lift: float
    hold: float
    score: float
    reason: str


class ShortsOut(BaseModel):
    video_id: str
    duration_s: float
    candidates: list[ShortCandidateOut]
    note: str | None
    """Set when the list is empty, saying which of the several reasons it was."""


@router.get("/analytics/shorts/{video_id}")
async def shorts(video_id: str, count: int = Query(3, ge=1, le=10)) -> ShortsOut:
    """Moments in a long-form video worth cutting into a Short.

    Reads the retention curve the retention map already pulls, and the beats the
    script was written in, so the only new cost is one `videos.list` unit for the
    runtime.

    An empty list is a real answer and comes with a `note` saying why — a video
    whose retention never rises above its own decay has no standout moment, and
    offering three arbitrary windows instead would make every later ranking
    unbelievable.
    """
    record = (await current_records()).get(video_id)
    if record is None:
        raise HTTPException(404, "no provenance recorded for that video")

    beats = record.beats
    if not beats:
        return ShortsOut(
            video_id=video_id,
            duration_s=0.0,
            candidates=[],
            note=(
                "This video has no script beats recorded, so there is nothing to cut "
                "on. Beats are what make a clip start at the top of a thought."
            ),
        )

    creds = CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected")

    duration_s = await youtube.YouTube(creds).duration_seconds(video_id)
    if not duration_s:
        raise HTTPException(502, "could not read the video's duration from YouTube")

    curve = await Analytics(creds).retention(video_id)
    candidates = shorts_from_retention.find_candidates(curve, beats, duration_s, count=count)

    note = None
    if not candidates:
        note = (
            "Retention on this video never rises above its own decay by enough to "
            "call any stretch a standout. That usually means it holds evenly rather "
            "than that it has no good moment — there is just nothing here to rank."
        )
    return ShortsOut(
        video_id=video_id,
        duration_s=round(duration_s, 2),
        candidates=[ShortCandidateOut(**c.as_dict()) for c in candidates],
        note=note,
    )


class ThresholdOut(BaseModel):
    """One bar. `fraction` is already clamped to 0..1, so the UI multiplies by 100
    and stops there — nothing downstream re-derives it from current/target."""

    name: str
    current: float
    target: float
    unit: str
    met: bool
    remaining: float
    fraction: float
    window_days: int
    covers_full_window: bool
    days_remaining: int | None


class MonetisationOut(BaseModel):
    eligible: bool
    route: str
    blocking: list[str]
    """What is still in the way, most-limiting first; empty once eligible.

    A list, because both halves of a route can be outstanding at once. This was
    `str | None`, which Pydantic refuses a list for — so the endpoint returned a
    500 for every channel that was *not* already monetised, which is every channel
    the feature exists for. Neither the 20 tests on `progress()` nor the 7 on the
    card caught it: nothing exercised the seam between them.
    """

    caveat: str | None
    subscribers: ThresholdOut
    watch_hours: ThresholdOut
    shorts_views: ThresholdOut
    subscriber_count_hidden: bool


@router.get("/analytics/monetisation")
async def monetisation() -> MonetisationOut:
    """How close the channel is to the Partner Programme, on either route.

    The number the whole product is aimed at, and the one no screen showed. Three
    calls: the subscriber total from the Data API (one quota unit — Analytics
    reports a delta, not a count), a year of daily watch minutes, and 90 days of
    Shorts views.

    A year of dailies is one Analytics query, and Analytics has its own far larger
    quota pool (KNOWN-ISSUES §3.2b), so the cost of this endpoint is the single
    Data API unit.
    """
    creds = CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected")

    analytics = Analytics(creds)
    subscribers = await youtube.YouTube(creds).subscriber_count()
    daily = await analytics.daily(days=monetisation_progress.WATCH_HOURS_WINDOW_DAYS)
    shorts = await analytics.shorts_views()

    # A hidden subscriber count reads as zero rather than failing the request. The
    # response says which it was, so the UI can show "hidden" instead of a bar
    # sitting at 0% that looks like a channel with no subscribers at all.
    report = monetisation_progress.progress(
        subscriber_count=subscribers or 0,
        watch_minutes_by_day={d.day: d.watch_minutes for d in daily},
        shorts_views_by_day=shorts,
    )
    return MonetisationOut(**report.as_dict(), subscriber_count_hidden=subscribers is None)
