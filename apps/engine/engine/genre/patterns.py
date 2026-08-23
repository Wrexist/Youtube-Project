"""What the niche rewards, read structurally off the watched corpus.

Pure functions over rows from `repository.watched_videos_for_mining` — no I/O,
no clocks beyond an injectable `now`, so every claim here is testable against
fixed data.

Two questions answered:

  * **Which hooks win here?** Every title is classified into one of six hook
    strategies (the same strategies Phase 4's title generator writes), and each
    strategy reports its share of the corpus plus median views and velocity.
    This turns "curiosity gaps feel right for this niche" into "curiosity-gap
    titles are 31% of what these channels publish and carry the highest median
    velocity" — evidence the prompt chains can quote.
  * **What shape does the niche run?** Duration distribution and posting
    cadence, so duration targeting and series cadence come from observed
    behaviour rather than defaults.

Velocity is `views / days-since-publish`, computed from the latest counters —
the honest cheap metric. It undercounts old videos' total reach and overweights
recent uploads deliberately: for "what is working *now*", recency is the point.
"""

from __future__ import annotations

import re
import statistics
from datetime import UTC, datetime
from typing import Any

#: Hook strategies, first-match-wins in this order. The order encodes editorial
#: judgement about which signal dominates when several appear: "Why I Stopped
#: Buying New Cars" is a contrarian video even though it opens like a question;
#: "7 Reasons Bridges Fail" is a number even though "why" leads the tail.
HOOK_PATTERNS: tuple[str, ...] = (
    "contrarian",
    "number",
    "question",
    "outcome",
    "curiosity",
    "statement",
)

_QUESTION_START = re.compile(
    r"^(why|how|what|can|could|should|does|do|is|are|will|which|who|when|where)\b",
    re.IGNORECASE,
)
_OUTCOME_START = re.compile(r"^how (to|i)\b", re.IGNORECASE)
_CONTRARIAN = re.compile(
    r"\b(stop|stopped|never|don'?t|dont|myth|wrong|lie|lies|lying|truth about"
    r"|no ?one (tells|talks)|everyones? (wrong|lying)|actually|debunk)\b",
    re.IGNORECASE,
)
_NUMBER_LEAD = re.compile(r"^[\W$#]*(\d|#\d)")
_CURIOSITY_MARKERS = re.compile(
    r"(\.\.\.|you won'?t|won'?t believe|secret|until you|before you"
    r"|what happens|here'?s why|the reason why|nobody (knows|talks))",
    re.IGNORECASE,
)

#: The cadence window: recent enough to reflect what channels are doing now,
#: long enough that a three-week creative break does not read as abandonment.
_CADENCE_DAYS = 90

_TOP_VELOCITY = 5


def classify_hook(title: str) -> str:
    """One of `HOOK_PATTERNS` for a title. Never raises, never returns nothing —
    `statement` is the honest bucket for titles whose hook is neither of the
    five named shapes."""
    if not title:
        return "statement"
    if _CONTRARIAN.search(title):
        return "contrarian"
    if _NUMBER_LEAD.match(title):
        return "number"
    if _OUTCOME_START.match(title):
        # Before `_QUESTION_START`: "How to…" is an outcome promise, not a
        # question, and both regexes match it.
        return "outcome"
    if _QUESTION_START.match(title) or title.rstrip().endswith("?"):
        return "question"
    if _CURIOSITY_MARKERS.search(title):
        return "curiosity"
    return "statement"


def views_per_day(video: dict[str, Any], *, now: datetime | None = None) -> float:
    """Views per day since publication. 0.0 when unmeasurable — no publish date,
    or a future-dated (still scheduled) video whose counter means nothing yet."""
    published = video.get("published_at")
    views = float(video.get("views") or 0)
    if not published or views <= 0:
        return 0.0
    moment = now or datetime.now(UTC)
    if published.tzinfo is None:
        # SQLite returns naive datetimes; treat stored stamps as UTC everywhere
        # (`repurpose.rights._aware` made the same call for the same reason).
        published = published.replace(tzinfo=UTC)
    age_days = (moment - published).total_seconds() / 86400
    if age_days <= 0:
        return 0.0
    return views / max(age_days, 0.5)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def analyze(videos: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Aggregate the corpus into the numbers downstream consumers read.

    Empty corpus → zeroed report, never an exception: callers render this for a
    watchlist that has not synced yet, and "nothing watched yet" is a normal
    screen, not an error page.
    """
    moment = now or datetime.now(UTC)

    by_pattern: dict[str, list[dict[str, Any]]] = {p: [] for p in HOOK_PATTERNS}
    for video in videos:
        by_pattern[classify_hook(video.get("title", ""))].append(video)

    patterns_out = []
    for pattern in HOOK_PATTERNS:
        group = by_pattern[pattern]
        if not group:
            continue
        patterns_out.append(
            {
                "pattern": pattern,
                "count": len(group),
                "share": round(len(group) / len(videos), 3),
                "median_views": _median([float(v.get("views") or 0) for v in group]),
                "median_views_per_day": _median([views_per_day(v, now=moment) for v in group]),
            }
        )
    patterns_out.sort(key=lambda p: (-p["count"], p["pattern"]))

    durations = [float(v.get("duration_s") or 0) for v in videos if v.get("duration_s")]
    buckets = {
        "under_60s": sum(1 for d in durations if d < 60),
        "60s_to_8m": sum(1 for d in durations if 60 <= d < 480),
        "over_8m": sum(1 for d in durations if d >= 480),
    }

    dated = [v for v in videos if v.get("published_at") and isinstance(v["published_at"], datetime)]
    aware = []
    for v in dated:
        published = v["published_at"]
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        aware.append(published)
    window = [p for p in aware if (moment - p).days <= _CADENCE_DAYS and p <= moment]
    uploads_per_week: float | None = round(len(window) / (_CADENCE_DAYS / 7), 2) if aware else None

    ranked = sorted(
        (v for v in videos if views_per_day(v, now=moment) > 0),
        key=lambda v: views_per_day(v, now=moment),
        reverse=True,
    )[:_TOP_VELOCITY]

    return {
        "video_count": len(videos),
        "hook_patterns": patterns_out,
        "median_duration_s": _median(durations),
        "duration_buckets": buckets,
        "uploads_per_week": uploads_per_week,
        "top_by_velocity": [
            {
                "title": v.get("title", ""),
                "channel_label": v.get("channel_label", ""),
                "views": v.get("views", 0),
                "views_per_day": round(views_per_day(v, now=moment), 1),
            }
            for v in ranked
        ],
    }
