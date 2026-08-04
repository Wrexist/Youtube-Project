"""Progress toward the Partner Programme.

The arithmetic gets the weight here because it is the part that can actually be
proven: no Google account has ever been connected to this repository
(KNOWN-ISSUES §1.1), so the API calls in `providers/analytics.py` and
`providers/youtube.py` remain reviewed code rather than exercised code. What is
under test is everything downstream of the numbers arriving.

The failure this module exists to prevent is not a crash. It is a plausible wrong
number: a four-week-old channel shown "312 of 4,000 hours" as though that were its
trailing-12-month total, an operator planning a launch around it, and nobody
finding out until Google disagrees.
"""

from __future__ import annotations

from datetime import date, timedelta

from engine.monetisation import (
    SHORTS_VIEWS_REQUIRED,
    SUBSCRIBERS_REQUIRED,
    WATCH_HOURS_REQUIRED,
    Threshold,
    progress,
)

TODAY = date(2026, 8, 4)


def days_back(n: int, minutes: float) -> dict[date, float]:
    """The `n` most recent days *including today*, each with the same minutes.

    Including today matters for the boundary: a 365-day window holds 365 distinct
    days ending today, so 365 days ending *yesterday* is one short of filling it
    and `covers_full_window` correctly stays False. Getting that backwards in the
    fixture would have made the off-by-one look like an implementation bug.
    """
    return {TODAY - timedelta(days=i): minutes for i in range(n)}


# ── Threshold ───────────────────────────────────────────────────────────────


class TestThreshold:
    def test_fraction_is_clamped_at_one(self):
        """A bar that renders 340% wide is a rendering bug waiting to happen."""
        over = Threshold("subs", 3_400, 1_000, "subscribers", 1, True)
        assert over.fraction == 1.0
        assert over.met is True
        assert over.remaining == 0.0

    def test_an_empty_window_does_not_divide_by_zero(self):
        empty = Threshold("watch hours", 0, 4_000, "hours", 0, False)
        assert empty.per_day == 0.0
        assert empty.days_remaining() is None

    def test_days_remaining_is_none_once_met(self):
        assert Threshold("subs", 1_200, 1_000, "subscribers", 1, True).days_remaining() is None

    def test_days_remaining_is_none_at_a_standstill(self):
        """Zero rate means never, and `inf` days is not a thing to render."""
        stalled = Threshold("watch hours", 100, 4_000, "hours", 30, False)
        assert stalled.per_day == pytest_approx(100 / 30)
        assert Threshold("watch hours", 0, 4_000, "hours", 30, False).days_remaining() is None
        assert stalled.days_remaining() is not None

    def test_a_window_too_short_to_extrapolate_refuses_to(self):
        """One good video in three days implies a rate nothing will sustain."""
        brief = Threshold("watch hours", 90, 4_000, "hours", 3, False)
        assert brief.per_day == 30.0  # the rate exists
        assert brief.days_remaining() is None  # and is still not projected from

    def test_days_remaining_rounds_up(self):
        """Down would report arrival a day before it happens."""
        # 3,900 of 4,000 at 30/day: 100 remaining is 3.33 days, so 4.
        almost = Threshold("watch hours", 3_900, 4_000, "hours", 30, True)
        assert almost.per_day == 130.0
        assert almost.days_remaining() == 1

        slower = Threshold("watch hours", 900, 4_000, "hours", 30, True)
        assert slower.days_remaining() == 104  # 3100 / 30 = 103.33 -> 104


def pytest_approx(value: float, tol: float = 1e-9):
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, float) and abs(other - value) < tol

        def __repr__(self) -> str:  # pragma: no cover - only on failure
            return f"~{value}"

    return _Approx()


# ── the window ──────────────────────────────────────────────────────────────


class TestWindow:
    def test_a_partial_window_says_so(self):
        """The whole point. 28 days of data is not a 12-month figure."""
        report = progress(
            subscriber_count=120,
            watch_minutes_by_day=days_back(28, 600),
            today=TODAY,
        )
        assert report.watch_hours.covers_full_window is False
        assert report.watch_hours.window_days == 28
        assert report.caveat is not None
        assert "28 days" in report.caveat

    def test_a_full_year_covers_the_window(self):
        report = progress(
            subscriber_count=120,
            watch_minutes_by_day=days_back(365, 10),
            today=TODAY,
        )
        assert report.watch_hours.covers_full_window is True
        assert report.watch_hours.window_days == 365

    def test_days_outside_the_window_are_dropped(self):
        """400 days of history must not count toward a figure Google measures over 365.

        Counting them would overstate the one number an operator is waiting on.
        """
        old_and_new = {
            **days_back(365, 60),  # inside: 365 * 1 hour
            **{TODAY - timedelta(days=400 + i): 60_000 for i in range(20)},  # far outside
        }
        report = progress(subscriber_count=0, watch_minutes_by_day=old_and_new, today=TODAY)
        assert report.watch_hours.current == pytest_approx(365.0)

    def test_the_window_is_spanned_not_counted(self):
        """A silent day still happened.

        Counting only days with rows would shrink the denominator and inflate the
        per-day rate — making the projection look better than reality, which is the
        wrong direction for a number someone plans around.
        """
        sparse = {TODAY - timedelta(days=1): 600.0, TODAY - timedelta(days=30): 600.0}
        report = progress(subscriber_count=0, watch_minutes_by_day=sparse, today=TODAY)

        assert report.watch_hours.window_days == 30, "spanned, not the 2 days with rows"
        assert report.watch_hours.current == pytest_approx(20.0)
        assert report.watch_hours.per_day == pytest_approx(20.0 / 30)


