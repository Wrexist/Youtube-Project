"""Trend monitoring (FIX-TASKS.md E3).

`ideas.build_backlog` / `build_backlog_async` have always accepted a
`trending_terms` argument that feeds straight into `score_idea`'s `freshness`
component — and until this module existed, nothing supplied it, so `freshness`
was zero for every idea ever scored. This is the source.

Two independent signals, combined:

  * **YouTube's own Trending feed** (`providers.youtube.YouTube.trending`,
    `videos.list` with `chart=mostPopular`) — 1 quota unit, cheap enough to poll
    on every backlog build. What is actually moving on the platform right now,
    region-scoped and optionally category-filtered.
  * **Rising autocomplete** — today's `research.keywords.suggest()` output for
    the channel's niche, diffed against the last snapshot taken for that seed
    (`engine.tables.KeywordSnapshot`, via `engine.repository`). A topic moving on
    the search side shows up as autocomplete queries that were not there last
    time this ran.

Both degrade to `[]` rather than raising. No YouTube client, no niche seed, no
prior snapshot, an unreachable API — every one of those is a reason to score
freshness at zero, not a reason to fail a backlog build. Same contract as
`providers.tiktok.trends()`, which this deliberately mirrors: "empty means
honestly nothing, not invented."
"""

from __future__ import annotations

import asyncio

from loguru import logger

from engine.research import keywords as kw


async def youtube_trending_terms(
    youtube_client,
    *,
    region_code: str = "US",
    category_id: str | None = None,
    limit: int = 25,
) -> list[str]:
    """Titles from YouTube's Trending feed, or `[]` with no client / on failure.

    A trend source failing must not fail the backlog build it feeds — the whole
    point of `trending_terms` being optional everywhere it is threaded through.
    """
    if youtube_client is None:
        return []
    try:
        return await youtube_client.trending(
            region_code=region_code, category_id=category_id, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 — degrade to "nothing trending", not a crash
        logger.warning("YouTube trending unavailable: {}", exc)
        return []


async def rising_autocomplete_terms(seed: str, *, limit: int = 20) -> list[str]:
    """Autocomplete queries for `seed` that are new since the last time this ran.

    The first poll for any seed has nothing to compare against, so every query
    counts as "rising". That is deliberate, not an edge case worth special-casing:
    the alternative — returning nothing on a seed's first poll — would make
    freshness silently zero for exactly the channels that most need a trend
    signal, the ones just getting started.

    Imports `engine.repository` inside the function for the same reason
    `api/ideas.py` does: avoiding a module-level import cycle back into here.
    """
    if not seed:
        return []
    from engine import repository

    today = await kw.suggest(seed, expand=False)
    if not today:
        return []
    previous = set(await repository.get_keyword_snapshot(seed))
    rising = [term for term in today if term not in previous]
    await repository.save_keyword_snapshot(seed, today)
    return rising[:limit]


async def gather_trending_terms(
    *,
    youtube_client=None,
    seed: str = "",
    region_code: str = "US",
    category_id: str | None = None,
) -> list[str]:
    """Both signals, combined. Never raises — `[]` means "nothing configured or moving".

    Run concurrently: they hit two unrelated APIs (YouTube Data, YouTube
    autocomplete) and neither depends on the other's result.
    """
    yt_terms, rising = await asyncio.gather(
        youtube_trending_terms(youtube_client, region_code=region_code, category_id=category_id),
        rising_autocomplete_terms(seed),
    )
    return [*yt_terms, *rising]
