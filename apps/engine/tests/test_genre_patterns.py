"""Tests for `engine.genre.patterns` — hook classification and corpus aggregates.

The classifier is the part that reaches an LLM prompt ("the niche's winning
hooks are…"), so its precedence decisions are pinned case by case: a title that
matches several strategies must land in the one this module documents as
dominant, or the numbers the prompts quote quietly change meaning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.genre import patterns

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def video(
    title: str,
    *,
    days_ago: float = 10,
    views: int = 10_000,
    duration_s: float = 300.0,
    channel: str = "C1",
) -> dict:
    """A mining row in the shape the repository returns (aware datetimes)."""
    return {
        "video_id": f"v-{title[:12]}",
        "channel_label": channel,
        "title": title,
        "published_at": NOW - timedelta(days=days_ago),
        "duration_s": duration_s,
        "views": views,
        "likes": 0,
        "first_seen_at": NOW - timedelta(days=days_ago),
        "first_seen_views": views // 2,
    }


# ── classify_hook ────────────────────────────────────────────────────────────


def test_contrarian_beats_question():
    assert patterns.classify_hook("Why I Stopped Buying New Cars") == "contrarian"


def test_number_beats_question_and_curiosity():
    assert patterns.classify_hook("7 Reasons Why Dams Fail... Revealed") == "number"


def test_outcome_beats_question():
    assert patterns.classify_hook("How to Read Bridge Drawings") == "outcome"


def test_plain_question():
    assert patterns.classify_hook("Why Bridges Fail?") == "question"
    assert patterns.classify_hook("What Happens Inside a Reactor") == "question"


def test_curiosity_markers():
    assert patterns.classify_hook("The Room Nobody Talks About") == "curiosity"
    assert patterns.classify_hook("I Found This Under Rome And You Won't Believe It") == "curiosity"


def test_statement_is_the_honest_fallback():
    assert patterns.classify_hook("A Day in the Life of a Lighthouse Keeper") == "statement"


def test_empty_title_never_raises():
    assert patterns.classify_hook("") == "statement"


# ── views_per_day ─────────────────────────────────────────────────────────────


def test_velocity_is_views_over_age():
    v = video("x", days_ago=10, views=5_000)
    assert patterns.views_per_day(v, now=NOW) == 500.0


def test_naive_datetimes_are_treated_as_utc():
    naive = dict(video("x", days_ago=10, views=5_000))
    naive["published_at"] = naive["published_at"].replace(tzinfo=None)
    assert patterns.views_per_day(naive, now=NOW) == 500.0


def test_unmeasurable_rows_score_zero_not_a_crash():
    no_date = video("x")
    no_date["published_at"] = None
    future = video("y", days_ago=-3)  # still scheduled
    dead = video("z", views=0)
    for row in (no_date, future, dead):
        assert patterns.views_per_day(row, now=NOW) == 0.0


# ── analyze ───────────────────────────────────────────────────────────────────


def test_an_empty_corpus_is_zeroed_not_an_error():
    report = patterns.analyze([], now=NOW)
    assert report["video_count"] == 0
    assert report["hook_patterns"] == []
    assert report["median_duration_s"] is None
    assert report["uploads_per_week"] is None
    assert report["top_by_velocity"] == []


def test_pattern_shares_reflect_the_corpus():
    corpus = [
        video("7 Bridges That Failed", days_ago=5, views=90_000),
        video("10 Dams That Should Not Exist", days_ago=8, views=40_000),
        video("Why Steel Snaps", days_ago=30, views=5_000),
    ]
    report = patterns.analyze(corpus, now=NOW)
    by_name = {p["pattern"]: p for p in report["hook_patterns"]}
    assert by_name["number"]["count"] == 2
    assert by_name["number"]["share"] == 0.667
    # The number group's median velocity towers over the stale question title.
    assert by_name["question"]["median_views_per_day"] < by_name["number"]["median_views_per_day"]
    assert sum(p["share"] for p in report["hook_patterns"]) == 1.0


def test_top_by_velocity_is_sorted_descending():
    corpus = [
        video("slow old", days_ago=200, views=500_000),
        video("hot new", days_ago=2, views=80_000),
    ]
    top = patterns.analyze(corpus, now=NOW)["top_by_velocity"]
    assert [t["title"] for t in top] == ["hot new", "slow old"]


def test_cadence_counts_only_the_recent_window():
    recent = [video(f"recent {i}", days_ago=i + 1) for i in range(9)]
    stale = video("ancient", days_ago=400)
    report = patterns.analyze([*recent, stale], now=NOW)
    # 9 uploads inside 90 days ≈ 0.70/week; the year-old upload is excluded.
    assert report["uploads_per_week"] == round(9 / (90 / 7), 2)


def test_duration_buckets_split_three_ways():
    corpus = [
        video("short", duration_s=30),
        video("mid", duration_s=300),
        video("long", duration_s=900),
    ]
    buckets = patterns.analyze(corpus, now=NOW)["duration_buckets"]
    assert buckets == {"under_60s": 1, "60s_to_8m": 1, "over_8m": 1}
