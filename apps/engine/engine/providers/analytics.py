"""YouTube Analytics API.

A separate API from the Data API, with its own — much more generous — quota, so
pulling daily is fine.

These calls are **not** metered into `engine.quota`, which is the one documented
exception to CLAUDE.md #5. The ledger models the Data API's 10,000 units/day and is
what `can_afford` consults before an upload; feeding a second, unrelated quota pool
into the same counter would make it refuse uploads there is budget for. Doing it
properly means a second pool, which is real work for a breakdown panel nobody reads.
The docstring here used to claim the calls "are still recorded in the ledger for
visibility" — they never were. See KNOWN-ISSUES §3.

The important caveat, enforced here rather than left to callers: **data lags 24-48
hours**. The two most recent days are always incomplete, and treating them as final
makes every trend look like it is collapsing. `is_provisional` marks them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from loguru import logger

from engine.monetisation import SHORTS_VIEWS_WINDOW_DAYS
from engine.providers.youtube import Credentials, refresh
from engine.scheduling import AudienceProfile

BASE = "https://youtubeanalytics.googleapis.com/v2/reports"

# Days at the end of a window that Google has not finished counting.
PROVISIONAL_DAYS = 2


def today() -> date:
    """Today in UTC, not in whatever zone the server happens to sit in.

    `date.today()` is local. Every other timestamp in this codebase is UTC-aware on
    purpose, and this is the layer that talks to Google — so a container in UTC+13
    asked for a date range a day ahead of the one it meant, and marked the wrong two
    days provisional. Being consistently one day off is worse than being wrong once:
    it silently shifts every window this module computes.

    UTC rather than the channel's own zone because the API does not expose that. The
    two-day provisional margin already covers a zone offset several times over.
    """
    return datetime.now(UTC).date()


@dataclass
class DailyMetrics:
    day: date
    views: int = 0
    impressions: int = 0
    ctr: float = 0.0
    avd_seconds: float = 0.0
    subscribers_gained: int = 0
    #: Minutes, as Google reports them — `engine.monetisation` converts to hours.
    #:
    #: `daily()` has always *asked* for `estimatedMinutesWatched` and then read
    #: columns 1, 3 and 4 of the four it paid for, dropping this one. It is half of
    #: the Partner Programme threshold the whole product is aimed at, so it was the
    #: one number worth keeping.
    watch_minutes: float = 0.0

    @property
    def is_provisional(self) -> bool:
        return (today() - self.day).days < PROVISIONAL_DAYS


class Analytics:
    def __init__(self, creds: Credentials) -> None:
        self.creds = creds

    async def _query(self, params: dict) -> dict:
        if not self.creds.is_fresh:
            await refresh(self.creds)
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(
                BASE,
                params={"ids": "channel==MINE", **params},
                headers={"Authorization": f"Bearer {self.creds.access_token}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"analytics query failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    async def daily(self, days: int = 28) -> list[DailyMetrics]:
        end = today()
        start = end - timedelta(days=days)
        payload = await self._query(
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": ("views,estimatedMinutesWatched,averageViewDuration,subscribersGained"),
                "dimensions": "day",
                "sort": "day",
            }
        )
        rows = payload.get("rows", [])
        return [
            DailyMetrics(
                day=date.fromisoformat(row[0]),
                views=int(row[1]),
                watch_minutes=float(row[2]),
                avd_seconds=float(row[3]),
                subscribers_gained=int(row[4]),
            )
            for row in rows
        ]

    async def shorts_views(self, days: int = SHORTS_VIEWS_WINDOW_DAYS) -> dict[date, int]:
        """Daily Shorts views — the other route to the Partner Programme.

        Separate from `daily()` because it needs a filter the rest of the daily pull
        must not have: `creatorContentType==shortsVideo` would silently narrow every
        other metric to Shorts alone if it were added there.

        Returns an empty mapping rather than raising when the dimension is refused.
        Not every channel and not every API version answers this filter, and a
        channel with no Shorts is the overwhelmingly common case here — neither is
        an error worth failing a dashboard over, and the long-form route is
        unaffected either way.
        """
        end = today()
        start = end - timedelta(days=days)
        try:
            payload = await self._query(
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": "views",
                    "dimensions": "day",
                    "filters": "creatorContentType==shortsVideo",
                    "sort": "day",
                }
            )
        except RuntimeError as exc:
            logger.info("no Shorts breakdown available ({}); the long-form route stands", exc)
            return {}
        return {date.fromisoformat(row[0]): int(row[1]) for row in payload.get("rows", [])}

    async def per_video(self, days: int = 90) -> list[dict]:
        """Per-video CTR and duration — the input to attribution.

        `impressionClickThroughRate` is only available on the channel's own content
        and only for the last ~90 days, which is the practical horizon for any
        finding this system produces.
        """
        end = today()
        start = end - timedelta(days=days)
        payload = await self._query(
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": (
                    "views,impressions,impressionClickThroughRate,"
                    "averageViewDuration,averageViewPercentage"
                ),
                "dimensions": "video",
                "sort": "-views",
                "maxResults": 200,
            }
        )
        return [
            {
                "video_id": row[0],
                "views": int(row[1]),
                "impressions": int(row[2]),
                "ctr": float(row[3]),
                "avd_seconds": float(row[4]),
                "avd_percent": float(row[5]),
            }
            for row in payload.get("rows", [])
        ]

    async def retention(self, video_id: str) -> list[float]:
        """The retention curve, sampled across the video's runtime.

        `elapsedVideoTimeRatio` is what makes the retention map possible — it is the
        dimension that lets a drop-off be located against a script beat.
        """
        payload = await self._query(
            {
                "ids": "channel==MINE",
                "startDate": (today() - timedelta(days=90)).isoformat(),
                "endDate": today().isoformat(),
                "metrics": "audienceWatchRatio",
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
                "sort": "elapsedVideoTimeRatio",
            }
        )
        rows = payload.get("rows", [])
        return [float(row[1]) * 100 for row in rows]

    async def audience_profile(self) -> AudienceProfile:
        """Derive a measured publish-time profile to replace the scheduler's guess.

        YouTube does not expose "when your viewers are on YouTube" through the public
        API, so this is reconstructed from *when views actually happen* — which is a
        proxy, not the same thing, and the profile is labelled accordingly.
        """
        end = today()
        start = end - timedelta(days=90)
        try:
            payload = await self._query(
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": "views",
                    "dimensions": "day",
                    "sort": "day",
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not derive audience profile: {}", exc)
            return AudienceProfile()  # falls back to the labelled heuristic

        rows = payload.get("rows", [])
        if len(rows) < 28:
            # Fewer than four weeks is not a pattern, it is a sample.
            logger.info("only {} days of data; keeping the default profile", len(rows))
            return AudienceProfile()

        weekday_totals = [0.0] * 7
        weekday_counts = [0] * 7
        for row in rows:
            day = date.fromisoformat(row[0])
            weekday_totals[day.weekday()] += float(row[1])
            weekday_counts[day.weekday()] += 1

        averages = [
            total / count if count else 0.0
            for total, count in zip(weekday_totals, weekday_counts, strict=True)
        ]
        overall = sum(averages) / 7 or 1.0
        weekday = [a / overall for a in averages]

        # The hourly shape still comes from the heuristic — the API gives no hourly
        # dimension. Saying so is better than implying we measured it.
        profile = AudienceProfile(daily=weekday)
        profile.source = "measured_weekday_only"
        return profile
