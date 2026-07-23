"""YouTube API quota ledger.

The default project quota is 10,000 units per day and a single upload costs 1,600.
That is roughly six uploads a day, and it is the hardest ceiling in the entire
system — every scheduling decision is downstream of it.

So spend is *recorded*, never estimated after the fact, and an operation that cannot
afford to complete is refused before it starts rather than failing halfway through a
1,600-unit upload.

Quota resets at midnight **Pacific**, not UTC and not local. Getting that wrong means
the ledger disagrees with Google by up to eight hours, which shows up as mysterious
`quotaExceeded` errors on a day the UI claims is empty.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone

# Documented unit costs. Anything not listed defaults to 1 (the read-op cost).
COSTS: dict[str, int] = {
    "videos.insert": 1600,
    "search.list": 100,
    "captions.insert": 400,
    "captions.update": 450,
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
    "videos.update": 50,
    "playlists.insert": 50,
    "videos.list": 1,
    "channels.list": 1,
    "playlistItems.list": 1,
}

DAILY_LIMIT = 10_000
PACIFIC = timezone(timedelta(hours=-8))  # PST; DST shifts this by an hour


class QuotaExceeded(Exception):
    """Raised before a spend that would breach the daily ceiling."""

    def __init__(self, operation: str, cost: int, remaining: int) -> None:
        self.operation, self.cost, self.remaining = operation, cost, remaining
        super().__init__(f"{operation} costs {cost} units but only {remaining} remain today")


@dataclass
class Entry:
    operation: str
    cost: int
    at: datetime
    channel_id: str = ""
    note: str = ""


def quota_day(moment: datetime | None = None) -> date:
    """The quota day a moment falls in. Pacific, because that's where Google resets."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(PACIFIC).date()


@dataclass
class QuotaLedger:
    """In-memory ledger. Phase 1 moves this to a Postgres table; the interface is
    the same so the swap stays inside this module."""

    limit: int = DAILY_LIMIT
    entries: list[Entry] = field(default_factory=list)

    def cost_of(self, operation: str) -> int:
        return COSTS.get(operation, 1)

    def spent(self, day: date | None = None) -> int:
        day = day or quota_day()
        return sum(e.cost for e in self.entries if quota_day(e.at) == day)

    def remaining(self, day: date | None = None) -> int:
        return max(0, self.limit - self.spent(day))

    def can_afford(self, operation: str, day: date | None = None) -> bool:
        return self.cost_of(operation) <= self.remaining(day)

    def check(self, operation: str, day: date | None = None) -> None:
        """Raise if the operation cannot complete. Call before the request, not after."""
        cost = self.cost_of(operation)
        remaining = self.remaining(day)
        if cost > remaining:
            raise QuotaExceeded(operation, cost, remaining)

    def record(
        self,
        operation: str,
        *,
        at: datetime | None = None,
        channel_id: str = "",
        note: str = "",
    ) -> Entry:
        entry = Entry(
            operation=operation,
            cost=self.cost_of(operation),
            at=at or datetime.now(UTC),
            channel_id=channel_id,
            note=note,
        )
        self.entries.append(entry)
        return entry

    def uploads_left(self, day: date | None = None) -> int:
        """How many more videos can be published today.

        Counts the whole publish sequence — insert plus thumbnail plus captions —
        because publishing a video and then failing to set its thumbnail is not a
        useful outcome.
        """
        per_publish = COSTS["videos.insert"] + COSTS["thumbnails.set"] + COSTS["captions.insert"]
        return self.remaining(day) // per_publish

    def usage_by_day(self, days: int = 28) -> dict[date, int]:
        """For the calendar's weekly quota bars."""
        out: dict[date, int] = defaultdict(int)
        today = quota_day()
        for offset in range(days):
            out[today - timedelta(days=offset)] = 0
        for entry in self.entries:
            day = quota_day(entry.at)
            if day in out:
                out[day] += entry.cost
        return dict(out)

    def breakdown(self, day: date | None = None) -> dict[str, int]:
        """Where the day's units went. Surfaced when a user asks why they're capped —
        it is usually competitor mining, not uploads."""
        day = day or quota_day()
        out: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            if quota_day(entry.at) == day:
                out[entry.operation] += entry.cost
        return dict(out)


ledger = QuotaLedger()
