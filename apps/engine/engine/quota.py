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

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from engine.settings import get_settings

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

# Google's default grant. The effective ceiling is `ledger.limit`, which reads
# STUDIO_YOUTUBE_DAILY_QUOTA — read that, not this, when comparing against spend.
DAILY_LIMIT = 10_000
#: Google resets quota at midnight Pacific, which is a *place*, not an offset. A
#: fixed `timezone(timedelta(hours=-8))` is only correct for the four months of PST;
#: through PDT it puts the boundary an hour early, so spend in the 07:00-08:00 UTC
#: hour is booked to the previous day. That is precisely the "ledger disagrees with
#: Google" failure this module's docstring warns about, and it is live for two
#: thirds of the year.
PACIFIC = ZoneInfo("America/Los_Angeles")


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
    #: False when the database write failed. `record()` keeps such an entry in
    #: memory on purpose — the units were spent at Google whether or not we managed
    #: to write the row — and `load()` has to carry it across, or re-reading the
    #: ledger silently hands that budget back.
    persisted: bool = True
    #: Primary key of the `quota_entries` row, once there is one. `None` means the
    #: database cannot see this entry yet — the write failed, or it is still in
    #: flight — which is exactly what `load()` needs to know to merge rather than
    #: replace. It is also the handle `refund()` deletes by.
    row_id: int | None = None


