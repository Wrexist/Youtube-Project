"""Automation safety rails.

Three brakes prevent an unattended channel from doing expensive or embarrassing
things silently:

1. **Spend ceilings** (``check_budget``, ``plan_week``) — per-video, daily, and
   monthly caps enforced before any generation is queued.

2. **Approval gate** (``resolve_stage``) — videos with quality blockers cannot
   auto-publish even when the series has ``auto_publish=True``.  Ungrounded
   scripts, weak critiques, and missing assets stay in NEEDS_REVIEW.

3. **Publish blockers** (``publish_blockers``) — individual checks that must all
   clear before a video is sent to YouTube.  Each carries a readable reason so
   the user knows exactly what to fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from engine.ideas import Idea, next_up

# Per-video cost ceiling from engine.settings — hardcoded here to avoid pulling
# in pydantic in test environments that may not have a .env file.
_MAX_COST_PER_VIDEO_USD: float = 8.0

# Default cost used for planning when the actual cost is not yet known.
DEFAULT_COST_PER_VIDEO_USD: float = 2.50

# YouTube title character limit.
_TITLE_MAX_LEN: int = 100

# Critique severity at or above this threshold blocks publication.
#: On the 1-5 scale the critique prompt actually asks for (script.py). This was 5
#: while the blocker message said "/10", so it could only ever fire at the absolute
#: maximum — and it never fired at all, because the value it compared was misread.
#: 4 means "the critique thinks this is weak", which is what the gate is asking.
_WEAK_SCRIPT_THRESHOLD: int = 4


# ── domain objects ───────────────────────────────────────────────────────────


@dataclass
class Series:
    """A recurring content series with its own budget and cadence."""

    id: str
    name: str
    niche: str
    monthly_budget_usd: float
    shorts_per_week: int = 3
    long_per_week: int = 1
    auto_publish: bool = False
    paused: bool = False


@dataclass
class VideoState:
    """Snapshot of one video's readiness for publication."""

    id: str
    series_id: str
    cost_usd: float = 0.0
    has_sources: bool = False
    source_count: int = 0
    has_thumbnail: bool = False
    has_seo: bool = False
    keyword_grounded: bool = False
    render_ok: bool = False
    title: str = ""
    critique_severity: int = 0


