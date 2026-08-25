"""Series endpoints: standing configs for a repeatable format.

This is the endpoint the Series screen shipped without — its primary action was
`disabled` with the reason "the series endpoint does not exist yet". A series is
cadence + budget + auto-publish, persisted; the interesting read is `GET
/{series_id}/plan`, which is the first production caller `automation.plan_week`
has ever had: it joins the series config to the real backlog and the real spend
recorded on the jobs table, and says what to generate this week and what stopped
more.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine import automation, repository
from engine.ideas import Idea

router = APIRouter(prefix="/v1/series", tags=["series"])


class SeriesIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    niche: str = Field(default="", max_length=300)
    monthly_budget_usd: float = Field(default=30.0, ge=0)
    shorts_per_week: int = Field(default=3, ge=0, le=21)
    long_per_week: int = Field(default=1, ge=0, le=7)
    auto_publish: bool = False


class SeriesPatch(BaseModel):
    """Partial update. Only the fields present are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    niche: str | None = Field(default=None, max_length=300)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    shorts_per_week: int | None = Field(default=None, ge=0, le=21)
    long_per_week: int | None = Field(default=None, ge=0, le=7)
    auto_publish: bool | None = None
    paused: bool | None = None


class SeriesOut(BaseModel):
    id: str
    name: str
    niche: str
    monthly_budget_usd: float
    shorts_per_week: int
    long_per_week: int
    auto_publish: bool
    paused: bool
    created_at: datetime | None = None
    # Read off the jobs table — jobs carry `series_id` in their inputs, and
    # `cost_usd` is written at the same stage boundary that spends the money.
    spent_today_usd: float = 0.0
    spent_this_month_usd: float = 0.0
    produced_this_week: int = 0
    #: Open ideas in the backlog. The backlog is shared across series — ideas are
    #: not series-scoped — so every series reports the same pool. Honest, and the
    #: number the cadence check actually runs against.
    backlog_depth: int = 0


class BlockerOut(BaseModel):
    code: str
    message: str


class PlanIdea(BaseModel):
    topic: str
    score: float
    why: str = ""


class PlanOut(BaseModel):
    series_id: str
    week_of: str
    to_generate: list[PlanIdea]
    blocked: list[BlockerOut]
    already_this_week: int


def _series_from(record: dict) -> automation.Series:
    return automation.Series(
        id=record["id"],
        name=record["name"],
        niche=record["niche"],
        monthly_budget_usd=record["monthly_budget_usd"],
        shorts_per_week=record["shorts_per_week"],
        long_per_week=record["long_per_week"],
        auto_publish=record["auto_publish"],
        paused=record["paused"],
    )


async def _out(record: dict, usage: dict | None = None, backlog_depth: int = 0) -> SeriesOut:
    usage = usage or {}
    return SeriesOut(
        **record,
        spent_today_usd=usage.get("spent_today", 0.0),
        spent_this_month_usd=usage.get("spent_this_month", 0.0),
        produced_this_week=usage.get("produced_this_week", 0),
        backlog_depth=backlog_depth,
    )


@router.get("")
async def list_all() -> list[SeriesOut]:
    records = await repository.list_series()
    usage = await repository.series_usage()
    depth = len(await repository.open_backlog_ideas(limit=100))
    return [await _out(r, usage.get(r["id"]), depth) for r in records]


@router.post("", status_code=201)
async def create(body: SeriesIn) -> SeriesOut:
    record = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(UTC),
        "paused": False,
        **body.model_dump(),
    }
    await repository.save_series(record)
    stored = await repository.get_series(record["id"]) or record
    return await _out(stored)


@router.patch("/{series_id}")
async def patch(series_id: str, body: SeriesPatch) -> SeriesOut:
    record = await repository.get_series(series_id)
    if record is None:
        raise HTTPException(404, "unknown series")
    changes = body.model_dump(exclude_none=True)
    record.update(changes)
    await repository.save_series(record)
    usage = await repository.series_usage()
    depth = len(await repository.open_backlog_ideas(limit=100))
    return await _out(record, usage.get(series_id), depth)


@router.delete("/{series_id}", status_code=204)
async def remove(series_id: str) -> None:
    if not await repository.delete_series(series_id):
        raise HTTPException(404, "unknown series")


@router.get("/{series_id}/plan")
async def plan(series_id: str) -> PlanOut:
    """What this series should generate this week, and what stopped more.

    `plan_week` is pure and this endpoint is its production caller: the ledger is
    rebuilt from the jobs table (the record that is actually true — see
    `repository.series_usage`), the ideas come from the shared backlog, and
    `already_this_week` is what the series has really produced since Monday.
    """
    record = await repository.get_series(series_id)
    if record is None:
        raise HTTPException(404, "unknown series")

    usage = (await repository.series_usage()).get(series_id) or {}
    ledger = automation.SpendLedger()
    spent_today = usage.get("spent_today", 0.0)
    spent_month = usage.get("spent_this_month", 0.0)
    if spent_today:
        ledger.record(series_id, spent_today)
    # The remainder of the month's spend, back-dated inside the current month so
    # `spent_this_month` sees it and `spent_today` does not double-count it.
    rest = spent_month - spent_today
    if rest > 0:
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ledger.record(series_id, rest, at=month_start)

    ideas = [
        Idea(
            topic=raw["topic"],
            source="series",
            demand=raw["demand"],
            competition=raw["competition"],
            notes=raw.get("why", ""),
        )
        for raw in await repository.open_backlog_ideas(limit=100)
    ]

    week = automation.plan_week(
        _series_from(record),
        ideas,
        ledger,
        automation.BudgetPolicy(),
        already_this_week=usage.get("produced_this_week", 0),
    )

    now = datetime.now(UTC)
    week_of = (now.date() - timedelta(days=now.weekday())).isoformat()
    return PlanOut(
        series_id=series_id,
        week_of=week_of,
        to_generate=[
            PlanIdea(topic=i.topic, score=round(i.score_at(now), 3), why=i.notes)
            for i in week.to_generate
        ],
        blocked=[BlockerOut(code=b.code, message=b.message) for b in week.blocked],
        already_this_week=usage.get("produced_this_week", 0),
    )
