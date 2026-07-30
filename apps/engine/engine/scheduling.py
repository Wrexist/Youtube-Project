"""Publish-time scheduling.

Two public surfaces:

* **``candidate_slots``** — rank every hour in the coming horizon by expected
  audience size, so the calendar can show *why* a slot is good.

* **``auto_schedule``** — greedily assign videos to the best available slots,
  respecting cadence, minimum gaps, daily upload caps, and quota limits.

* **``validate_move``** — check a manual calendar drag for hard blockers and
  soft warnings before applying it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

# Hard cap: more than this many uploads in one day harms the channel.
MAX_PUBLISHES_PER_DAY: int = 3

# Publish this many hours before the audience peak so the video has time to
# surface in recommendations before most of the audience shows up.
_LEAD_HOURS: int = 2

# Default prime-time hour for viewing (7 pm local / UTC approximate).
_DEFAULT_PEAK_HOUR: int = 19

# Gap below which a manually-dragged video earns a "compete" warning.
_COMPETE_WARNING_HOURS: int = 20


# ── audience profile ─────────────────────────────────────────────────────────


def _default_hourly() -> list[float]:
    """Gaussian audience curve centred at 19:00, std = 3 hours."""
    return [math.exp(-((h - _DEFAULT_PEAK_HOUR) ** 2) / (2 * 3.0**2)) for h in range(24)]


@dataclass
class AudienceProfile:
    """Hour-of-day and day-of-week audience weights.

    Use the default constructor for an estimated curve.  Use
    ``from_analytics()`` to build a measured profile from real Analytics data.
    """

    hourly: list[float] = field(default_factory=_default_hourly)
    daily: list[float] = field(default_factory=lambda: [1.0] * 7)
    is_measured: bool = False
    source: str = "estimated"

    @classmethod
    def from_analytics(
        cls,
        hourly_weights: list[float],
        daily_weights: list[float],
    ) -> AudienceProfile:
        """Build a measured profile from Analytics hourly/daily weight vectors.

        Args:
            hourly_weights:  24 floats, one per hour of day (index 0 = midnight).
            daily_weights:   7 floats, one per ISO weekday (index 0 = Monday).
        """
        return cls(
            hourly=list(hourly_weights),
            daily=list(daily_weights),
            is_measured=True,
            source="measured",
        )

    def score_for(self, at: datetime) -> float:
        """Audience score for publishing at *at* (assumes a fixed lead time)."""
        view_hour = (at.hour + _LEAD_HOURS) % 24
        weekday = at.weekday()  # 0 = Monday, 6 = Sunday
        h = self.hourly[view_hour] if self.hourly else 1.0
        d = self.daily[weekday] if self.daily else 1.0
        return round(h * d, 6)


# ── slots ────────────────────────────────────────────────────────────────────


@dataclass
class Slot:
    """One candidate publish time with its audience score."""

    at: datetime
    score: float
    reason: str


def candidate_slots(
    start: datetime,
    days: int,
    profile: AudienceProfile,
) -> list[Slot]:
    """Return all hourly slots for the next *days* days, ranked best-first.

    Slots before *start* are omitted.  The ``reason`` field names the profile
    source ("estimated" or "measured") so the UI can display provenance.
    """
    slots: list[Slot] = []

    for d in range(days):
        day_base = (start + timedelta(days=d)).replace(minute=0, second=0, microsecond=0)
        for h in range(24):
            at = day_base.replace(hour=h)
            if at < start:
                continue
            score = profile.score_for(at)
            reason = (
                f"{profile.source} audience score {score:.3f} "
                f"at {at.strftime('%H:%M')} ({at.strftime('%A')})"
            )
            slots.append(Slot(at=at, score=score, reason=reason))

    return sorted(slots, key=lambda s: s.score, reverse=True)


# ── scheduling objects ───────────────────────────────────────────────────────


@dataclass
class Pending:
    """A video waiting to be placed on the calendar."""

    id: str
    title: str
    format: str = "short"  # "short" | "long"
    ready_at: datetime | None = None


@dataclass
class Assignment:
    """One confirmed calendar entry."""

    video_id: str
    at: datetime
    score: float
    reason: str


@dataclass
class SchedulePlan:
    """Output of ``auto_schedule``."""

    assignments: list[Assignment]
    unplaced: list[tuple[str, str]]  # (video_id, human-readable reason)


@dataclass
class Constraints:
    """Scheduling rules for a series."""

    shorts_per_week: int = 3
    long_per_week: int = 1
    min_gap_hours: int = 20
    quota_used_by_day: dict[date, int] | None = None


# ── auto-scheduler ───────────────────────────────────────────────────────────


def auto_schedule(
    pending: list[Pending],
    *,
    start: datetime,
    profile: AudienceProfile | None = None,
    constraints: Constraints | None = None,
    existing: list[datetime] | None = None,
    horizon_days: int = 28,
) -> SchedulePlan:
    """Assign each pending video to the best available slot.

    Long-form videos are processed first so they capture the highest-scored
    times (they are more expensive to produce and benefit more from a prime
    slot).  Short-form fills in around them.

    Videos that cannot be placed are reported in ``plan.unplaced`` with a
    human-readable explanation so the user can act on them.
    """
    if profile is None:
        profile = AudienceProfile()
    if constraints is None:
        constraints = Constraints()
    if existing is None:
        existing = []

    # Long-form gets priority over the slot list.
    ordered = sorted(pending, key=lambda p: (0 if p.format == "long" else 1, p.id))

    all_slots = candidate_slots(start, horizon_days, profile)

    # Mutable state tracking the placement so far.
    booked_times: list[datetime] = list(existing)
    per_day: dict[date, int] = {}
    week_shorts: dict[tuple[int, int], int] = {}  # (iso_year, iso_week) → count
    week_longs: dict[tuple[int, int], int] = {}
    assigned_slot_ats: set[datetime] = set()

    assignments: list[Assignment] = []
    unplaced: list[tuple[str, str]] = []

    min_gap_s = constraints.min_gap_hours * 3600

    for video in ordered:
        placed = False

        for slot in all_slots:
            # Slot already taken by another assignment in this run.
            if slot.at in assigned_slot_ats:
                continue

            # Video isn't ready yet.
            if video.ready_at is not None and slot.at < video.ready_at:
                continue

            # Minimum gap from every booked time (existing + new assignments).
            too_close = any(abs((slot.at - t).total_seconds()) < min_gap_s for t in booked_times)
            if too_close:
                continue

            # Daily cap.
            day = slot.at.date()
            if per_day.get(day, 0) >= MAX_PUBLISHES_PER_DAY:
                continue

            # Quota exhausted for this day.
            if constraints.quota_used_by_day:
                from engine.quota import ledger  # noqa: PLC0415

                if constraints.quota_used_by_day.get(day, 0) >= ledger.limit:
                    continue

            # Weekly cadence.
            iso = slot.at.isocalendar()
            week_key: tuple[int, int] = (iso[0], iso[1])

            if video.format == "long":
                if week_longs.get(week_key, 0) >= constraints.long_per_week:
                    continue
            else:
                if week_shorts.get(week_key, 0) >= constraints.shorts_per_week:
                    continue

            # ── slot accepted ──
            assignments.append(
                Assignment(
                    video_id=video.id,
                    at=slot.at,
                    score=slot.score,
                    reason=slot.reason,
                )
            )
            booked_times.append(slot.at)
            assigned_slot_ats.add(slot.at)
            per_day[day] = per_day.get(day, 0) + 1

            if video.format == "long":
                week_longs[week_key] = week_longs.get(week_key, 0) + 1
            else:
                week_shorts[week_key] = week_shorts.get(week_key, 0) + 1

            placed = True
            break

        if not placed:
            unplaced.append(
                (
                    video.id,
                    "weekly cadence limit reached — no slot available within horizon",
                )
            )

    return SchedulePlan(assignments=assignments, unplaced=unplaced)


# ── manual drag validation ────────────────────────────────────────────────────


def validate_move(
    at: datetime,
    *,
    existing: list[datetime],
    quota_used_by_day: dict[date, int] | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Validate a manual calendar drag before applying it.

    Returns ``(ok, message)`` where *ok* is whether the move is permitted and
    *message* is either a blocking reason (when ``not ok``) or a soft warning
    (when ``ok`` but the move is inadvisable).  An empty string means no issue.
    """
    if now is None:
        now = datetime.now(UTC)

    # Hard block: time is in the past.
    if at < now:
        return (False, f"The requested time has already passed ({at.isoformat()}).")

    # Hard block: quota already exhausted for that day.
    if quota_used_by_day:
        from engine.quota import ledger  # noqa: PLC0415

        day = at.date()
        if quota_used_by_day.get(day, 0) >= ledger.limit:
            return (
                False,
                f"The YouTube upload quota for {day} is exhausted; choose a different day.",
            )

    # Hard block: too many uploads already on that day.
    #
    # `MAX_PUBLISHES_PER_DAY` is documented as "more than this many uploads in one
    # day harms the channel", but it was enforced only inside `auto_schedule` —
    # so the automatic planner respected it and every manual drag ignored it. One
    # constant, two paths, one of them honouring it.
    #
    # Counted from `existing` rather than taken as a new parameter: the caller
    # already passes every other scheduled time, so asking for a tally as well
    # would be a second source of the same truth, and the two would drift.
    same_day = sum(1 for t in existing if t.astimezone(at.tzinfo).date() == at.date())
    if same_day >= MAX_PUBLISHES_PER_DAY:
        return (
            False,
            f"{at.date()} already has {same_day} uploads scheduled. More than "
            f"{MAX_PUBLISHES_PER_DAY} in a day splits your own audience.",
        )

    # Soft warning: too close to another scheduled upload.
    for t in existing:
        gap_hours = abs((at - t).total_seconds()) / 3600
        if gap_hours < _COMPETE_WARNING_HOURS:
            return (
                True,
                f"This video will compete with one scheduled {gap_hours:.0f}h away — "
                f"uploads this close split each other's audience.",
            )

    return (True, "")
