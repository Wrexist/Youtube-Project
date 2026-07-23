"""The idea backlog.

An automated channel needs a queue of things to make. The failure mode nobody
anticipates is not running dry — it is the generator quietly producing the same video
four times with different wording, because "why bridges collapse" and "the reason
bridges fail" score identically against the same keyword data.

So duplicate detection is not a nicety here, it is the thing that makes unattended
generation safe. It runs against the published catalogue *and* against the rest of
the backlog.

Scoring is deliberately transparent — four named components the user can see on the
idea card — rather than one opaque number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

# Below this Jaccard similarity on content words, two topics are distinct enough.
# Tuned so "why bridges collapse" vs "the reason bridges fail" is caught (0.5+)
# while "why bridges collapse" vs "why dams fail" is not.
DUPLICATE_THRESHOLD = 0.45

# Words that carry no topical meaning and would inflate every similarity score.
STOPWORDS = frozenset(
    """a an the and or but of for to in on at by with from is are was were be been
    this that these those it its how why what when where who which do does did
    can could should would will your you i we they he she reason reasons about""".split()
)


class IdeaStatus(StrEnum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass
class Idea:
    topic: str
    source: str = "manual"  # manual | keyword_gap | trend | series
    status: IdeaStatus = IdeaStatus.BACKLOG
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Score components, kept separate so the idea card can show the reasoning.
    demand: float = 0.0  # how much people search for it
    competition: float = 0.0  # how contested it is (lower is better)
    fit: float = 0.0  # how close to what this channel already does well
    freshness: float = 0.0  # whether it is currently moving

    duplicate_of: str | None = None
    similarity: float = 0.0
    notes: str = ""

    @property
    def score(self) -> float:
        """Weighted, and weighted toward demand — a perfectly-fitting idea nobody
        searches for is still a video nobody watches."""
        return round(
            0.40 * self.demand
            + 0.25 * (1.0 - self.competition)
            + 0.20 * self.fit
            + 0.15 * self.freshness,
            3,
        )

    def summary(self) -> str:
        if self.duplicate_of:
            return f"duplicate of “{self.duplicate_of}” ({self.similarity:.0%})"
        return f"{self.score:.2f} · demand {self.demand:.2f} · fit {self.fit:.2f}"


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def similarity(a: str, b: str) -> float:
    """Jaccard overlap on content words.

    Deliberately not an embedding model: this runs on every idea against the whole
    catalogue, it needs to be explainable on the idea card, and the failure it
    guards against is near-identical *wording*, which token overlap catches well.
    """
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_duplicate(topic: str, existing: list[str]) -> tuple[str | None, float]:
    """The closest already-covered topic, if it is close enough to matter."""
    best: tuple[str | None, float] = (None, 0.0)
    for candidate in existing:
        score = similarity(topic, candidate)
        if score > best[1]:
            best = (candidate, score)
    if best[1] >= DUPLICATE_THRESHOLD:
        return best
    return (None, best[1])


def score_idea(
    topic: str,
    *,
    suggestions: list[str],
    competitor_count: int,
    competitor_median_views: int = 0,
    channel_topics: list[str] | None = None,
    trending_terms: list[str] | None = None,
) -> Idea:
    """Build a scored idea from the evidence available.

    All four components are derived from real data, or they are zero. There is no
    model guessing at "demand" — that would defeat the point of the whole SEO chain.
    """
    idea = Idea(topic=topic)
    tokens = tokenize(topic)

    # Demand: how many real autocomplete queries this topic covers. Saturates at 20 —
    # beyond that the difference stops being meaningful.
    matches = sum(1 for s in suggestions if tokens & tokenize(s))
    idea.demand = min(matches / 20, 1.0)

    # Competition: many strong incumbents is bad. A topic with no competitors at all
    # is usually a topic with no audience, so zero is not treated as ideal.
    if competitor_count == 0:
        idea.competition = 0.7
        idea.notes = "no competing videos found — often means no audience, not an opening"
    else:
        density = min(competitor_count / 20, 1.0)
        strength = min(competitor_median_views / 500_000, 1.0)
        idea.competition = round(0.6 * density + 0.4 * strength, 3)

    # Fit: overlap with what this channel already covers. Rewards adjacency, and
    # exact repetition is caught separately by the duplicate check.
    if channel_topics:
        idea.fit = round(max((similarity(topic, t) for t in channel_topics), default=0.0) * 2, 3)
        idea.fit = min(idea.fit, 1.0)

    if trending_terms:
        idea.freshness = 1.0 if any(tokens & tokenize(t) for t in trending_terms) else 0.0

    return idea


def build_backlog(
    candidates: list[str],
    *,
    published_topics: list[str],
    suggestions: list[str],
    competitor_counts: dict[str, int] | None = None,
    trending_terms: list[str] | None = None,
) -> list[Idea]:
    """Score and de-duplicate a batch of candidate topics.

    Duplicates are marked and kept rather than dropped — the user should be able to
    see that the generator tried to repeat itself and why it was stopped.
    """
    counts = competitor_counts or {}
    seen: list[str] = list(published_topics)
    out: list[Idea] = []

    for topic in candidates:
        idea = score_idea(
            topic,
            suggestions=suggestions,
            competitor_count=counts.get(topic, 0),
            channel_topics=published_topics,
            trending_terms=trending_terms,
        )
        duplicate, score = find_duplicate(topic, seen)
        if duplicate:
            idea.duplicate_of = duplicate
            idea.similarity = round(score, 3)
            idea.status = IdeaStatus.REJECTED
        else:
            # Compared against the rest of this batch too, or a single run can emit
            # three phrasings of one idea and none of them look like duplicates.
            seen.append(topic)
        idea.similarity = idea.similarity or round(score, 3)
        out.append(idea)

    out.sort(key=lambda i: (i.status is IdeaStatus.REJECTED, -i.score))
    return out


def next_up(backlog: list[Idea], count: int, *, max_age_days: int = 45) -> list[Idea]:
    """Pull the next ideas to produce.

    Stale ideas are skipped: a topic that has sat in the backlog for six weeks was
    scored against search data that has since moved.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    fresh = [
        idea for idea in backlog if idea.status is IdeaStatus.BACKLOG and idea.created_at >= cutoff
    ]
    return sorted(fresh, key=lambda i: -i.score)[:count]
