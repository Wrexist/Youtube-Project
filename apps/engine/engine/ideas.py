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

import httpx

# Below this Jaccard similarity on content words, two topics are distinct enough.
# Tuned so "why bridges collapse" vs "the reason bridges fail" is caught (0.5+)
# while "why bridges collapse" vs "why dams fail" is not.
DUPLICATE_THRESHOLD = 0.45

# Jaccard range where Ollama embedding similarity is consulted for a second opinion.
# Below SEMANTIC_LOWER the topics are clearly distinct; above DUPLICATE_THRESHOLD they
# are clearly the same.  The band in between catches "why bridges collapse" vs "the
# reason bridges fail" — same meaning, different words, Jaccard ~0.30.
SEMANTIC_LOWER = 0.20
SEMANTIC_EMBEDDING_THRESHOLD = 0.90  # cosine similarity that confirms a duplicate

_DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

#: How long a trend signal keeps half its value. Three weeks: long enough that an
#: idea captured on a Friday is still worth something at the next planning session,
#: short enough that a month-old trend stops outranking a fresh one. The `next_up`
#: cutoff sits at 45 days, by which point this has already decayed to ~23%, so the
#: hard floor removes ideas the ranking had mostly given up on anyway.
FRESHNESS_HALF_LIFE_DAYS = 21.0


def _aware(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    `created_at` round-trips through any store without a timezone type — SQLite is
    the one in the box — and comparing naive with aware raises `TypeError`, which
    would surface as a crash in scoring rather than as a stale idea.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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

    def freshness_at(self, now: datetime | None = None) -> float:
        """Trend match, decayed by how long ago the idea was scored.

        `freshness` is what the trend data said on the day this idea was written
        down, and it does not stay true. A topic that was moving six weeks ago is
        not still moving; treating the stored value as current is how a backlog
        ends up recommending last quarter's news at full confidence.

        Exponential rather than linear, and expressed as a half-life, because that
        is the shape attention actually has and the one parameter anybody can
        reason about: at `FRESHNESS_HALF_LIFE_DAYS` old it is worth half as much,
        at twice that a quarter.
        """
        # `_aware` on the *argument* too, not just on `created_at`. A caller passing
        # a naive `now` against an aware `created_at` raised TypeError, which is not
        # "stale" and reaches nobody's error handling.
        now = _aware(now or datetime.now(UTC))
        age_days = max((now - _aware(self.created_at)).total_seconds() / 86400.0, 0.0)
        return round(self.freshness * 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS), 4)

    def score_at(self, now: datetime | None = None) -> float:
        """Weighted, and weighted toward demand — a perfectly-fitting idea nobody
        searches for is still a video nobody watches."""
        return round(
            0.40 * self.demand
            + 0.25 * (1.0 - self.competition)
            + 0.20 * self.fit
            + 0.15 * self.freshness_at(now),
            3,
        )

    @property
    def score(self) -> float:
        """The score as of now. Takes a clock, so it moves — see `freshness_at`."""
        return self.score_at()

    def summary(self) -> str:
        if self.duplicate_of:
            return f"duplicate of “{self.duplicate_of}” ({self.similarity:.0%})"
        line = f"{self.score:.2f} · demand {self.demand:.2f} · fit {self.fit:.2f}"
        # Only when there is a trend signal to report. Shown because it is the one
        # component that changes on its own: an idea sliding down the backlog with
        # no edit and no new data is otherwise unexplainable from the card.
        if self.freshness:
            line += f" · trend {self.freshness_at():.2f}"
        return line


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

    # Freshness: how strongly the topic overlaps whatever is currently moving.
    #
    # Graded, not a coin flip. This used to be 1.0 for any single shared token and
    # 0.0 otherwise, which put "why bridges collapse" and "bridges" on exactly the
    # same footing against a trend for "bridge collapse baltimore" — one is the
    # trend, the other shares a word with it. `overlap` is against the *trend's*
    # tokens rather than the topic's: the question is how much of the trend this
    # idea covers, and dividing by the topic's length would reward short topics for
    # being short.
    if trending_terms:
        idea.freshness = round(
            max(
                (_overlap(tokens, tokenize(term)) for term in trending_terms),
                default=0.0,
            ),
            3,
        )

    return idea


def _overlap(tokens: set[str], trend_tokens: set[str]) -> float:
    """The fraction of a trend's content words that a topic covers."""
    if not trend_tokens:
        return 0.0
    return len(tokens & trend_tokens) / len(trend_tokens)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length embedding vectors."""
    # strict=True: a length mismatch means two different embedding models were
    # mixed, and silently truncating would return a plausible-looking score.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _get_embedding(
    text: str,
    *,
    base_url: str,
    model: str = _DEFAULT_EMBEDDING_MODEL,
) -> list[float] | None:
    """Fetch an embedding from Ollama.  Returns None gracefully on any failure.

    Ollama must be running and the model must be pulled.  The caller never has
    to check: returning None causes the embedding path to be skipped entirely.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
    except Exception:  # noqa: BLE001 — any failure → graceful fallback
        return None


