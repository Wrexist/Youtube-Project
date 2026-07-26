"""Scheduler and quota tests.

Both are pure logic that the calendar's promises rest on, so they get real coverage.
If the scheduler quietly double-books a day or the ledger disagrees with Google about
when "today" started, the failure shows up as a rejected upload hours later.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from engine.quota import DAILY_LIMIT, QuotaExceeded, QuotaLedger, quota_day
from engine.scheduling import (
    MAX_PUBLISHES_PER_DAY,
    AudienceProfile,
    Constraints,
    Pending,
    auto_schedule,
    candidate_slots,
    validate_move,
)

START = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)  # a Monday


# ── quota ───────────────────────────────────────────────────────────────────


async def test_upload_cost_caps_the_day():
    led = QuotaLedger(persist=False)
    for _ in range(6):
        await led.record("videos.insert")
    assert led.spent() == 9600
    assert led.remaining() == 400
    with pytest.raises(QuotaExceeded):
        led.check("videos.insert")


def test_uploads_left_counts_the_whole_publish_sequence():
    """Publishing a video then failing to set its thumbnail is not a useful outcome,
    so the estimate reserves the full sequence."""
    led = QuotaLedger(persist=False)
    # insert 1600 + thumbnail 50 + captions 400 = 2050 per publish
    assert led.uploads_left() == DAILY_LIMIT // 2050 == 4


async def test_search_competes_with_uploads_for_the_same_budget():
    led = QuotaLedger(persist=False)
    for _ in range(40):
        await led.record("search.list")  # 100 each — competitor mining
    assert led.spent() == 4000
    assert led.uploads_left() == 2  # halved by research


def test_quota_day_uses_pacific_not_utc():
    """01:00 UTC is still the previous day in Pacific. Getting this wrong means the
    ledger and Google disagree for eight hours every day."""
    early_utc = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)
    assert quota_day(early_utc) == date(2026, 8, 3)


async def test_breakdown_attributes_spend():
    led = QuotaLedger(persist=False)
    await led.record("videos.insert")
    await led.record("search.list")
    await led.record("search.list")
    assert led.breakdown() == {"videos.insert": 1600, "search.list": 200}


# ── scheduling ──────────────────────────────────────────────────────────────


def test_slots_are_ranked_and_lead_the_audience_peak():
    slots = candidate_slots(START, 7, AudienceProfile())
    assert slots == sorted(slots, key=lambda s: s.score, reverse=True)
    # Default curve peaks at 19:00, so the best slot publishes two hours earlier.
    assert slots[0].at.hour == 17


def test_measured_profile_is_labelled_differently_from_the_default():
    default = candidate_slots(START, 2, AudienceProfile())[0]
    assert "estimated" in default.reason

    measured = AudienceProfile.from_analytics([1.0] * 24, [1.0] * 7)
    assert "measured" in candidate_slots(START, 2, measured)[0].reason


def test_respects_weekly_cadence():
    pending = [Pending(id=f"s{i}", title=f"short {i}") for i in range(10)]
    plan = auto_schedule(
        pending, start=START, constraints=Constraints(shorts_per_week=3), horizon_days=14
    )
    by_week: dict[int, int] = {}
    for a in plan.assignments:
        week = a.at.isocalendar()[1]
        by_week[week] = by_week.get(week, 0) + 1
    assert all(count <= 3 for count in by_week.values())


def test_enforces_minimum_gap_between_uploads():
    pending = [Pending(id=f"s{i}", title=f"s{i}") for i in range(6)]
    plan = auto_schedule(pending, start=START, horizon_days=28)
    times = sorted(a.at for a in plan.assignments)
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(times, times[1:], strict=False)]
    assert gaps and all(g >= 20 for g in gaps), gaps


def test_long_form_gets_first_pick():
    """Long-form is more expensive to produce and benefits more from a good slot."""
    pending = [
        Pending(id="short", title="a short", format="short"),
        Pending(id="long", title="a long one", format="long"),
    ]
    plan = auto_schedule(pending, start=START, horizon_days=14)
    best = max(plan.assignments, key=lambda a: a.score)
    assert best.video_id == "long"


def test_never_exceeds_the_daily_publish_ceiling():
    pending = [Pending(id=f"v{i}", title=f"v{i}") for i in range(12)]
    plan = auto_schedule(
        pending,
        start=START,
        constraints=Constraints(shorts_per_week=99, min_gap_hours=1),
        horizon_days=3,
    )
    per_day: dict[date, int] = {}
    for a in plan.assignments:
        per_day[a.at.date()] = per_day.get(a.at.date(), 0) + 1
    assert all(n <= MAX_PUBLISHES_PER_DAY for n in per_day.values())


def test_unplaceable_videos_are_reported_not_dropped():
    pending = [Pending(id=f"v{i}", title=f"v{i}") for i in range(8)]
    plan = auto_schedule(
        pending, start=START, constraints=Constraints(shorts_per_week=1), horizon_days=7
    )
    assert len(plan.assignments) + len(plan.unplaced) == 8
    assert plan.unplaced and "cadence" in plan.unplaced[0][1]


def test_a_video_is_not_scheduled_before_it_is_ready():
    ready = START + timedelta(days=5)
    plan = auto_schedule([Pending(id="v", title="v", ready_at=ready)], start=START, horizon_days=14)
    assert plan.assignments[0].at >= ready


def test_existing_schedule_is_respected():
    existing = [START + timedelta(days=1, hours=9)]
    plan = auto_schedule(
        [Pending(id="v", title="v")], start=START, existing=existing, horizon_days=14
    )
    gap = abs((plan.assignments[0].at - existing[0]).total_seconds()) / 3600
    assert gap >= 20


# ── manual drags ────────────────────────────────────────────────────────────


def test_move_into_the_past_is_refused():
    ok, msg = validate_move(START - timedelta(hours=1), existing=[], now=START)
    assert not ok and "passed" in msg


def test_move_onto_an_exhausted_day_is_refused():
    day = (START + timedelta(days=1)).date()
    ok, msg = validate_move(
        START + timedelta(days=1, hours=9),
        existing=[],
        quota_used_by_day={day: DAILY_LIMIT},
        now=START,
    )
    assert not ok and "quota" in msg


def test_a_tight_gap_is_allowed_but_warned_about():
    """A permitted move that is merely a bad idea should say so rather than silently
    being accepted."""
    other = START + timedelta(days=1, hours=9)
    ok, msg = validate_move(other + timedelta(hours=3), existing=[other], now=START)
    assert ok and "compete" in msg


def test_a_clean_move_returns_no_message():
    other = START + timedelta(days=1, hours=9)
    ok, msg = validate_move(other + timedelta(days=3), existing=[other], now=START)
    assert ok and msg == ""
