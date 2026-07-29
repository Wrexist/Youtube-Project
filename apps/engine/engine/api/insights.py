"""Insight and analytics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.api.publishing import CHANNELS
from engine.insights import VideoRecord, analyze, map_retention_to_beats
from engine.providers.analytics import Analytics

router = APIRouter(prefix="/v1", tags=["insights"])

# Published videos joined to the provenance of what produced them. Postgres-backed
# in Phase 1; the shape is what the attribution needs and does not change.
RECORDS: dict[str, VideoRecord] = {}


def _channel() -> Analytics:
    creds = CHANNELS.get("default")
    if creds is None:
        raise HTTPException(409, "no channel connected")
    return Analytics(creds)


@router.get("/insights")
async def insights() -> dict:
    """Findings, each carrying its verdict and sample size.

    Suggestive findings are returned so the user can see them; only confirmed ones
    are ever fed back into generation.
    """
    report = analyze(list(RECORDS.values()))
    return {
        "findings": [f.as_dict() for f in report.findings],
        "confirmed_count": len(report.confirmed),
        "skipped": report.skipped,
        "video_count": len(RECORDS),
    }


@router.post("/insights/refresh")
async def refresh_insights() -> dict:
    """Pull the last 90 days and re-join metrics onto stored provenance."""
    analytics = _channel()
    rows = await analytics.per_video(days=90)

    updated = 0
    for row in rows:
        record = RECORDS.get(row["video_id"])
        if record is None:
            continue  # published outside Studio; no provenance to attribute to
        record.ctr = row["ctr"]
        record.avd_seconds = row["avd_seconds"]
        record.views = row["views"]
        record.retention_30s = row["avd_percent"]
        updated += 1

    return {"pulled": len(rows), "matched": updated, "unmatched": len(rows) - updated}


@router.get("/analytics/daily")
async def daily(days: int = 28) -> dict:
    rows = await _channel().daily(days=days)
    return {
        "days": [
            {
                "day": row.day.isoformat(),
                "views": row.views,
                "avd_seconds": row.avd_seconds,
                "subscribers_gained": row.subscribers_gained,
                # The last two days are always incomplete. Presenting them as final
                # makes every trend look like it is collapsing.
                "provisional": row.is_provisional,
            }
            for row in rows
        ]
    }


@router.get("/analytics/retention/{video_id}")
async def retention(video_id: str) -> dict:
    """The retention curve with script beats located on it."""
    curve = await _channel().retention(video_id)
    record = RECORDS.get(video_id)
    beats = getattr(record, "beats", []) if record else []

    return {
        "curve": curve,
        "beats": map_retention_to_beats(
            curve, beats, duration_s=record.avd_seconds if record else 0
        ),
    }


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
