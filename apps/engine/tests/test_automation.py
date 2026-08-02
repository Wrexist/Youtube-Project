"""Automation tests.

These cover the three brakes that make unattended generation safe: duplicate
detection, spend ceilings, and the approval gate. Each one fails expensively and
quietly if it regresses — a broken duplicate check produces four versions of the
same video, a broken ceiling produces a bill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.automation import (
    BudgetPolicy,
    Series,
    SpendLedger,
    Stage,
    VideoState,
    check_budget,
    plan_week,
    publish_blockers,
    resolve_stage,
)
from engine.ideas import Idea, IdeaStatus, build_backlog, find_duplicate, next_up, similarity

# ── duplicate detection ─────────────────────────────────────────────────────


def test_rewordings_of_the_same_topic_are_caught():
    """The failure nobody anticipates: the generator repeating itself in new words."""
    assert similarity("why bridges collapse", "the reason bridges collapse") > 0.45


def test_genuinely_different_topics_are_not_confused():
    assert similarity("why bridges collapse", "why dams fail") < 0.45
    assert similarity("how salt built cities", "why elevators have mirrors") < 0.2


def test_stopwords_do_not_inflate_similarity():
    """Without stopword removal these share 'the/of/a' and score as related."""
    assert similarity("the history of the compass", "a study of the violin") < 0.2


def test_duplicates_are_marked_not_silently_dropped():
    backlog = build_backlog(
        ["why bridges collapse", "how salt built cities"],
        published_topics=["the reason bridges collapse"],
        suggestions=["why bridges collapse", "bridge failure"],
    )
    duplicate = next(i for i in backlog if i.topic == "why bridges collapse")
    assert duplicate.status is IdeaStatus.REJECTED
    assert duplicate.duplicate_of == "the reason bridges collapse"
    assert "duplicate of" in duplicate.summary()  # visible to the user


def test_duplicates_within_one_batch_are_caught():
    """Otherwise a single run emits three phrasings of one idea and none of them
    looks like a duplicate of anything already published."""
    backlog = build_backlog(
        ["why bridges collapse", "why bridges collapse suddenly", "how glass is made"],
        published_topics=[],
        suggestions=[],
    )
    rejected = [i for i in backlog if i.status is IdeaStatus.REJECTED]
    assert len(rejected) == 1


def test_no_duplicate_returns_the_near_miss_score():
    match, score = find_duplicate("why volcanoes erupt", ["why bridges collapse"])
    assert match is None
    assert score < 0.45


# ── idea scoring ────────────────────────────────────────────────────────────


def test_demand_comes_from_real_queries_not_a_guess():
    suggestions = [f"bridge collapse {i}" for i in range(20)]
    strong = build_backlog(["bridge collapse"], published_topics=[], suggestions=suggestions)[0]
    weak = build_backlog(["obscure topic"], published_topics=[], suggestions=suggestions)[0]
    assert strong.demand > weak.demand
    assert weak.demand == 0.0


def test_zero_competitors_is_treated_as_a_warning_not_an_opening():
    idea = build_backlog(
        ["a topic nobody covers"],
        published_topics=[],
        suggestions=[],
        competitor_counts={"a topic nobody covers": 0},
    )[0]
    assert idea.competition == 0.7
    assert "no audience" in idea.notes


def test_score_components_are_visible():
    idea = build_backlog(["x topic"], published_topics=[], suggestions=[])[0]
    assert "demand" in idea.summary() and "fit" in idea.summary()


def test_stale_ideas_are_skipped():
    """A topic scored against six-week-old search data is scored against data that
    has moved."""
    old = Idea(topic="old", created_at=datetime.now(UTC) - timedelta(days=60))
    fresh = Idea(topic="fresh")
    assert [i.topic for i in next_up([old, fresh], 5)] == ["fresh"]


# ── spend ceilings ──────────────────────────────────────────────────────────


def series() -> Series:
    return Series(id="s1", name="Engineering", niche="engineering", monthly_budget_usd=50)


def test_a_video_over_the_per_video_cap_is_refused():
    blockers = check_budget(series(), SpendLedger(), BudgetPolicy(), estimate_usd=12.0)
    assert any(b.code == "per_video_cap" for b in blockers)


def test_the_daily_ceiling_is_enforced_across_series():
    ledger = SpendLedger()
    ledger.record("other-series", 38.0)
    blockers = check_budget(series(), ledger, BudgetPolicy(per_day_usd=40), estimate_usd=5.0)
    assert any(b.code == "daily_cap" for b in blockers)


def test_the_series_monthly_budget_is_enforced():
    ledger = SpendLedger()
    ledger.record("s1", 48.0)
    blockers = check_budget(series(), ledger, BudgetPolicy(), estimate_usd=5.0)
    assert any(b.code == "series_budget" for b in blockers)


def test_last_months_spend_does_not_count_against_this_month():
    ledger = SpendLedger()
    ledger.record("s1", 48.0, at=datetime.now(UTC) - timedelta(days=45))
    assert check_budget(series(), ledger, BudgetPolicy(), estimate_usd=5.0) == []


# ── approval gate ───────────────────────────────────────────────────────────


def clean_video() -> VideoState:
    return VideoState(
        id="v1",
        series_id="s1",
        cost_usd=2.4,
        has_sources=True,
        source_count=8,
        has_thumbnail=True,
        has_seo=True,
        keyword_grounded=True,
        render_ok=True,
        title="A perfectly reasonable title",
        critique_severity=2,
    )


def test_a_clean_video_still_waits_for_review_by_default():
    stage, blockers = resolve_stage(clean_video(), series())
    assert stage is Stage.NEEDS_REVIEW
    assert blockers == []


def test_auto_publish_skips_the_wait_not_the_checks():
    auto = series()
    auto.auto_publish = True
    stage, _ = resolve_stage(clean_video(), auto)
    assert stage is Stage.APPROVED

    ungrounded = clean_video()
    ungrounded.has_sources = False
    ungrounded.source_count = 0
    stage, blockers = resolve_stage(ungrounded, auto)
    assert stage is Stage.NEEDS_REVIEW
    assert any(b.code == "ungrounded" for b in blockers)


def test_a_paused_series_never_auto_publishes():
    paused = series()
    paused.auto_publish = True
    paused.paused = True
    assert resolve_stage(clean_video(), paused)[0] is Stage.NEEDS_REVIEW


def test_every_blocker_carries_a_readable_reason():
    """'Blocked' with no reason is not an acceptable thing to show a user."""
    video = VideoState(id="v", series_id="s1")
    blockers = publish_blockers(video, series())
    assert blockers
    assert all(len(b.message) > 20 for b in blockers)


def test_an_overlong_title_is_caught_before_the_api_rejects_it():
    video = clean_video()
    video.title = "x" * 120
    assert any(b.code == "title_too_long" for b in publish_blockers(video, series()))


def test_a_weak_script_blocks_publication():
    video = clean_video()
    video.critique_severity = 5
    assert any(b.code == "weak_script" for b in publish_blockers(video, series()))


# ── run planning ────────────────────────────────────────────────────────────


def backlog(n: int) -> list[Idea]:
    return [Idea(topic=f"topic {i}", demand=0.8) for i in range(n)]


def test_plans_up_to_the_weekly_cadence():
    plan = plan_week(series(), backlog(20), SpendLedger(), BudgetPolicy())
    assert len(plan.to_generate) == 4  # 3 shorts + 1 long


def test_already_generated_videos_count_against_the_cadence():
    plan = plan_week(series(), backlog(20), SpendLedger(), BudgetPolicy(), already_this_week=3)
    assert len(plan.to_generate) == 1


def test_a_thin_backlog_produces_fewer_videos_and_says_so():
    """Publishing something weak on schedule is worse than publishing nothing."""
    plan = plan_week(series(), backlog(2), SpendLedger(), BudgetPolicy())
    assert len(plan.to_generate) == 2
    assert any(b.code == "thin_backlog" for b in plan.blocked)


def test_budget_caps_the_cadence_and_explains_itself():
    ledger = SpendLedger()
    # `BudgetPolicy()` leaves the daily ceiling at infinity, so the monthly budget is
    # the only brake here — the daily one gets its own test below. Recorded *now*
    # rather than five days ago: `spent_this_month` compares calendar months, so on
    # any of a month's first five days the spend landed in the previous one and this
    # test failed for reasons that had nothing to do with budgets.
    ledger.record("s1", 45.0)
    plan = plan_week(series(), backlog(20), ledger, BudgetPolicy())
    assert len(plan.to_generate) == 2  # $5 left at $2.50 each
    assert any(b.code == "budget_limits_cadence" for b in plan.blocked)


def test_the_daily_ceiling_bites_before_the_monthly_one():
    ledger = SpendLedger()
    ledger.record("s1", 45.0)  # today
    plan = plan_week(series(), backlog(20), ledger, BudgetPolicy(per_day_usd=40))
    assert plan.to_generate == []
    assert any(b.code == "daily_cap" for b in plan.blocked)


def test_a_paused_series_generates_nothing():
    paused = series()
    paused.paused = True
    plan = plan_week(paused, backlog(20), SpendLedger(), BudgetPolicy())
    assert plan.to_generate == []
    assert plan.blocked[0].code == "paused"


def test_duplicates_never_reach_a_run_plan():
    ideas = build_backlog(
        ["why bridges collapse", "the reason bridges collapse", "how glass is made"],
        published_topics=[],
        suggestions=[],
    )
    plan = plan_week(series(), ideas, SpendLedger(), BudgetPolicy())
    topics = [i.topic for i in plan.to_generate]
    assert "the reason bridges collapse" not in topics