class Stage(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


@dataclass
class Blocker:
    """A named reason that something cannot proceed."""

    code: str
    message: str


@dataclass
class WeekPlan:
    """Output of ``plan_week``: what to generate and what stopped us from more."""

    to_generate: list[Idea]
    blocked: list[Blocker] = field(default_factory=list)


# ── spend ledger ─────────────────────────────────────────────────────────────


@dataclass
class _SpendEntry:
    series_id: str
    amount: float
    at: datetime


class SpendLedger:
    """Track USD spend across series, used to enforce budget caps."""

    def __init__(self) -> None:
        self._entries: list[_SpendEntry] = []

    def record(self, series_id: str, amount: float, *, at: datetime | None = None) -> None:
        """Record a spend event."""
        if at is None:
            at = datetime.now(UTC)
        self._entries.append(_SpendEntry(series_id=series_id, amount=amount, at=at))

    def spent_today(self, *, series_id: str | None = None) -> float:
        """Total USD spent today (UTC date), optionally filtered to one series."""
        today = datetime.now(UTC).date()
        return sum(
            e.amount
            for e in self._entries
            if e.at.astimezone(UTC).date() == today
            and (series_id is None or e.series_id == series_id)
        )

    def spent_this_month(self, series_id: str) -> float:
        """Total USD spent in the current UTC calendar month for *series_id*."""
        now = datetime.now(UTC)
        return sum(
            e.amount
            for e in self._entries
            if e.series_id == series_id
            and e.at.astimezone(UTC).year == now.year
            and e.at.astimezone(UTC).month == now.month
        )


# ── budget policy ─────────────────────────────────────────────────────────────


@dataclass
class BudgetPolicy:
    """Organisation-wide daily spend ceiling (USD), applied across all series."""

    per_day_usd: float = float("inf")


# ── checks ────────────────────────────────────────────────────────────────────


def check_budget(
    series: Series,
    ledger: SpendLedger,
    policy: BudgetPolicy,
    *,
    estimate_usd: float,
) -> list[Blocker]:
    """Return any spend blockers for one video generation.

    Three independent checks, each with its own code:
    - ``per_video_cap``   — this video alone exceeds the per-video ceiling.
    - ``daily_cap``       — adding it would push today's total over the daily cap.
    - ``series_budget``   — the series' monthly budget would be exceeded.
    """
    blockers: list[Blocker] = []

    if estimate_usd > _MAX_COST_PER_VIDEO_USD:
        blockers.append(
            Blocker(
                code="per_video_cap",
                message=(
                    f"Estimated cost ${estimate_usd:.2f} exceeds the per-video "
                    f"ceiling of ${_MAX_COST_PER_VIDEO_USD:.2f}. Reduce scope or "
                    f"raise the ceiling in settings."
                ),
            )
        )

    today_total = ledger.spent_today()
    if today_total + estimate_usd > policy.per_day_usd:
        blockers.append(
            Blocker(
                code="daily_cap",
                message=(
                    f"Today's spend (${today_total:.2f}) plus this video "
                    f"(${estimate_usd:.2f}) would exceed the daily cap of "
                    f"${policy.per_day_usd:.2f}."
                ),
            )
        )

    this_month = ledger.spent_this_month(series.id)
    if this_month + estimate_usd > series.monthly_budget_usd:
        blockers.append(
            Blocker(
                code="series_budget",
                message=(
                    f"This month's spend for '{series.name}' (${this_month:.2f}) "
                    f"plus this video (${estimate_usd:.2f}) would exceed the "
                    f"${series.monthly_budget_usd:.2f} monthly budget."
                ),
            )
        )

    return blockers


def publish_blockers(video: VideoState, series: Series) -> list[Blocker]:  # noqa: ARG001
    """Return every reason this video cannot be published as-is.

    All checks are independent — the caller sees the full list, not just the
    first failure.
    """
    blockers: list[Blocker] = []

    if not video.has_sources or video.source_count == 0:
        blockers.append(
            Blocker(
                code="ungrounded",
                message=(
                    "This video has no research sources attached. Add at least "
                    "one source before publishing to ensure factual grounding."
                ),
            )
        )

    if not video.has_thumbnail:
        blockers.append(
            Blocker(
                code="no_thumbnail",
                message=(
                    "No thumbnail has been generated or uploaded for this video. "
                    "Create a thumbnail before publishing."
                ),
            )
        )

    if not video.has_seo:
        blockers.append(
            Blocker(
                code="no_seo",
                message=(
                    "SEO metadata (description, tags) has not been generated. "
                    "Run the SEO stage before publishing."
                ),
            )
        )

    if not video.keyword_grounded:
        blockers.append(
            Blocker(
                code="not_keyword_grounded",
                message=(
                    "This video is not connected to keyword research. Link it to "
                    "a target keyword before publishing."
                ),
            )
        )

    if not video.render_ok:
        blockers.append(
            Blocker(
                code="render_not_ok",
                message=(
                    "The render has not been verified as complete and playable. "
                    "Check the render output before publishing."
                ),
            )
        )

    if video.title and len(video.title) > _TITLE_MAX_LEN:
        blockers.append(
            Blocker(
                code="title_too_long",
                message=(
                    f"Title is {len(video.title)} characters; YouTube allows "
                    f"at most {_TITLE_MAX_LEN}. Shorten it before publishing."
                ),
            )
        )

    if video.critique_severity >= _WEAK_SCRIPT_THRESHOLD:
        blockers.append(
            Blocker(
                code="weak_script",
                message=(
                    f"Script critique severity is {video.critique_severity}/5, "
                    f"which is at or above the threshold of {_WEAK_SCRIPT_THRESHOLD}. "
                    f"Revise the script before publishing."
                ),
            )
        )

    return blockers


def resolve_stage(video: VideoState, series: Series) -> tuple[Stage, list[Blocker]]:
    """Decide whether a video goes to APPROVED or waits in NEEDS_REVIEW.

    A series with ``auto_publish=True`` skips the manual review queue — but only
    if there are no quality blockers.  A paused series is always NEEDS_REVIEW
    regardless of other settings.
    """
    quality_blockers = publish_blockers(video, series)

    if series.paused or not series.auto_publish or quality_blockers:
        return Stage.NEEDS_REVIEW, quality_blockers

    return Stage.APPROVED, []


# ── week planning ─────────────────────────────────────────────────────────────


def plan_week(
    series: Series,
    ideas: list[Idea],
    ledger: SpendLedger,
    policy: BudgetPolicy,
    *,
    already_this_week: int = 0,
) -> WeekPlan:
    """Return how many videos to generate this week and why any were skipped.

    Checks are applied in priority order:
    1. Paused series → stop immediately.
    2. Daily ceiling → stop immediately (generating anything costs money today).
    3. Monthly budget → cap the count and explain.
    4. Thin backlog → cap the count and explain.
    """
    # 1. Paused series — generating is not permitted at all.
    if series.paused:
        return WeekPlan(
            to_generate=[],
            blocked=[
                Blocker(
                    code="paused",
                    message=(
                        f"Series '{series.name}' is paused. Resume it before queuing new videos."
                    ),
                )
            ],
        )

    # 2. Daily ceiling — even one video today would exceed it.
    today_total = ledger.spent_today()
    if (
        policy.per_day_usd != float("inf")
        and today_total + DEFAULT_COST_PER_VIDEO_USD > policy.per_day_usd
    ):
        return WeekPlan(
            to_generate=[],
            blocked=[
                Blocker(
                    code="daily_cap",
                    message=(
                        f"Today's spend (${today_total:.2f}) plus the cost of one "
                        f"video (${DEFAULT_COST_PER_VIDEO_USD:.2f}) would exceed the "
                        f"${policy.per_day_usd:.2f} daily ceiling. Try again tomorrow."
                    ),
                )
            ],
        )

    blocked: list[Blocker] = []

    # 3. Monthly budget — how many more videos can the budget afford?
    target = max(0, (series.shorts_per_week + series.long_per_week) - already_this_week)
    this_month = ledger.spent_this_month(series.id)
    remaining_budget = series.monthly_budget_usd - this_month
    affordable = max(0, int(remaining_budget // DEFAULT_COST_PER_VIDEO_USD))

    if affordable < target:
        blocked.append(
            Blocker(
                code="budget_limits_cadence",
                message=(
                    f"Only ${remaining_budget:.2f} remaining in '{series.name}'s "
                    f"monthly budget at ${DEFAULT_COST_PER_VIDEO_USD:.2f}/video; "
                    f"can afford {affordable} more this month instead of {target}."
                ),
            )
        )
        target = affordable

    # 4. Backlog — filter to fresh, non-duplicate ideas and check count.
    eligible = next_up(ideas, 1000)  # filters to BACKLOG + non-stale, sorted by score

    if len(eligible) < target:
        blocked.append(
            Blocker(
                code="thin_backlog",
                message=(
                    f"Only {len(eligible)} fresh idea(s) in the backlog; need "
                    f"{target} to fill this week's cadence. Add more topics."
                ),
            )
        )
        target = len(eligible)

    return WeekPlan(to_generate=eligible[:target], blocked=blocked)
