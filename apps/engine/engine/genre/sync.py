"""Sweep the watchlist: refresh each watched channel's recent uploads.

One sweep = for every active channel, its uploads playlist (newest first),
then one batched `videos.list` for statistics. The Data API prices each of
those at 1 unit — so a 40-channel watchlist costs ~80 units a day, where
re-running `search.list` per topic (the old way to learn who competes) costs
100 *per query*. Cheap enough that freshness stops being a budget decision.

Per-channel failures degrade the way `trending.py` degrades: one dead channel
must not stop the other thirty-nine, and must leave a mark on its own row
(`last_error`) rather than only in worker logs — "quietly broken" is the
failure mode this integration is designed against.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from engine import repository
from engine.providers.youtube import _parse_iso8601_duration

#: Uploads pulled per channel per sweep. Deep history is not the point of a
#: *watch* — velocity and hook patterns come from what a channel is doing now,
#: and 60 uploads reaches back months even for daily posters.
UPLOADS_PER_CHANNEL = 60


def _parse_published_at(raw: str | None) -> datetime | None:
    """YouTube's `publishedAt` (`2024-05-01T12:34:56Z`) → aware UTC datetime.

    `fromisoformat` has handled the `Z` suffix since 3.11; a malformed stamp is
    metadata loss on one video, not a reason to fail the sweep.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_from_item(item: dict[str, Any], *, channel_id: str) -> dict[str, Any]:
    """API item → the dict `repository.upsert_watched_videos` wants."""
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    content = item.get("contentDetails", {})
    # Hidden counts are absent keys, not zeros — `.get(..., 0)` is load-bearing.
    views = int(statistics.get("viewCount", 0))
    return {
        "video_id": item.get("id", ""),
        "watched_channel_id": channel_id,
        "title": snippet.get("title", ""),
        "published_at": _parse_published_at(snippet.get("publishedAt")),
        # `PT#H#M#S` → seconds; the parser lives beside the client that receives
        # these strings rather than being duplicated here.
        "duration_s": _parse_iso8601_duration(content.get("duration", "")) or 0.0,
        "views": views,
        "likes": int(statistics.get("likeCount", 0)),
    }


async def sync_channel(yt, youtube_channel_id: str) -> dict[str, Any]:
    """Refresh one watched channel. Returns a report; never raises.

    A channel that stopped existing (terminated, deleted) surfaces here as an
    error report rather than vanishing from the API silently — the row keeps
    saying why nothing has arrived lately.
    """
    try:
        details = await yt.channel_details([youtube_channel_id])
        if not details:
            report = {
                "channel_id": youtube_channel_id,
                "ok": False,
                "videos_seen": 0,
                "new_videos": 0,
                "error": "channel not found",
            }
            await repository.mark_watched_channel_synced(youtube_channel_id, error=report["error"])
            return report

        uploads = (
            details[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        )
        if not uploads:
            # Uploads playlists are private-only for channels with zero public
            # videos or with uploads hidden — rare, but a state worth naming.
            report = {
                "channel_id": youtube_channel_id,
                "ok": False,
                "videos_seen": 0,
                "new_videos": 0,
                "error": "no public uploads playlist",
            }
            await repository.mark_watched_channel_synced(youtube_channel_id, error=report["error"])
            return report

        items = await yt.playlist_items(uploads, limit=UPLOADS_PER_CHANNEL)
        ids = [vid for item in items if (vid := item.get("contentDetails", {}).get("videoId"))]
        full = await yt.video_details(ids) if ids else []

        rows = [_row_from_item(item, channel_id=youtube_channel_id) for item in full]
        rows = [r for r in rows if r["video_id"]]
        new = await repository.upsert_watched_videos(rows)
        await repository.mark_watched_channel_synced(youtube_channel_id)
        return {
            "channel_id": youtube_channel_id,
            "ok": True,
            "videos_seen": len(rows),
            "new_videos": new,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 — one channel failing must not stop the sweep
        logger.warning("watchlist sync failed for {}: {}", youtube_channel_id, exc)
        await repository.mark_watched_channel_synced(youtube_channel_id, error=str(exc)[:500])
        return {
            "channel_id": youtube_channel_id,
            "ok": False,
            "videos_seen": 0,
            "new_videos": 0,
            "error": str(exc)[:500],
        }


async def sync_all(yt=None) -> list[dict[str, Any]]:
    """Sweep every active watched channel concurrently.

    `yt=None` (no connected YouTube credential) returns `[]` — the same
    honest-empty contract as `trending.gather_trending_terms`. Channels are
    swept together because they are independent requests; a slow channel waits
    in parallel with the others instead of serialising the whole sweep.
    """
    if yt is None:
        return []
    channels = await repository.list_watched_channels(active_only=True)
    if not channels:
        return []
    import asyncio

    reports = await asyncio.gather(*(sync_channel(yt, ch["youtube_channel_id"]) for ch in channels))
    return list(reports)
