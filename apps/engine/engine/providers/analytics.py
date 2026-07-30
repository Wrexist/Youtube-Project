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
from datetime import date, timedelta

import httpx
from loguru import logger

from engine.providers.youtube import Credentials, refresh
from engine.scheduling import AudienceProfile

BASE = "https://youtubeanalytics.googleapis.com/v2/reports"

# Days at the end of a window that Google has not finished counting.
PROVISIONAL_DAYS = 2


@dataclass
class DailyMetrics:
    day: date
    views: int = 0
    impressions: int = 0
    ctr: float = 0.0
    avd_seconds: float = 0.0
    subscribers_gained: int = 0

    @property
    def is_provisional(self) -> bool:
        return (date.today() - self.day).days < PROVISIONAL_DAYS


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
        end = date.today()
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
                avd_seconds=float(row[3]),
                subscribers_gained=int(row[4]),
            )
            for row in rows
        ]

    async def per_video(self, days: int = 90) -> list[dict]:
        """Per-video CTR and duration — the input to attribution.

        `impressionClickThroughRate` is only available on the channel's own content
        and only for the last ~90 days, which is the practical horizon for any
        finding this system produces.
        """
        end = date.today()
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
                "startDate": (date.today() - timedelta(days=90)).isoformat(),
                "endDate": date.today().isoformat(),
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
        end = date.today()
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
