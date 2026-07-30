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
from datetime import UTC, date, datetime, time, timedelta
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


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC half-open interval `[start, end)` covering one Pacific quota day.

    The database stores instants, not days, so every day-scoped query has to be a
    range scan on `at` — which is what `quota_entries.at` is indexed for. Built by
    zone-aware arithmetic rather than a fixed eight-hour offset for the reason
    `PACIFIC` exists at all: through PDT the boundary moves, and a fixed offset
    books the 07:00-08:00 UTC hour to the wrong day.
    """
    start = datetime.combine(day, time.min, tzinfo=PACIFIC)
    # Wall-clock arithmetic on a zoned datetime, so a DST transition inside the day
    # yields a 23- or 25-hour span rather than a 24-hour one that clips or overlaps.
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


async def _lock_day(s, day: date) -> None:
    """Take a write lock scoped to one quota day, inside the caller's transaction.

    Dialect-specific because the two engines offer different primitives, and the
    difference matters:

    * **Postgres** gets a transaction-scoped advisory lock keyed on the day.
      `SELECT ... FOR UPDATE` alone is not enough here — it locks the rows that
      exist, and the thing being guarded against is two transactions each
      *inserting* a row the other cannot see. The `FOR UPDATE` in the caller stays
      as well, so a concurrent `refund()` deleting a row cannot slip between the
      sum and the insert.
    * **SQLite** has one writer, but its default `BEGIN` is deferred: the write
      lock is only taken at the INSERT, by which point both transactions have
      already read the same total and one of them is about to be told "database is
      locked" *after* deciding it had room. `BEGIN IMMEDIATE` takes the lock up
      front, so the second reserver blocks on it (for `sqlite3`'s five-second
      busy timeout) and then reads the first one's booking.

    Failure to acquire is not fatal: the asyncio lock still serialises this
    process, and refusing an upload because the lock statement was not understood
    would be a worse outcome than the race it prevents. But it is not silent
    either — losing this lock loses the cross-process guarantee `_reserve_locked`
    documents, and a degraded ceiling that nobody is told about is how the overrun
    happens twice. Every path out of here that did not take a lock says so.
    """
    from sqlalchemy import text

    from engine.db import engine

    dialect = engine().dialect.name
    try:
        if dialect == "postgresql":
            # toordinal() is a stable, collision-free key per day and fits the
            # bigint the one-argument form takes.
            await s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": day.toordinal()})
        elif dialect == "sqlite":
            # This runs before SQLAlchemy has emitted anything on the connection, so
            # the transaction has not actually begun and the statement is accepted.
            # If that ever stops being true it fails loudly below rather than
            # quietly, because `with_for_update()` is a no-op on SQLite — losing
            # this statement would leave the day with no lock at all.
            await s.execute(text("BEGIN IMMEDIATE"))
        else:
            logger.warning(
                "no quota day lock on dialect {!r}: two processes can both book the "
                "last upload of the day. Only postgresql and sqlite are guarded.",
                dialect,
            )
    except Exception as exc:  # noqa: BLE001 — see the docstring
        logger.warning(
            "could not take the quota day lock on {} ({}); the daily ceiling is now "
            "guarded only within this process",
            dialect,
            exc,
        )


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
    #: Whether writes touch the database. `None` means "follow STUDIO_PERSIST",
    #: which is what the module singleton wants: the app's lifespan used to *pin*
    #: this to False on a scratch instance, and because the ledger is a
    #: process-wide singleton that pin outlived whatever set it — a later caller
    #: with persistence genuinely on then read and wrote nothing but its own cache.
    #: Tests that construct a bare ledger still pass `persist=False` explicitly.
    persist: bool | None = None

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

    @property
    def _persisting(self) -> bool:
        """`persist`, resolved. See the field's comment for why it can be unset."""
        return get_settings().persist if self.persist is None else self.persist

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

        if self._persisting:
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
        """Re-read, check and book inside one database transaction.

        The upload path used to do this as two awaits — `check_fresh()` and then,
        after opening the resumable session, `record()`. Two publishes starting
        together therefore both read the same "8,400 of 10,000 spent", both passed
        the check, and both booked 1,600: 11,600 units against a 10,000 ceiling,
        discovered only when Google refused the second one.

        The asyncio lock below is the *intra-process* fast path and nothing more.
        It cannot serialise the API process against the render worker, which is a
        separate process and the one that actually uploads — so an earlier version
        that held only this lock across a read-then-write still lost the race it
        was written to close, just between two processes instead of two tasks.

        The guarantee therefore lives at the database: `_reserve_locked` takes a
        day-scoped write lock, re-sums the day inside that transaction, and inserts
        only if the new spend fits. Whoever gets the lock second sees the first
        booking and is refused.

        Raises `QuotaExceeded` without booking anything when there is no room.
        """
        async with self._serialised():
            if not self._persisting:
                # Tests and scratch instances have no rows to lock; the in-process
                # lock is the whole of the guarantee, which is all they need.
                self.check(operation)
                return await self._record(operation, channel_id=channel_id, note=note)
            return await self._reserve_locked(operation, channel_id=channel_id, note=note)

    async def _reserve_locked(
        self,
        operation: str,
        *,
        channel_id: str = "",
        note: str = "",
    ) -> Entry:
        """One transaction: lock the day, sum it, insert only if it fits.

        Read, check and book were three separate sessions inside `reserve()`, which
        put two commit boundaries in the middle of the sequence this is supposed to
        make indivisible. They are one session here. The read-only forms
        (`_load`, `check`, `_record`) stay as they are — plenty of callers only ever
        display the number.
        """
        from sqlalchemy import select
        from sqlalchemy.exc import SQLAlchemyError

        from engine.db import session
        from engine.tables import QuotaEntry

        cost = self.cost_of(operation)
        at = datetime.now(UTC)
        day = quota_day(at)
        start, end = day_bounds(day)

        try:
            async with session() as s:
                await _lock_day(s, day)

                rows = (
                    (
                        await s.execute(
                            select(QuotaEntry)
                            .where(QuotaEntry.at >= start, QuotaEntry.at < end)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )

                # Entries this process is holding that the query above cannot see:
                # a write that failed. The units were spent at Google regardless, so
                # counting only the rows would hand that budget back — the same
                # merge `_load` does, for the same reason.
                carried = [e for e in self.entries if e.row_id is None and quota_day(e.at) == day]
                spent = sum(r.cost for r in rows) + sum(e.cost for e in carried)

                if spent + cost > self.limit:
                    # Raised inside the transaction, before any INSERT, so the
                    # rollback has nothing to undo.
                    raise QuotaExceeded(operation, cost, max(0, self.limit - spent))

                row = QuotaEntry(
                    operation=operation,
                    cost=cost,
                    at=at,
                    channel_id=channel_id,
                    note=note,
                )
                s.add(row)
                await s.flush()

                entry = Entry(
                    operation=operation,
                    cost=cost,
                    at=at,
                    channel_id=channel_id,
                    note=note,
                    row_id=row.id,
                )
                booked = [
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
        except QuotaExceeded:
            raise
        except SQLAlchemyError:
            # The booking never happened, so nothing is double-counted — but the
            # caller is about to spend real units and refusing on a database blip
            # would be worse than checking against the cache, which is the same
            # trade `check_fresh` makes.
            logger.exception(
                "could not reserve {} atomically; falling back to the cache", operation
            )
            self.check(operation)
            return await self._record(operation, at=at, channel_id=channel_id, note=note)

        # Refresh the cache from exactly what the transaction saw. Only this day is
        # replaced — the rest of the 35-day window the calendar charts is untouched,
        # since nothing outside the locked range was read.
        self.entries = [e for e in self.entries if quota_day(e.at) != day]
        self.entries.extend(booked)
        self.entries.extend(carried)
        self.entries.append(entry)
        return entry

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

            if self._persisting and entry.row_id is not None:
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
            if self._persisting:
                try:
                    await self._load()
                except Exception:  # noqa: BLE001
                    # Refusing to upload because the ledger could not be re-read would
                    # be worse than proceeding on a cache that is at worst incomplete.
                    logger.warning("could not refresh the quota ledger; checking against the cache")
            self.check(operation)

    async def refresh(self) -> None:
        """Re-read before a display read. Best effort, never raises.

        The cache is per-process and hydrated once at startup, which was correct
        while one process did everything. The render worker uploads, so it is the
        one accumulating spend, and the API's copy only moved when the API itself
        spent something — so `GET /v1/quota` reported yesterday's number
        indefinitely and the calendar planned around budget that was already gone.

        One indexed range query on a cold path. Every endpoint here is a page load
        or a planning call; none is in a render's inner loop.
        """
        if not self._persisting:
            return
        try:
            await self.load()
        except Exception:  # noqa: BLE001 — a stale number beats a 500 on a page load
            logger.warning("could not refresh the quota ledger; serving the cache")

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
