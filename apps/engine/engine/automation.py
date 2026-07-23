"""Series, approval gates, and spend ceilings.

This is the module that decides whether the system is allowed to act without a human
watching. Three independent brakes, because unattended generation fails expensively:

  * **Spend ceilings** — per video, per series per month, and per day across
    everything. Checked before work starts, not after.
  * **Approval gates** — a video reaches `needs_review` and stays there unless the
    series has auto-publish on *and* the video is clean. "Clean" is a checklist, not
    a vibe.
  * **Backlog depth** — a series will not generate from an empty or stale backlog
    just to hit its cadence. Publishing something bad on schedule is worse than
    publishing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from engine.ideas import Idea, next_up


class Stage(StrEnum):
    """Where a video sits between generated and public."""

    GENERATING = "generating"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass
class Series:
    """A standing instruction: keep making this kind of video at this rate."""

    id: str
    name: str
    niche: str
    shorts_per_week: int = 3
    long_per_week: int = 1
    voice: str = ""
    aspect_short: str = "9:16"
    aspect_long: str = "16:9"

    #: Off by default, and deliberately awkward to turn on.
    auto_publish: bool = False
    #: Hard monthly ceiling for this series.
    monthly_budget_usd: float = 120.0
    paused: bool = False

    def weekly_target(self) -> int:
        return self.shorts_per_week + self.long_per_week


@dataclass
class SpendLedger:
    """Actual spend, recorded per video. Estimates are never used as a substitute."""

    entries: list[tuple[datetime, str, float]] = field(default_factory=list)

    def record(self, series_id: str, usd: float, at: datetime | None = None) -> None:
        self.entries.append((at or datetime.now(UTC), series_id, usd))

    def spent_today(self) -> float:
        today = datetime.now(UTC).date()
        return sum(usd for at, _, usd in self.entries if at.date() == today)

    def spent_this_month(self, series_id: str) -> float:
        now = datetime.now(UTC)
        return sum(
            usd
            for at, sid, usd in self.entries
            if sid == series_id and at.year == now.year and at.month == now.month
        )

    def by_day(self, days: int = 30) -> dict[date, float]:
        today = datetime.now(UTC).date()
        out = {today - timedelta(days=n): 0.0 for n in range(days)}
        for at, _, usd in self.entries:
            if at.date() in out:
                out[at.date()] += usd
        return out


@dataclass
class BudgetPolicy:
    per_video_usd: float = 8.0
    per_day_usd: float = 40.0


@dataclass
class Blocker:
    """One reason a video cannot proceed. Shown verbatim in the review queue —
    'blocked' with no reason is not an acceptable state to display."""

    code: str
    message: str
    fixable: bool = True


def check_budget(
    series: Series,
    ledger: SpendLedger,
    policy: BudgetPolicy,
    *,
    estimate_usd: float,
) -> list[Blocker]:
    """Refuse work that cannot afford to finish. Checked before it starts."""
    blockers: list[Blocker] = []

    if estimate_usd > policy.per_video_usd:
        blockers.append(
            Blocker(
                "per_video_cap",
                f"estimated ${estimate_usd:.2f} exceeds the ${policy.per_video_usd:.2f} "
                f"per-video ceiling",
            )
        )

    day_spent = ledger.spent_today()
    if day_spent + estimate_usd > policy.per_day_usd:
        blockers.append(
            Blocker(
                "daily_cap",
                f"${day_spent:.2f} spent today; this would exceed the "
                f"${policy.per_day_usd:.2f} daily ceiling",
            )
        )

    month_spent = ledger.spent_this_month(series.id)
    if month_spent + estimate_usd > series.monthly_budget_usd:
        blockers.append(
            Blocker(
                "series_budget",
                f"“{series.name}” has spent ${month_spent:.2f} of its "
                f"${series.monthly_budget_usd:.2f} monthly budget",
            )
        )

    return blockers


@dataclass
class VideoState:
    """What the approval gate inspects. Populated from the workflow's stage outputs."""

    id: str
    series_id: str
    stage: Stage = Stage.GENERATING
    cost_usd: float = 0.0
    has_sources: bool = False
    source_count: int = 0
    has_thumbnail: bool = False
    has_seo: bool = False
    keyword_grounded: bool = False
    render_ok: bool = False
    title: str = ""
    critique_severity: int = 0