def quota_day(moment: datetime | None = None) -> date:
    """The quota day a moment falls in. Pacific, because that's where Google resets."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(PACIFIC).date()


@dataclass
class QuotaLedger:
    """Postgres-backed, with the day's entries cached in memory.

    Reads (`spent`, `remaining`, `can_afford`, `check`) stay synchronous and hit
    the cache — they are called from sync code in `automation.py` and from the
    hot path in `youtube.py`, and making them async would ripple through both for
    no benefit. Writes are async and durable before they return: this is the one
    table where losing a row means silently overrunning Google's daily ceiling.

    `load()` hydrates the cache at startup. Without it a restart forgets the day's
    spend and the next upload overruns — the single worst consequence of §5.1.
    """

    # Reads STUDIO_YOUTUBE_DAILY_QUOTA. The setting existed and was read by
    # nothing, so a granted quota extension (KNOWN-ISSUES §3.2) could not be
    # configured — the 10,000 ceiling was hardcoded here.
    limit: int = field(default_factory=lambda: get_settings().youtube_daily_quota)
    entries: list[Entry] = field(default_factory=list)
    # Tests construct bare ledgers and must not need a database.
    persist: bool = True

    # Serialises every read-modify-write of `entries`. `record()` appends and then
    # awaits a database write; `load()` queries and then replaces the list. With
    # both suspending mid-sequence, load's query could run before record's row was
    # committed *and* its reassignment land after record's append — dropping the
    # appended entry and handing its budget straight back.
    _lock: asyncio.Lock | None = field(default=None, repr=False, compare=False)
    _lock_loop: Any = field(default=None, repr=False, compare=False)

    def _serialised(self) -> asyncio.Lock:
        """The lock for the running loop.

        Rebound when the loop changes rather than created once at import: this
        module owns a singleton `ledger`, and an `asyncio.Lock` binds itself to the
        first loop that awaits it — so a second `asyncio.run`, which is how the test
        suite and the CLI both call in, would raise "bound to a different event
        loop" on a ledger that had already been used. What needs serialising is
        concurrency *within* one loop; two loops sharing one ledger is not a shape
        this system has.
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

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

    async def record(
        self,
        operation: str,
        *,
        at: datetime | None = None,
        channel_id: str = "",
        note: str = "",
    ) -> Entry:
        """Meter a call. Durable before it returns.

        Deliberately not fire-and-forget: the row is the only record that these
        units were spent, and a crash between the API call and an unflushed write
        is exactly the case that leads to a silent overrun tomorrow.
        """
        async with self._serialised():
            return await self._record(operation, at=at, channel_id=channel_id, note=note)

    async def _record(
        self,
        operation: str,
        *,
        at: datetime | None = None,
        channel_id: str = "",
        note: str = "",
    ) -> Entry:
        """`record()` with the lock already held."""
        entry = Entry(
            operation=operation,
            cost=self.cost_of(operation),
            at=at or datetime.now(UTC),
            channel_id=channel_id,
            note=note,
        )
        self.entries.append(entry)

        if self.persist:
            from engine.db import session
            from engine.tables import QuotaEntry

            try:
                async with session() as s:
                    row = QuotaEntry(
                        operation=entry.operation,
                        cost=entry.cost,
                        at=entry.at,
                        channel_id=entry.channel_id,
                        note=entry.note,
                    )
                    s.add(row)
                    # Flushed inside the session so the primary key exists before it
                    # closes. `load()` matches on that key to decide what it may
                    # replace, and `refund()` deletes by it.
                    await s.flush()
                entry.row_id = row.id
            except Exception:
                # The spend already happened at Google. Dropping the in-memory
                # entry too would double-count the remaining budget, so keep it
                # and make the persistence failure loud instead.
                entry.persisted = False
                logger.exception("failed to persist quota entry for {}", operation)

        return entry

    async def reserve(
        self,
        operation: str,
        *,
        channel_id: str = "",
        note: str = "",
    ) -> Entry:
        """Re-read, check and book, without letting go in between.

        The upload path used to do this as two awaits — `check_fresh()` and then,
        after opening the resumable session, `record()`. Two publishes starting
        together therefore both read the same "8,400 of 10,000 spent", both passed
        the check, and both booked 1,600: 11,600 units against a 10,000 ceiling,
        discovered only when Google refused the second one. Nothing between the read
        and the write can be allowed to yield, which is what this exists to
        guarantee.

        Raises `QuotaExceeded` without booking anything when there is no room.
        """
        async with self._serialised():
            if self.persist:
                try:
                    await self._load()
                except Exception:  # noqa: BLE001 — see check_fresh
                    logger.warning("could not refresh the quota ledger; checking against the cache")
            self.check(operation)
            return await self._record(operation, channel_id=channel_id, note=note)

    async def refund(self, entry: Entry) -> None:
        """Un-book a reservation that was never spent.

        Only for the narrow case where the request that would have cost the units
        demonstrably never reached Google — `reserve()` books before the call, so
        without this a run of failed session openings would eat a day's budget that
        was never actually charged, and refuse uploads there is full room for.

        Never call this because an upload *failed*: Google charges the 1,600 units
        when the resumable session is created, whatever happens to it afterwards.
        """
        async with self._serialised():
            try:
                self.entries.remove(entry)
            except ValueError:
                # A `load()` between the reserve and the refund replaced this object
                # with an equal one read back from the row; drop that instead.
                match = next((e for e in self.entries if e.row_id == entry.row_id), None)
                if entry.row_id is None or match is None:
                    logger.warning(
                        "quota entry for {} was already gone; not refunding", entry.operation
                    )
                    return
                self.entries.remove(match)

            if self.persist and entry.row_id is not None:
                from sqlalchemy import delete

                from engine.db import session
                from engine.tables import QuotaEntry

                try:
                    async with session() as s:
                        await s.execute(delete(QuotaEntry).where(QuotaEntry.id == entry.row_id))
                except Exception:
                    # The in-memory entry is already gone, so the ledger now
                    # under-counts by this row until the next `load()` puts it back.
                    # That is the safe direction of wrong for a refund: it comes
                    # back, rather than staying spent forever.
                    logger.exception("failed to delete refunded quota row {}", entry.row_id)

    async def check_fresh(self, operation: str) -> None:
        """Re-read the day's spend from the database, then check.

        `check()` reads an in-process cache hydrated at startup, which was correct
        while one process did everything. The render worker is a separate process
        and it is the one that uploads, so the API and the worker each accumulate
        spend the other never sees: both can believe there is room for a
        1,600-unit upload when between them there is not, and Google's ceiling is
        breached silently — the exact failure this module exists to prevent.

        Only the paths that are about to spend need this. Display reads can be a
        few seconds stale without hurting anyone; an upload cannot.

        This is the *read-only* form. A path that is about to spend wants
        `reserve()`, which holds the lock across the check and the booking — the gap
        between this returning and a later `record()` is exactly where two
        concurrent publishes both fit through the last 1,600 units.
        """
        async with self._serialised():
            if self.persist:
                try:
                    await self._load()
                except Exception:  # noqa: BLE001
                    # Refusing to upload because the ledger could not be re-read would
                    # be worse than proceeding on a cache that is at worst incomplete.
                    logger.warning("could not refresh the quota ledger; checking against the cache")
            self.check(operation)

    async def load(self, days: int = 35) -> int:
        """Hydrate the cache from the database. Call once at startup.

        Loads a little more than the 28 days `usage_by_day` charts, so the
        calendar's history survives a restart along with today's spend.
        """
        async with self._serialised():
            return await self._load(days)

    async def _load(self, days: int = 35) -> int:
        """`load()` with the lock already held."""
        from sqlalchemy import select

        from engine.db import session
        from engine.tables import QuotaEntry

        # Carried across the reload — a *merge*, not a replacement. Anything without
        # a row id is something the query below cannot see: a write that failed, or
        # one still in flight. This used to be `[e for e in entries if not
        # e.persisted]` computed before the await, so an entry appended while the
        # query was running was neither in the rows nor in the carry-over list, and
        # `self.entries = [...]` erased it. `reserve()` calls this immediately before
        # deciding whether an upload fits, so an erased entry is 1,600 units of
        # budget handed straight back — the overrun this ledger exists to prevent.
        carried = [e for e in self.entries if e.row_id is None]
        unpersisted = [e for e in carried if not e.persisted]

        since = datetime.now(UTC) - timedelta(days=days)
        async with session() as s:
            rows = (await s.execute(select(QuotaEntry).where(QuotaEntry.at >= since))).scalars()
            self.entries = [
                Entry(
                    operation=r.operation,
                    cost=r.cost,
                    at=r.at if r.at.tzinfo else r.at.replace(tzinfo=UTC),
                    channel_id=r.channel_id,
                    note=r.note,
                    row_id=r.id,
                )
                for r in rows
            ]
        self.entries.extend(carried)

        if unpersisted:
            logger.warning(
                "{} quota entr{} never reached the database and are being counted from memory only",
                len(unpersisted),
                "y is" if len(unpersisted) == 1 else "ies are",
            )

        logger.info(
            "quota ledger restored: {} entries, {} units spent today",
            len(self.entries),
            self.spent(),
        )
        return len(self.entries)

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
