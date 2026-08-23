"""Where demand outruns supply — the gap detector.

Demand signals are free (autocomplete sweeps, trending terms); supply has
always been the expensive half. `search.list` costs 100 units per query,
which is why `api/ideas._score` deliberately scored competition at zero and
its `_why` had to apologise for it. This module replaces that compromise for
watched niches: supply is measured against the watchlist corpus, which is
already on disk and costs nothing to query.

That changes what idea scores *mean*, and the docstring on
`competitor_counts` says so at the point of use rather than burying it here.
"""

from __future__ import annotations

from typing import Any

from engine.ideas import tokenize

#: A watched video counts as covering a topic when this fraction of the topic's
#: content words appear in its title. High enough that "Baltimore bridge
#: collapse" does not match every video mentioning bridges; low enough that a
#: reworded competitor still counts. Titles are short, so this is a floor on
#: shared substance, not fuzzy matching.
_MIN_COVER = 0.6


def _covered(topic_tokens: set[str], title_tokens: set[str]) -> bool:
    if not topic_tokens:
        return False
    return len(topic_tokens & title_tokens) / len(topic_tokens) >= _MIN_COVER


def supply_count(topic: str, videos: list[dict[str, Any]]) -> int:
    """How many watched videos already cover this topic."""
    topic_tokens = tokenize(topic)
    return sum(1 for v in videos if _covered(topic_tokens, tokenize(v.get("title", ""))))


def competitor_counts(candidates: list[str], videos: list[dict[str, Any]]) -> dict[str, int]:
    """Per-candidate incumbent counts, in the shape `score_idea` consumes.

    **These counts are a floor, not a census** — they cover only channels the
    operator chose to watch. A count of zero means "none of our watched
    channels have covered this", which for a thin watchlist is weak evidence.
    Callers passing this into `ideas.build_backlog_async` should say so in
    their user-facing copy; the numbers are real, the denominator is not all
    of YouTube.
    """
    return {topic: supply_count(topic, videos) for topic in candidates}


async def competitor_counts_for(candidates: list[str]) -> dict[str, int]:
    """Repository-backed convenience wrapper for API callers."""
    videos = await _corpus()
    return competitor_counts(candidates, videos)


def _matches_demand(topic: str, suggestions: list[str]) -> int:
    """Autocomplete queries that share at least one content word with the topic."""
    topic_tokens = tokenize(topic)
    if not topic_tokens:
        return 0
    return sum(1 for s in suggestions if topic_tokens & tokenize(s))


async def _corpus() -> list[dict[str, Any]]:
    # Imported here, like every other module that reaches into the repository:
    # keeps the import graph acyclic at module-load time.
    from engine import repository

    return await repository.watched_videos_for_mining()


async def score_gaps(candidates: list[str], *, suggestions: list[str]) -> list[dict[str, Any]]:
    """Demand ÷ supply per candidate, ranked most-open first.

    `gap` divides demand by one more than supply, so an unwatched topic can
    still rank on demand alone while a saturated one sinks even with high
    demand — the shape a content decision actually needs. Every component is
    reported alongside the score so the screen can show its work.
    """
    videos = await _corpus()
    out: list[dict[str, Any]] = []
    for topic in candidates:
        demand = _matches_demand(topic, suggestions)
        supply = supply_count(topic, videos)
        out.append(
            {
                "topic": topic,
                "autocomplete_matches": demand,
                "watched_videos_on_topic": supply,
                "gap": round(demand / (1 + supply), 3),
            }
        )
    out.sort(key=lambda r: (-r["gap"], -r["autocomplete_matches"], r["topic"]))
    return out