def publish_blockers(video: VideoState, series: Series) -> list[Blocker]:
    """Everything standing between this video and being published.

    These are not style preferences. Each one corresponds to a way an automated
    channel gets demonetised or embarrasses its owner.
    """
    blockers: list[Blocker] = []

    if not video.render_ok:
        blockers.append(Blocker("no_render", "the video has not rendered"))

    if not video.has_sources or video.source_count == 0:
        blockers.append(
            Blocker(
                "ungrounded",
                "the script cites no sources — this is what YouTube's "
                "inauthentic-content policy targets",
                fixable=True,
            )
        )

    if not video.keyword_grounded:
        blockers.append(
            Blocker("ungrounded_seo", "the SEO package was written without keyword data")
        )

    if not video.has_seo:
        blockers.append(Blocker("no_seo", "no title, description or tags"))

    if not video.has_thumbnail:
        blockers.append(Blocker("no_thumbnail", "no thumbnail was produced"))

    if len(video.title) > 100:
        blockers.append(
            Blocker(
                "title_too_long", f"title is {len(video.title)} characters; the API limit is 100"
            )
        )

    if video.critique_severity >= 4:
        blockers.append(
            Blocker(
                "weak_script",
                f"the critique pass rated this {video.critique_severity}/5 for problems",
            )
        )

    return blockers


def resolve_stage(video: VideoState, series: Series) -> tuple[Stage, list[Blocker]]:
    """Decide where a finished video goes next.

    Auto-publish requires the series to have it enabled *and* a clean checklist.
    A series with auto-publish on does not get to skip the checks — it gets to skip
    the waiting.
    """
    blockers = publish_blockers(video, series)

    if blockers:
        return Stage.NEEDS_REVIEW, blockers
    if series.auto_publish and not series.paused:
        return Stage.APPROVED, []
    return Stage.NEEDS_REVIEW, []


@dataclass
class RunPlan:
    to_generate: list[Idea] = field(default_factory=list)
    blocked: list[Blocker] = field(default_factory=list)

    def summary(self) -> str:
        if self.blocked:
            return f"{len(self.to_generate)} queued · {len(self.blocked)} blockers"
        return f"{len(self.to_generate)} queued"


def plan_week(
    series: Series,
    backlog: list[Idea],
    ledger: SpendLedger,
    policy: BudgetPolicy,
    *,
    already_this_week: int = 0,
    estimate_per_video_usd: float = 2.5,
) -> RunPlan:
    """What this series should generate now.

    Deliberately conservative: it will produce fewer videos than the cadence asks for
    rather than dip into a stale backlog or spend past a ceiling. A cadence is a
    target, not an obligation.
    """
    plan = RunPlan()

    if series.paused:
        plan.blocked.append(Blocker("paused", f"“{series.name}” is paused", fixable=True))
        return plan

    wanted = max(0, series.weekly_target() - already_this_week)
    if wanted == 0:
        return plan

    budget_blockers = check_budget(series, ledger, policy, estimate_usd=estimate_per_video_usd)
    if budget_blockers:
        plan.blocked.extend(budget_blockers)
        return plan

    # How many more we can afford this month, at the estimate.
    remaining = series.monthly_budget_usd - ledger.spent_this_month(series.id)
    affordable = int(remaining // estimate_per_video_usd)
    if affordable < wanted:
        plan.blocked.append(
            Blocker(
                "budget_limits_cadence",
                f"budget allows {affordable} more videos this month, cadence wants {wanted}",
                fixable=True,
            )
        )

    take = min(wanted, max(affordable, 0))
    candidates = next_up(backlog, take)

    if len(candidates) < take:
        plan.blocked.append(
            Blocker(
                "thin_backlog",
                f"backlog has {len(candidates)} usable ideas but {take} are needed — "
                f"publishing something weak on schedule is worse than publishing "
                f"nothing",
                fixable=True,
            )
        )

    plan.to_generate = candidates
    return plan
