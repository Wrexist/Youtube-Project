"""What the channel has cost, over time.

Cost has always been metered per stage and capped per video, and nothing could
answer "what have I spent this month" — the question that decides whether this is
usable at volume rather than once.

Read off the `jobs` table rather than out of `automation.SpendLedger`. The ledger is
in-memory, series-scoped, and written by nothing; the jobs table is where `cost_usd`
is actually recorded, by the same stage boundary that spends the money.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from engine import repository
from engine.workflows import video
from engine.workflows.base import Provenance, StageOutput, StageStatus


@pytest.fixture
def client(database):
    from engine.main import app

    with TestClient(app) as c:
        yield c


async def _job(job_id: str, *, cost: float, days_ago: int, status: str, workflow: str = "video"):
    """A finished job row, backdated.

    The cost goes on a *stage output*, not on a top-level `cost_usd`. `save_job`
    writes `row.cost_usd = _cost_of(job)`, which sums what the stages actually
    spent — so the column is derived from the real ledger rather than from
    whatever a caller claims, and a test that sets the shortcut measures nothing.

    Written through `save_job` and then aged with a direct UPDATE: `created_at`
    has a server-side default, so it cannot be set on the way in.
    """
    flow = video.get(workflow)
    states = flow.initial_states()
    first = next(iter(states))
    states[first].status = StageStatus.DONE
    states[first].output = StageOutput(
        value={"note": "spent"}, provenance=Provenance(), cost_usd=cost
    )

    await repository.save_job(
        {
            "id": job_id,
            "workflow": flow,
            "status": status,
            "inputs": {"topic": "why bridges collapse"},
            "states": states,
            "events": [],
            "cost_usd": cost,
        }
    )
    from sqlalchemy import update

    from engine.db import session
    from engine.tables import Job

    when = datetime.now(UTC) - timedelta(days=days_ago)
    async with session() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(created_at=when))


async def test_no_jobs_is_zero_not_an_error(client):
    body = client.get("/v1/spend").json()

    assert body["days"] == []
    assert body["total_usd"] == 0
    # None, not 0.0. "No videos yet" and "videos that cost nothing" are different
    # claims and a screen renders them differently.
    assert body["per_video_usd"] is None
    assert body["completed_videos"] == 0


async def test_it_totals_by_day_oldest_first(client):
    await _job("a", cost=1.02, days_ago=2, status="completed")
    await _job("b", cost=0.41, days_ago=2, status="completed")
    await _job("c", cost=1.27, days_ago=0, status="failed")

    body = client.get("/v1/spend").json()

    assert [d["date"] for d in body["days"]] == sorted(d["date"] for d in body["days"])
    two_days_ago = next(d for d in body["days"] if d["jobs"] == 2)
    assert two_days_ago["usd"] == pytest.approx(1.43)
    # A failed run still spent the money. Excluding it would understate the bill.
    assert body["total_usd"] == pytest.approx(2.70)


async def test_the_per_video_average_counts_only_finished_videos(client):
    await _job("done-1", cost=1.00, days_ago=1, status="completed")
    await _job("done-2", cost=2.00, days_ago=1, status="completed")
    await _job("lost", cost=1.27, days_ago=1, status="failed")

    body = client.get("/v1/spend").json()

    # 1.50, not 1.42: a failed run is not a video, so averaging it in answers a
    # different question than "what does a video cost me". It stays in the total.
    assert body["per_video_usd"] == pytest.approx(1.50)
    assert body["completed_videos"] == 2
    assert body["total_usd"] == pytest.approx(4.27)


async def test_a_publish_job_does_not_dilute_the_average(client):
    """A publish is a separate row costing almost nothing. Counting it would halve
    the apparent price of a video by adding a near-zero sample."""
    await _job("vid", cost=2.00, days_ago=1, status="completed")
    await _job("pub", cost=0.00, days_ago=1, status="completed", workflow="publish")

    body = client.get("/v1/spend").json()

    assert body["per_video_usd"] == pytest.approx(2.00)
    assert body["completed_videos"] == 1


async def test_the_window_excludes_older_jobs(client):
    await _job("old", cost=9.99, days_ago=40, status="completed")
    await _job("new", cost=1.00, days_ago=1, status="completed")

    body = client.get("/v1/spend?days=7").json()

    assert body["total_usd"] == pytest.approx(1.00)
    assert len(body["days"]) == 1


async def test_the_window_is_bounded(client):
    """A caller asking for ten years would scan the whole table."""
    assert client.get("/v1/spend?days=0").status_code == 422
    assert client.get("/v1/spend?days=9999").status_code == 422
