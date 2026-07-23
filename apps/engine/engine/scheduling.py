"""Automatic publish scheduling.

Placing a video on the calendar is a real optimisation, not a formality: publish into
a dead hour and the first-hour click-through — which is most of what the recommender
uses to decide whether to push the video further — never happens.

The scheduler balances four things that genuinely conflict:

  1. **Audience activity.** Publish shortly *before* the audience peak, not during it,
     so the video has accumulated impressions by the time traffic arrives.
  2. **Spacing.** Two uploads too close together compete with each other in the same
     subscribers' feeds.
  3. **Quota.** An upload costs 1,600 of 10,000 daily units. Six a day is the wall.
  4. **Cadence.** The user asked for 3 shorts and 1 long-form a week, and that shape
     should survive contact with the other three constraints.

Everything here is pure — no I/O, no clock reads beyond what's passed in — which is
what makes it testable and what makes the calendar's preview honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from engine.quota import DAILY_LIMIT, COSTS

# Publish this far ahead of the audience peak so impressions accumulate first.
PEAK_LEAD_HOURS = 2

# Below this gap, two uploads compete in the same feed.
MIN_GAP_HOURS = 20

# Whole-sequence cost: insert + thumbnail + captions.
PUBLISH_COST = COSTS["videos.insert"] + COSTS["thumbnails.set"] + COSTS["captions.insert"]
MAX_PUBLISHES_PER_DAY = DAILY_LIMIT // PUBLISH_COST  # 4 with the default quota

# Fallback audience curve, used until real analytics exist: relative activity by hour
# in the channel's primary timezone. Evening-weighted, which is typical but is a
# *guess* — it is replaced by measured data as soon as Phase 8 has 28 days of it.
DEFAULT_HOURLY = [
    0.15, 0.10, 0.07, 0.05, 0.05, 0.08, 0.18, 0.32,
    0.45, 0.48, 0.46, 0.50, 0.58, 0.55, 0.52, 0.56,
    0.68, 0.82, 0.94, 1.00, 0.96, 0.82, 0.58, 0.32,
]

# Weekday multipliers. Sunday and Thursday-Saturday evenings tend to run hotter.
DEFAULT_WEEKDAY = [0.92, 0.90, 0.94, 1.00, 1.04, 1.02, 0.98]  # Mon..Sun


@dataclass
class AudienceProfile:
    """When this channel's viewers are actually watching.

    `source` is carried so the UI can be honest about whether a recommendation rests
    on measured data or on the default curve.
    """

    hourly: list[float] = field(default_factory=lambda: list(DEFAULT_HOURLY))
    weekday: list[float] = field(default_factory=lambda: list(DEFAULT_WEEKDAY))
    source: str = "default_heuristic"

    @classmethod
    def from_analytics(cls, hourly: list[float], weekday: list[float]) -> AudienceProfile:
        if len(hourly) != 24 or len(weekday) != 7:
            raise ValueError("expected 24 hourly and 7 weekday values")
        return cls(hourly=hourly, weekday=weekday, source="measured")

    @property
    def is_measured(self) -> bool:
        return self.source == "measured"

    def peak_hour(self, weekday: int) -> int:
        return max(range(24), key=lambda h: self.hourly[h])

    def score(self, moment: datetime) -> float:
        """Score a candidate publish time.

        The lookup is shifted by PEAK_LEAD_HOURS: publishing at 17:00 is scored on the
        audience present at 19:00, because that is who the video needs to reach.
        """
        target = (moment.hour + PEAK_LEAD_HOURS) % 24
        return self.hourly[target] * self.weekday[moment.weekday()]


@dataclass
class Slot:
    at: datetime
    score: float
    reason: str

    def summary(self) -> str:
        return f"{self.at:%a %d %b %H:%M} · {self.score:.2f}"


@dataclass
class Pending:
    """A finished video waiting for a publish time."""

    id: str
    title: str
    format: str = "short"  # short | long
    duration_s: float = 0.0
    ready_at: datetime | None = None  # cannot publish before this


def candidate_slots(
    start: datetime,
    days: int,
    profile: AudienceProfile,
    *,
    hours: tuple[int, ...] = (9, 12, 15, 17, 19, 21),
) -> list[Slot]:
    """Score every candidate publish time in the window, best first.

    Candidate hours are deliberately coarse. Minute-level precision is false accuracy
    — the recommender does not care whether a video went out at 17:00 or 17:12.
    """
    slots: list[Slot] = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        for hour in hours:
            at = datetime.combine(day, time(hour), tzinfo=start.tzinfo)
            if at <= start:
                continue
            score = profile.score(at)
            slots.append(
                Slot(
                    at=at,
                    score=round(score, 4),
                    reason=(
                        f"{'measured' if profile.is_measured else 'estimated'} audience "
                        f"peak at {(hour + PEAK_LEAD_HOURS) % 24:02d}:00"
                    ),
                )
            )
    return sorted(slots, key=lambda s: s.score, reverse=True)


@dataclass
class Constraints:
    shorts_per_week: int = 3
    long_per_week: int = 1
    min_gap_hours: int = MIN_GAP_HOURS
    max_per_day: int = MAX_PUBLISHES_PER_DAY
    quota_used_by_day: dict[date, int] = field(default_factory=dict)

    def budget_left(self, day: date) -> int:
        used = self.quota_used_by_day.get(day, 0)
        return (DAILY_LIMIT - used) // PUBLISH_COST


@dataclass
class Assignment:
    video_id: str
    at: datetime
    score: float
    reason: str


@dataclass
class Plan:
    assignments: list[Assignment] = field(default_factory=list)
    unplaced: list[tuple[str, str]] = field(default_factory=list)  # (video_id, why)

    def summary(self) -> str:
        placed = len(self.assignments)
        return f"{placed} scheduled" + (
            f" · {len(self.unplaced)} unplaced" if self.unplaced else ""
        )


def auto_schedule(
    pending: list[Pending],
    *,
    start: datetime,
    profile: AudienceProfile | None = None,
    constraints: Constraints | None = None,
    existing: list[datetime] | None = None,
    horizon_days: int = 28,
) -> Plan:
    """Assign publish times to a set of finished videos.

    Long-form goes first: it takes more effort to produce, benefits more from a good
    slot, and there is less of it, so giving it first pick costs the shorts very
    little.

    A video that cannot be placed is reported with the reason rather than silently
    dropped — a scheduler that quietly loses work is worse than one that refuses it.
    """
    profile = profile or AudienceProfile()
    constraints = constraints or Constraints()
    taken: list[datetime] = list(existing or [])
    plan = Plan()

    slots = candidate_slots(start, horizon_days, profile)
    ordered = sorted(pending, key=lambda p: (p.format != "long", p.id))

    per_day: dict[date, int] = {}
    for moment in taken:
        per_day[moment.date()] = per_day.get(moment.date(), 0) + 1

    weekly_used: dict[tuple[int, str], int] = {}

    for video in ordered:
        placed = False
        for slot in slots:
            if any(abs((slot.at - t).total_seconds()) < constraints.min_gap_hours * 3600
                   for t in taken):
                continue
            if video.ready_at and slot.at < video.ready_at:
                continue

            day = slot.at.date()
            if per_day.get(day, 0) >= constraints.max_per_day:
                continue
            if constraints.budget_left(day) - per_day.get(day, 0) <= 0:
                continue

            week = slot.at.isocalendar()[1]
            cap = (
                constraints.long_per_week
                if video.format == "long"
                else constraints.shorts_per_week
            )
            if weekly_used.get((week, video.format), 0) >= cap:
                continue

            plan.assignments.append(
                Assignment(
                    video_id=video.id, at=slot.at, score=slot.score, reason=slot.reason
                )
            )
            taken.append(slot.at)
            per_day[day] = per_day.get(day, 0) + 1
            weekly_used[(week, video.format)] = weekly_used.get((week, video.format), 0) + 1
            placed = True
            break

        if not placed:
            plan.unplaced.append(
                (
                    video.id,
                    f"no slot within {horizon_days} days satisfies cadence "
                    f"({cadence_text(video.format, constraints)}), the "
                    f"{constraints.min_gap_hours}h spacing rule, and the daily quota",
                )
            )

    plan.assignments.sort(key=lambda a: a.at)
    return plan


def cadence_text(fmt: str, constraints: Constraints) -> str:
    n = constraints.long_per_week if fmt == "long" else constraints.shorts_per_week
    return f"{n} {fmt}/week"


def validate_move(
    moment: datetime,
    *,
    existing: list[datetime],
    quota_used_by_day: dict[date, int] | None = None,
    min_gap_hours: int = MIN_GAP_HOURS,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Check a manual drag on the calendar.

    Returns `(ok, message)`. The message is shown either way — a permitted move that
    is merely a bad idea should say so rather than silently accepting it.
    """
    now = now or datetime.now(moment.tzinfo)
    if moment <= now:
        return False, "that time has already passed"

    day = moment.date()
    used = (quota_used_by_day or {}).get(day, 0)
    same_day = sum(1 for t in existing if t.date() == day)
    if (DAILY_LIMIT - used) // PUBLISH_COST <= same_day:
        return False, (
            f"{day:%d %b} has no upload quota left — an upload costs "
            f"{PUBLISH_COST:,} of {DAILY_LIMIT:,} daily units"
        )

    conflict = next(
        (t for t in existing if abs((moment - t).total_seconds()) < min_gap_hours * 3600),
        None,
    )
    if conflict:
        gap = abs((moment - conflict).total_seconds()) / 3600
        return True, (
            f"only {gap:.0f}h from another upload — they'll compete in the same "
            f"subscriber feeds"
        )

    return True, ""
