"""Tests for FIX-TASKS E2: thumbnail A/B swapping.

`should_swap` and `pick_next_variant` are pure — no client, no database, no clock
but the one passed in — so they are tested directly, the same way `insights.
analyze`'s scoring is. `sweep()` is the thin orchestration layer around them;
tested separately, with everything it touches mocked or database-backed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine import thumbnail_ab
from engine.insights import VideoRecord
from engine.thumbnail_ab import (
    MIN_DAYS_BETWEEN_SWAPS,
    MIN_HOURS_SINCE_PUBLISH,
    channel_median_ctr,
    pick_next_variant,
    should_swap,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def video(*, ctr: float = 5.0, age_hours: float = 200, video_id: str = "v1") -> VideoRecord:
    return VideoRecord(
        video_id=video_id,
        title="t",
        published_at=(NOW - timedelta(hours=age_hours)).isoformat(),
        ctr=ctr,
    )


# ── channel_median_ctr ───────────────────────────────────────────────────────


def test_median_ignores_unmeasured_videos():
    """`ctr == 0.0` means "not measured yet", not a genuine zero — including it
    would drag the median toward zero as more freshly-published videos piled up."""
    records = [video(ctr=0.0), video(ctr=0.0), video(ctr=6.0)]
    assert channel_median_ctr(records) == 6.0


def test_median_of_no_measured_videos_is_zero():
    assert channel_median_ctr([video(ctr=0.0)]) == 0.0


def test_median_is_the_middle_value():
    records = [video(ctr=2.0), video(ctr=4.0), video(ctr=10.0)]
    assert channel_median_ctr(records) == 4.0


# ── should_swap: timing guardrails ──────────────────────────────────────────


def test_a_video_younger_than_48_hours_is_never_swapped():
    """CTR is still provisional in the first day or two — this guardrail is
    checked before the performance comparison even runs."""
    young = video(ctr=0.5, age_hours=10)  # would otherwise clearly underperform
    decision = should_swap(young, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is False
    assert "provisional" in decision.reason


def test_exactly_48_hours_is_old_enough():
    edge = video(ctr=1.0, age_hours=MIN_HOURS_SINCE_PUBLISH)
    decision = should_swap(edge, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is True


def test_a_recent_swap_blocks_another_one():
    old_enough = video(ctr=0.5, age_hours=1000)
    last_swap_at = NOW - timedelta(days=3)
    decision = should_swap(old_enough, channel_median=10.0, last_swap_at=last_swap_at, now=NOW)
    assert decision.should_swap is False
    assert "too soon" in decision.reason


def test_a_swap_exactly_14_days_ago_is_old_enough_for_another():
    old_enough = video(ctr=0.5, age_hours=1000)
    last_swap_at = NOW - timedelta(days=MIN_DAYS_BETWEEN_SWAPS)
    decision = should_swap(old_enough, channel_median=10.0, last_swap_at=last_swap_at, now=NOW)
    assert decision.should_swap is True


def test_never_swapped_before_does_not_block_anything():
    v = video(ctr=0.5, age_hours=1000)
    decision = should_swap(v, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is True


# ── should_swap: performance comparison ─────────────────────────────────────


def test_a_video_near_the_median_is_left_alone():
    """A raw '< median' threshold would flag roughly half of every channel's
    videos, forever — the margin exists so only a clear underperformer swaps."""
    v = video(ctr=9.0, age_hours=1000)  # 90% of a 10.0 median
    decision = should_swap(v, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is False
    assert "within range" in decision.reason


def test_a_video_well_below_the_median_is_swapped():
    v = video(ctr=2.0, age_hours=1000)  # 20% of a 10.0 median
    decision = should_swap(v, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is True
    assert "below" in decision.reason


def test_an_unmeasured_video_is_not_swapped():
    v = video(ctr=0.0, age_hours=1000)
    decision = should_swap(v, channel_median=10.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is False
    assert "not measured" in decision.reason


def test_no_channel_median_means_nothing_to_compare_against():
    """A brand-new channel with one video has no median yet — refusing to act is
    the honest answer, not comparing the video against itself."""
    v = video(ctr=5.0, age_hours=1000)
    decision = should_swap(v, channel_median=0.0, last_swap_at=None, now=NOW)
    assert decision.should_swap is False
    assert "no channel median" in decision.reason


# ── pick_next_variant ────────────────────────────────────────────────────────


def variants(*templates: str) -> list[dict]:
    return [{"template": t, "key": f"thumbnails/{t}.jpg"} for t in templates]


def test_picks_an_untried_variant_over_the_current_one():
    got = pick_next_variant(
        variants("before-after", "big-arrow", "text-only"),
        current_concept="before-after",
        tried_concepts=set(),
    )
    assert got["template"] in {"big-arrow", "text-only"}


def test_skips_variants_already_swapped_to():
    got = pick_next_variant(
        variants("before-after", "big-arrow", "text-only"),
        current_concept="before-after",
        tried_concepts={"big-arrow"},
    )
    assert got["template"] == "text-only"


def test_falls_back_to_a_repeat_once_everything_has_been_tried():
    """Every variant tried once should not permanently strand a video — it just
    means the choice is no longer "which is untried" but "which isn't live"."""
    got = pick_next_variant(
        variants("a", "b"),
        current_concept="a",
        tried_concepts={"b"},
    )
    assert got["template"] == "b"


def test_no_variant_differs_from_the_current_one_means_nothing_to_swap_to():
    got = pick_next_variant(variants("only-one"), current_concept="only-one", tried_concepts=set())
    assert got is None


# ── repository round trip ────────────────────────────────────────────────────


async def test_a_swap_is_recorded_and_read_back(database):
    from engine import repository

    await repository.record_thumbnail_swap(
        video_id="v1",
        from_concept="before-after",
        to_concept="big-arrow",
        variant_key="thumbnails/job-1.jpg",
        reason="ctr 2.00% is well below the 8.00% channel median",
    )
    last = await repository.last_thumbnail_swap("v1")
    assert last is not None
    assert last.to_concept == "big-arrow"

    history = await repository.thumbnail_swaps_for("v1")
    assert [s.to_concept for s in history] == ["big-arrow"]


async def test_last_swap_is_the_most_recent_one(database):
    from engine import repository

    await repository.record_thumbnail_swap(
        video_id="v1",
        from_concept="a",
        to_concept="b",
        variant_key="k1",
        reason="r1",
        at=NOW - timedelta(days=20),
    )
    await repository.record_thumbnail_swap(
        video_id="v1",
        from_concept="b",
        to_concept="c",
        variant_key="k2",
        reason="r2",
        at=NOW - timedelta(days=2),
    )
    last = await repository.last_thumbnail_swap("v1")
    assert last.to_concept == "c"

    history = await repository.thumbnail_swaps_for("v1")
    assert [s.to_concept for s in history] == ["b", "c"]  # oldest first


async def test_a_video_never_swapped_has_no_last_swap(database):
    from engine import repository

    assert await repository.last_thumbnail_swap("never-swapped") is None
    assert await repository.thumbnail_swaps_for("never-swapped") == []


async def test_job_id_for_video_reads_the_performance_record(database):
    from engine import repository
    from engine.insights import VideoRecord

    await repository.save_performance_record(
        VideoRecord(video_id="v1", title="t", published_at="2026-01-01T00:00:00+00:00"),
        job_id="job-42",
    )
    assert await repository.job_id_for_video("v1") == "job-42"


async def test_job_id_for_an_unknown_video_is_none(database):
    from engine import repository

    assert await repository.job_id_for_video("nope") is None


# ── worker registration ──────────────────────────────────────────────────────


class TestSchedule:
    def test_the_sweep_is_registered_as_an_hourly_cron_job(self):
        from engine.worker import WorkerSettings

        job = next(j for j in WorkerSettings.cron_jobs if j.name.endswith("thumbnail_swap_task"))
        # `hour=None` means "every hour" to arq; `minute`/`second` pinned to 0 is
        # what keeps that to once an hour rather than once a minute.
        assert job.hour is None
        assert job.minute == 0
        assert job.second == 0

    def test_it_does_not_fire_on_worker_restart(self):
        from engine.worker import WorkerSettings

        job = next(j for j in WorkerSettings.cron_jobs if j.name.endswith("thumbnail_swap_task"))
        assert job.run_at_startup is False

    def test_both_cron_jobs_coexist(self):
        from engine.worker import WorkerSettings

        names = [j.name for j in WorkerSettings.cron_jobs]
        assert len(names) == 2
        assert any(n.endswith("weekly_review_task") for n in names)
        assert any(n.endswith("thumbnail_swap_task") for n in names)


# ── sweep() orchestration ────────────────────────────────────────────────────


async def test_sweep_with_no_records_does_nothing(monkeypatch):
    from engine.api import insights as insights_api

    insights_api.RECORDS.clear()
    assert await thumbnail_ab.sweep() == []


async def test_sweep_with_no_connected_channel_reports_why(monkeypatch):
    """`scratch_state` (conftest.py, autouse) already disables persistence for
    every test, so `credentials_for` falls straight through to "not connected"
    without needing a database — this exercises exactly the path a real
    freshly-installed, not-yet-connected channel would hit."""
    from engine.api import insights as insights_api
    from engine.api import publishing as publishing_api

    insights_api.RECORDS.clear()
    # Two records so the median (5.5) is not just "v1 compared to itself" — v1
    # is clearly underperforming it, v2 is not.
    insights_api.RECORDS["v1"] = video(ctr=1.0, age_hours=1000, video_id="v1")
    insights_api.RECORDS["v2"] = video(ctr=10.0, age_hours=1000, video_id="v2")
    monkeypatch.setattr(publishing_api, "CHANNELS", {}, raising=False)

    try:
        results = await thumbnail_ab.sweep()
    finally:
        insights_api.RECORDS.clear()

    by_id = {r.video_id: r for r in results}
    assert by_id["v1"].swapped is False
    assert "no channel connected" in by_id["v1"].reason
    assert by_id["v2"].swapped is False
    assert "within range" in by_id["v2"].reason
