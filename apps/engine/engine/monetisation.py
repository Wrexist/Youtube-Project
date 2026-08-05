"""Progress toward the YouTube Partner Programme thresholds.

The whole product exists to get a channel monetised, and until now no screen said
how close it was. The numbers were not even missing — `providers/analytics.py`
requested `estimatedMinutesWatched` on every daily pull and then dropped it on the
floor, reading only columns 1, 3 and 4 out of the four it paid for.

Two routes to the same door, and a channel needs **either**:

  * **Long-form**: 1,000 subscribers **and** 4,000 valid public watch hours in the
    trailing 12 months.
  * **Shorts**: 1,000 subscribers **and** 10 million valid public Shorts views in
    the trailing 90 days.

Subscribers are required on both, so they are reported once.

The one thing this module refuses to do is state a trailing-12-month total from
less than 12 months of data. A channel four weeks old has *no* 12-month figure,
and printing its four-week total in a box labelled "of 4,000 hours" is the kind
of number an operator plans around and then finds out was never true. What is
reported instead is the window actually covered, the observed rate inside it, and
— separately and labelled as such — a projection. `covers_full_window` is how a
caller tells those apart.

"Valid public" is Google's qualifier and it is not knowable from the Analytics
API: it excludes private and unlisted videos, deleted videos, paid promotion and
anything their spam systems discount. Every figure here is therefore an upper
bound on what Google will count. Said once, in `Progress.caveat`, rather than
implied nowhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

#: The thresholds themselves. Constants rather than settings: they are Google's
#: numbers, not ours, and an operator who "configures" them has only changed the
#: number on their own dashboard.
SUBSCRIBERS_REQUIRED = 1_000
WATCH_HOURS_REQUIRED = 4_000
WATCH_HOURS_WINDOW_DAYS = 365
SHORTS_VIEWS_REQUIRED = 10_000_000
SHORTS_VIEWS_WINDOW_DAYS = 90

#: Below this many days of data, a projection is noise rather than a forecast —
#: one good video in a three-day window implies a rate nothing will sustain.
_MIN_DAYS_TO_PROJECT = 7


@dataclass(frozen=True)
class Threshold:
    """One requirement, and how far along it is.

    `remaining` and `fraction` are derived rather than stored so they cannot drift
    apart from `current`, which is the only field a caller ever sets.
    """

    name: str
    current: float
    target: float
    unit: str
    #: The number of days `current` was measured over, and whether that is the
    #: full window Google actually judges. A partial window is not a smaller
    #: version of the real figure — it is a different measurement.
    window_days: int
    covers_full_window: bool

    @property
    def met(self) -> bool:
        return self.current >= self.target

    @property
    def remaining(self) -> float:
        return max(0.0, self.target - self.current)

    @property
    def fraction(self) -> float:
        """Progress in 0..1, clamped.

        Clamped at 1.0 deliberately: a progress bar that renders 340% because a
        channel sailed past the bar is a rendering bug waiting to happen, and
        "how far past" is not a question this type is asked.
        """
        if self.target <= 0:
            return 1.0
        return min(1.0, self.current / self.target)

    @property
    def per_day(self) -> float:
        """Observed rate. Zero for an empty window rather than a ZeroDivisionError."""
        return self.current / self.window_days if self.window_days > 0 else 0.0

    def days_remaining(self) -> int | None:
        """Days to reach `target` at the observed rate, or None if unanswerable.

        None in three cases, all of which mean the same thing to a caller — there
        is no honest number to show:

          * already met (nothing to wait for),
          * a rate of zero (never, and `inf` days is not a useful thing to render),
          * a window too short to have a rate worth extrapolating.

        This is a projection from the recent past, not a forecast. Publishing more,
        or one video outperforming, changes it entirely.
        """
        if self.met:
            return None
        if self.window_days < _MIN_DAYS_TO_PROJECT:
            return None
        rate = self.per_day
        if rate <= 0:
            return None
        return math.ceil(self.remaining / rate)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "current": round(self.current, 2),
            "target": self.target,
            "unit": self.unit,
            "met": self.met,
            "remaining": round(self.remaining, 2),
            "fraction": round(self.fraction, 4),
            "window_days": self.window_days,
            "covers_full_window": self.covers_full_window,
            "days_remaining": self.days_remaining(),
        }


@dataclass(frozen=True)
class Progress:
    """Both routes to the Partner Programme, and which one is closer."""

    subscribers: Threshold
    watch_hours: Threshold
    shorts_views: Threshold

    @property
    def eligible(self) -> bool:
        """Whether *both halves* of at least one route are met.

        Subscribers gate both routes, so failing that fails everything regardless
        of how many hours are banked — which is the case worth being explicit
        about, because 4,000 hours on a 200-subscriber channel reads like success
        on any dashboard that shows the two bars side by side.
        """
        return self.subscribers.met and (self.watch_hours.met or self.shorts_views.met)

    @property
    def route(self) -> str:
        """Whichever content route is further along, by fraction of its target.

        Ties go to long-form: it is the route this product is built around, and
        10 million Shorts views is a far longer road than a tie in fractions
        suggests.
        """
        return "shorts" if self.shorts_views.fraction > self.watch_hours.fraction else "long-form"

    @property
    def blocking(self) -> list[str]:
        """What is still in the way, most-limiting first — empty once eligible."""
        if self.eligible:
            return []
        out = []
        if not self.subscribers.met:
            out.append(self.subscribers.name)
        route = self.watch_hours if self.route == "long-form" else self.shorts_views
        if not route.met:
            out.append(route.name)
        return out

    @property
    def caveat(self) -> str | None:
        """The reason a figure here may read higher than Google's own count.

        Scoped to `route` rather than to whichever window is shortest. Taking the
        minimum across both meant a channel with no Shorts at all — where
        `shorts_views.window_days` is 0 — got told its watch hours were "measured
        over 0 days", which is both wrong and the opposite of reassuring.
        """
        measured = self.watch_hours if self.route == "long-form" else self.shorts_views
        if measured.covers_full_window:
            return None
        return (
            f"Measured over {measured.window_days} days, not the full window Google "
            "judges. Counts also include views Google may not treat as valid and public."
        )

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "route": self.route,
            "blocking": self.blocking,
            "caveat": self.caveat,
            "subscribers": self.subscribers.as_dict(),
            "watch_hours": self.watch_hours.as_dict(),
            "shorts_views": self.shorts_views.as_dict(),
        }


def _window(days: list[date], requested: int) -> tuple[int, bool]:
    """How many days the data actually spans, and whether that fills the window.

    Spanned rather than counted: a channel that published nothing on Tuesday still
    lived through Tuesday, so `len(rows)` would understate the window and inflate
    every per-day rate derived from it — the direction that makes a projection look
    better than reality, which is the wrong direction to be wrong in.
    """
    if not days:
        return 0, False
    spanned = (max(days) - min(days)).days + 1
    return min(spanned, requested), spanned >= requested


def progress(
    *,
    subscriber_count: int,
    watch_minutes_by_day: dict[date, float],
    shorts_views_by_day: dict[date, int] | None = None,
    today: date | None = None,
) -> Progress:
    """Assemble the picture from whatever the Analytics API returned.

    Takes plain mappings rather than the provider's dataclasses so the arithmetic
    here is testable without a Google account — which matters, because a live
    account is the one thing this repository has never had (KNOWN-ISSUES §1.1).
    Days outside each route's window are dropped rather than trusted: a caller that
    hands over 400 days of history must not have 400 days counted toward a figure
    Google measures over 365.
    """
    # UTC, matching the provider that supplies these days. Local time here meant the
    # 365-day window could include or exclude a day the data was never keyed on.
    today = today or datetime.now(UTC).date()
    shorts_views_by_day = shorts_views_by_day or {}

    hours_from = today - timedelta(days=WATCH_HOURS_WINDOW_DAYS)
    in_hours_window = {d: m for d, m in watch_minutes_by_day.items() if d > hours_from}
    hours_days, hours_full = _window(list(in_hours_window), WATCH_HOURS_WINDOW_DAYS)

    shorts_from = today - timedelta(days=SHORTS_VIEWS_WINDOW_DAYS)
    in_shorts_window = {d: v for d, v in shorts_views_by_day.items() if d > shorts_from}
    shorts_days, shorts_full = _window(list(in_shorts_window), SHORTS_VIEWS_WINDOW_DAYS)

    return Progress(
        subscribers=Threshold(
            name="subscribers",
            current=float(subscriber_count),
            target=float(SUBSCRIBERS_REQUIRED),
            unit="subscribers",
            # A subscriber count is a running total, not a windowed measurement —
            # it is as complete as it will ever be the moment it is read.
            window_days=1,
            covers_full_window=True,
        ),
        watch_hours=Threshold(
            name="watch hours",
            current=sum(in_hours_window.values()) / 60.0,
            target=float(WATCH_HOURS_REQUIRED),
            unit="hours",
            window_days=hours_days,
            covers_full_window=hours_full,
        ),
        shorts_views=Threshold(
            name="Shorts views",
            current=float(sum(in_shorts_window.values())),
            target=float(SHORTS_VIEWS_REQUIRED),
            unit="views",
            window_days=shorts_days,
            covers_full_window=shorts_full,
        ),
    )