async def find_duplicate_async(
    topic: str,
    existing: list[str],
    *,
    ollama_base_url: str | None = None,
) -> tuple[str | None, float, str]:
    """Like find_duplicate() but layers an Ollama embedding check for ambiguous pairs.

    Jaccard runs first — it is fast and free.  If the best Jaccard score sits in
    the ambiguous zone (SEMANTIC_LOWER ≤ score < DUPLICATE_THRESHOLD) and Ollama
    is available, an embedding similarity check is made for those candidates.  A
    cosine similarity ≥ SEMANTIC_EMBEDDING_THRESHOLD then confirms a duplicate.

    Returns ``(duplicate_or_None, similarity_score, method_description)``.  The
    method description is shown on the idea card so the user understands why
    something was flagged.

    Graceful degradation: if Ollama is not running or returns an error, falls
    through to Jaccard-only.
    """
    best_topic: str | None = None
    best_score: float = 0.0
    ambiguous: list[tuple[str, float]] = []

    for candidate in existing:
        score = similarity(topic, candidate)
        if score > best_score:
            best_topic, best_score = candidate, score
        if SEMANTIC_LOWER <= score < DUPLICATE_THRESHOLD and ollama_base_url:
            ambiguous.append((candidate, score))

    if best_score >= DUPLICATE_THRESHOLD:
        return (best_topic, best_score, f"Jaccard {best_score:.2f}")

    if ambiguous and ollama_base_url:
        topic_emb = await _get_embedding(topic, base_url=ollama_base_url)
        if topic_emb is not None:
            emb_best_candidate: str | None = None
            emb_best_cos: float = 0.0
            emb_best_jac: float = 0.0
            for candidate, jac in ambiguous:
                cand_emb = await _get_embedding(candidate, base_url=ollama_base_url)
                if cand_emb is None:
                    continue
                cos = _cosine(topic_emb, cand_emb)
                if cos > emb_best_cos:
                    emb_best_candidate, emb_best_cos, emb_best_jac = candidate, cos, jac
            if emb_best_candidate and emb_best_cos >= SEMANTIC_EMBEDDING_THRESHOLD:
                return (
                    emb_best_candidate,
                    emb_best_cos,
                    f"Jaccard {emb_best_jac:.2f} / embedding {emb_best_cos:.2f}",
                )

    return (None, best_score, "Jaccard")


async def build_backlog_async(
    candidates: list[str],
    *,
    published_topics: list[str],
    suggestions: list[str],
    competitor_counts: dict[str, int] | None = None,
    trending_terms: list[str] | None = None,
    ollama_base_url: str | None = None,
) -> list[Idea]:
    """Like build_backlog() but with optional Ollama-based semantic deduplication.

    The ``ollama_base_url`` is optional — if not provided, or if Ollama is not
    running, the function falls through to Jaccard-only deduplication, identical
    to ``build_backlog()``.
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
        duplicate, score, method = await find_duplicate_async(
            topic, seen, ollama_base_url=ollama_base_url
        )
        if duplicate:
            idea.duplicate_of = duplicate
            idea.similarity = round(score, 3)
            idea.status = IdeaStatus.REJECTED
            idea.notes = f'too similar to "{duplicate}" ({method})'
        else:
            seen.append(topic)
        idea.similarity = idea.similarity or round(score, 3)
        out.append(idea)

    out.sort(key=lambda i: (i.status is IdeaStatus.REJECTED, -i.score))
    return out


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


def next_up(
    backlog: list[Idea],
    count: int,
    *,
    max_age_days: int = 45,
    now: datetime | None = None,
) -> list[Idea]:
    """Pull the next ideas to produce.

    `max_age_days` stays a hard floor — past six weeks a topic was scored against
    search data that has genuinely moved, and no amount of ranking rescues it. But
    it is only the floor now, not the whole story: ranking uses `score_at`, so an
    idea's trend component fades continuously as it sits rather than counting for
    full value on day 44 and vanishing on day 46. The cliff was doing two jobs, and
    only one of them was a cliff.
    """
    now = _aware(now or datetime.now(UTC))
    cutoff = now - timedelta(days=max_age_days)
    fresh = [
        idea
        for idea in backlog
        # Bounded at both ends. A future `created_at` has zero age, so it kept full
        # trend weight *and* cleared the cutoff — a record dated a year ahead
        # outranked everything real for a year. Clock skew and hand-edited rows both
        # produce one.
        if idea.status is IdeaStatus.BACKLOG and cutoff <= _aware(idea.created_at) <= now
    ]
    return sorted(fresh, key=lambda i: -i.score_at(now))[:count]