# ── eligibility ─────────────────────────────────────────────────────────────


class TestEligibility:
    def test_hours_alone_are_not_enough(self):
        """4,000 hours on a 200-subscriber channel is not eligibility.

        Worth its own test because it is what a two-bar dashboard makes look like
        success: one bar full, and the channel no closer to being paid.
        """
        report = progress(
            subscriber_count=200,
            watch_minutes_by_day=days_back(365, 700),  # ~4,258 hours
            today=TODAY,
        )
        assert report.watch_hours.met is True
        assert report.eligible is False
        assert report.blocking == ["subscribers"]

    def test_subscribers_alone_are_not_enough(self):
        report = progress(
            subscriber_count=5_000, watch_minutes_by_day=days_back(365, 10), today=TODAY
        )
        assert report.subscribers.met is True
        assert report.eligible is False
        assert report.blocking == ["watch hours"]

    def test_both_halves_of_the_long_form_route(self):
        report = progress(
            subscriber_count=1_000,
            watch_minutes_by_day=days_back(365, 700),
            today=TODAY,
        )
        assert report.eligible is True
        assert report.blocking == []

    def test_the_shorts_route_qualifies_on_its_own(self):
        """10M Shorts views in 90 days is a full alternative to 4,000 hours."""
        report = progress(
            subscriber_count=SUBSCRIBERS_REQUIRED,
            watch_minutes_by_day={},  # no long-form at all
            shorts_views_by_day={TODAY - timedelta(days=i + 1): 200_000 for i in range(90)},  # 18M
            today=TODAY,
        )
        assert report.watch_hours.met is False
        assert report.shorts_views.met is True
        assert report.eligible is True
        assert report.route == "shorts"

    def test_shorts_outside_ninety_days_do_not_count(self):
        report = progress(
            subscriber_count=SUBSCRIBERS_REQUIRED,
            watch_minutes_by_day={},
            shorts_views_by_day={
                TODAY - timedelta(days=120 + i): SHORTS_VIEWS_REQUIRED for i in range(5)
            },
            today=TODAY,
        )
        assert report.shorts_views.current == 0.0
        assert report.eligible is False

    def test_the_route_is_whichever_is_further_along(self):
        mostly_long = progress(
            subscriber_count=0,
            watch_minutes_by_day=days_back(365, 350),  # ~2,129 h of 4,000 -> 0.53
            shorts_views_by_day={TODAY - timedelta(days=1): 1_000},  # ~0.0001
            today=TODAY,
        )
        assert mostly_long.route == "long-form"

    def test_a_tie_goes_to_long_form(self):
        """Ties break toward the route this product is built around.

        Equal fractions are not equal roads: 10 million Shorts views is much
        further than the same fraction of 4,000 hours suggests.
        """
        neither = progress(subscriber_count=0, watch_minutes_by_day={}, today=TODAY)
        assert neither.watch_hours.fraction == neither.shorts_views.fraction == 0.0
        assert neither.route == "long-form"


# ── the empty channel ───────────────────────────────────────────────────────


class TestEmptyChannel:
    def test_a_brand_new_channel_reports_zero_rather_than_failing(self):
        """The state every channel starts in, and the one most likely to divide by zero."""
        report = progress(subscriber_count=0, watch_minutes_by_day={}, today=TODAY)

        assert report.eligible is False
        assert report.watch_hours.current == 0.0
        assert report.watch_hours.window_days == 0
        assert report.watch_hours.days_remaining() is None
        assert report.subscribers.fraction == 0.0
        assert report.blocking == ["subscribers", "watch hours"]

    def test_it_serialises_without_raising(self):
        """`as_dict` feeds the API; None values have to survive JSON."""
        payload = progress(subscriber_count=0, watch_minutes_by_day={}, today=TODAY).as_dict()

        import json

        json.loads(json.dumps(payload))  # no non-serialisable values
        assert payload["watch_hours"]["days_remaining"] is None
        assert payload["subscribers"]["target"] == float(SUBSCRIBERS_REQUIRED)
        assert payload["watch_hours"]["target"] == float(WATCH_HOURS_REQUIRED)


# ── the conversion nobody notices until it is wrong ─────────────────────────


def test_minutes_become_hours():
    """Google reports minutes; the threshold is in hours.

    A missing divide-by-60 would read 60x high — a channel at 1.6% of the way
    there would be shown as eligible, which is the single most consequential
    arithmetic error this module could make.
    """
    report = progress(
        subscriber_count=0,
        watch_minutes_by_day={TODAY - timedelta(days=1): 240_000.0},
        today=TODAY,
    )
    assert report.watch_hours.current == pytest_approx(4_000.0)
    assert report.watch_hours.met is True
