"""The quota ceiling, held against real operating-system processes.

`test_security.py` already proves `reserve()` admits exactly one of two
*coroutines* competing for the last 1,600 units. That test passes with nothing but
an `asyncio.Lock` behind it, and an `asyncio.Lock` is a per-process object — so it
proves nothing at all about the shape this system actually runs in, which is an API
process and one or more arq workers sharing a database and both uploading.

The failure being pinned: two processes each `_load()` the same "8,400 of 10,000
spent", each pass `check()`, and each `INSERT` 1,600. The row count is right, the
sum is 11,600, and nobody finds out until Google refuses an upload it has already
charged for. Nothing short of a second process reproduces it — which is why this
module forks real ones instead of gathering coroutines.

No external service: the ledger is a SQLite file in `tmp_path` unless
`STUDIO_TEST_DATABASE_URL` says otherwise, so this runs in CI as an ordinary test.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from datetime import UTC, datetime

import pytest

UPLOAD = "videos.insert"
UPLOAD_COST = 1_600


def _reserve_in_child(db_url: str, limit: int, barrier, results) -> None:
    """One process's attempt at the last upload slot. Runs after `spawn` re-imports.

    Module level and picklable-by-name because the context is `spawn`, not `fork`:
    fork would inherit the parent's already-open SQLite handles and its event loop,
    and inheriting the thing under test is how a concurrency test comes to prove
    nothing. `spawn` starts a genuinely fresh interpreter, so the child's ledger is
    hydrated from the database exactly the way a worker's is.

    Configuration travels as environment rather than as an argument: `QuotaLedger`
    reads its limit from `get_settings()`, and the settings object is built on first
    use in this new interpreter.
    """
    os.environ["STUDIO_DATABASE_URL"] = db_url
    os.environ["STUDIO_PERSIST"] = "true"
    os.environ["STUDIO_YOUTUBE_DAILY_QUOTA"] = str(limit)

    import asyncio

    from engine.quota import QuotaExceeded, QuotaLedger

    async def attempt() -> str:
        ledger = QuotaLedger()
        # Hydrate before the barrier and throw the result away. The first database
        # call in a process pays for the connection, the driver import and the page
        # cache — tens of milliseconds against the microseconds this race turns on —
        # so without a warm-up the "simultaneous" children are nothing of the sort
        # and the loser reads the winner's committed row. That is a broken ledger
        # passing the test.
        await ledger.load()

        # Every child parked here until the last one arrives, so the reservations
        # overlap. Without it the processes start milliseconds apart and the second
        # one reads the first one's committed row — which is the case that was never
        # broken.
        barrier.wait(timeout=60)
        try:
            await ledger.reserve(UPLOAD)
        except QuotaExceeded:
            return "refused"
        return "admitted"

    try:
        results.put(asyncio.run(attempt()))
    except BaseException as exc:  # noqa: BLE001 — a silent child is an unreadable failure
        results.put(f"error: {type(exc).__name__}: {exc}")


async def _preload(units: int) -> None:
    """Book `units` against today, as spend some other process already made."""
    from engine.db import session
    from engine.tables import QuotaEntry

    async with session() as s:
        s.add(QuotaEntry(operation="preload", cost=units, at=datetime.now(UTC), note="fixture"))


async def _recorded_total() -> int:
    """The day's spend re-read from the database, not from anybody's cache."""
    from sqlalchemy import func, select

    from engine.db import session
    from engine.tables import QuotaEntry

    async with session() as s:
        return int((await s.execute(select(func.sum(QuotaEntry.cost)))).scalar() or 0)


async def _race(db_url: str, *, limit: int, processes: int) -> list[str]:
    """Start `processes` children on one ledger and collect their verdicts."""
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(processes)
    results = ctx.Queue()

    children = [
        ctx.Process(target=_reserve_in_child, args=(db_url, limit, barrier, results))
        for _ in range(processes)
    ]
    for child in children:
        child.start()

    # Drained before `join`, not after: a `multiprocessing.Queue` is backed by a pipe
    # a feeder thread writes to, and a child that has written more than the pipe
    # buffer holds cannot exit until somebody reads. Joining first would deadlock.
    verdicts = [results.get(timeout=120) for _ in children]
    for child in children:
        child.join(timeout=30)
        assert child.exitcode == 0, f"a child died with exit code {child.exitcode}"

    assert not [v for v in verdicts if v.startswith("error:")], verdicts
    return verdicts


async def test_two_processes_cannot_both_take_the_last_upload(database):
    """8,400 spent, 10,000 allowed, two processes wanting 1,600 each.

    Exactly one may pass, and the database must end the day on 10,000 — not 11,600.
    The sum is the assertion that matters: a fix that refuses one caller but still
    writes both rows has not fixed anything, because the ceiling lives in the rows.
    """
    await _preload(8_400)

    verdicts = await _race(database, limit=10_000, processes=2)

    assert sorted(verdicts) == ["admitted", "refused"], verdicts
    assert await _recorded_total() == 10_000, "the ceiling was breached in the database"


async def test_four_processes_contending_for_one_slot_admit_one(database):
    """The same race with the odds against it: an empty ledger, a 1,600 ceiling,
    and four processes reaching for the only upload of the day.

    Two processes can be admitted by luck of scheduling even against a broken
    reservation; four make a passing run on a broken one vanishingly unlikely, which
    is what keeps this from being a test that only fails on a slow machine.
    """
    verdicts = await _race(database, limit=UPLOAD_COST, processes=4)

    assert verdicts.count("admitted") == 1, verdicts
    assert verdicts.count("refused") == 3, verdicts
    assert await _recorded_total() == UPLOAD_COST


# ── the ledger a second process starts with ─────────────────────────────────
#
# `reserve()` above closes the race between two processes booking at the same
# moment. This is the slower version of the same problem: a process that starts
# with an *empty* cache. `check()` and `can_afford()` are synchronous and read that
# cache, so until something hydrates it the process believes the whole day's budget
# is available — the API's `lifespan` calls `ledger.load()` for exactly this reason
# and the worker's `startup()` did not.


@pytest.fixture
def pristine_ledger():
    """The module singleton, emptied for the test and restored afterwards.

    `engine.quota.ledger` is process-wide and every other test in the suite shares
    it, so leaving 10,000 spent units in it would fail whichever test ran next.

    `persist` is restored along with the entries, and set, because it is sticky in
    the other direction: `lifespan` does `ledger.persist = False` under
    `STUDIO_PERSIST=false` and never puts it back, so any earlier test that stood
    the app up through `TestClient` leaves this singleton unable to read the
    database at all. That is invisible in isolation and shows up only as "0 == 8000"
    when the whole suite runs.
    """
    from engine.quota import ledger

    saved, persisted = list(ledger.entries), ledger.persist
    ledger.entries, ledger.persist = [], True
    yield ledger
    ledger.entries, ledger.persist = saved, persisted


async def test_the_worker_hydrates_the_ledger_before_it_runs_anything(database, pristine_ledger):
    """A worker that starts blank approves an upload the day has no room for.

    The worker process is the one that *uploads*, so this is not a display bug: it
    reads `can_afford` off its own empty cache, spends 1,600 units Google has
    already refused, and finds out from the API error. Hydrating at startup is the
    same thing the API's lifespan has always done.
    """
    from engine import worker
    from engine.quota import QuotaExceeded

    await _preload(10_000)
    assert pristine_ledger.spent() == 0, "the premise: a fresh process knows nothing"
    assert pristine_ledger.can_afford(UPLOAD) is True, "the premise: and would say yes"

    await worker.startup({})

    assert pristine_ledger.spent() == 10_000
    with pytest.raises(QuotaExceeded):
        pristine_ledger.check(UPLOAD)


async def test_the_quota_endpoint_reflects_spend_from_another_process(database, pristine_ledger):
    """`GET /v1/quota` is the number the UI shows and the operator plans against.

    It summed an in-memory cache that only this process ever wrote to, so every
    upload the worker made was invisible until the API was restarted — the screen
    said six uploads left on a day that had none.
    """
    import httpx

    from engine.main import app

    await _preload(8_000)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://engine.test") as client:
        body = (await client.get("/v1/quota")).json()

    assert body["spent"] == 8_000, "spend made outside this process was not counted"
    assert body["remaining"] == 2_000
    assert body["uploads_left"] == 0, "2,000 units is not a whole publish sequence"
