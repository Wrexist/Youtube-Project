"""Keyword grounding.

An LLM guessing at keywords is worthless — it produces plausible phrases nobody
searches for. Everything in the SEO workflow is built on the three sources here:

  1. YouTube autocomplete — free, no quota, and it reflects what people actually type
  2. Competitor titles via search.list — 100 quota units, so cached hard
  3. External volume data — optional, filled in by the Semrush integration

`suggest()` is the workhorse: it costs nothing and returns real queries.
"""

from __future__ import annotations

import asyncio
import json
import string
from dataclasses import dataclass, field

import httpx
from loguru import logger

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


@dataclass
class KeywordEvidence:
    seed: str
    suggestions: list[str] = field(default_factory=list)
    competitor_titles: list[dict] = field(default_factory=list)
    volumes: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        return bool(self.suggestions or self.competitor_titles)

    def summary(self) -> str:
        return (
            f"{len(self.suggestions)} queries · {len(self.competitor_titles)} competitors"
        )


async def suggest(seed: str, *, expand: bool = True, timeout: float = 8.0) -> list[str]:
    """YouTube autocomplete for a seed, optionally expanded with 'seed a'..'seed z'.

    Alphabet expansion is 27 requests. They're free and parallel, and the long-tail
    phrases they surface are the ones worth ranking for.
    """
    queries = [seed]
    if expand:
        queries += [f"{seed} {letter}" for letter in string.ascii_lowercase]

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_suggest_one(client, q) for q in queries), return_exceptions=True
        )

    seen: dict[str, None] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        for phrase in result:
            seen.setdefault(phrase.lower().strip(), None)
    return list(seen)


async def _suggest_one(client: httpx.AsyncClient, query: str) -> list[str]:
    resp = await client.get(
        SUGGEST_URL, params={"client": "firefox", "ds": "yt", "q": query}
    )
    resp.raise_for_status()
    payload = json.loads(resp.text)
    return payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []


async def competitors(
    keyword: str, youtube_client=None, limit: int = 20
) -> list[dict]:
    """Top-ranking videos for a keyword.

    Costs 100 quota units against the same 10,000/day budget that uploads draw from,
    so the caller is responsible for caching (7 days is the standing policy). Returns
    an empty list rather than raising when no channel is connected — SEO should still
    work on autocomplete alone.
    """
    if youtube_client is None:
        logger.info("no YouTube client; skipping competitor mining for {!r}", keyword)
        return []

    items = await youtube_client.search(keyword, limit=limit)
    return [
        {
            "title": item["snippet"]["title"],
            "channel": item["snippet"]["channelTitle"],
            "video_id": item["id"]["videoId"],
            "published_at": item["snippet"]["publishedAt"],
        }
        for item in items
    ]


async def gather(
    seed: str, *, youtube_client=None, use_competitors: bool = True
) -> KeywordEvidence:
    """Everything we can learn about a topic before writing a single title."""
    evidence = KeywordEvidence(seed=seed)

    suggestions_task = suggest(seed)
    competitor_task = (
        competitors(seed, youtube_client) if use_competitors else _empty()
    )
    suggestions, competitor_list = await asyncio.gather(
        suggestions_task, competitor_task, return_exceptions=True
    )

    if not isinstance(suggestions, Exception):
        evidence.suggestions = suggestions
        evidence.sources.append("youtube_autocomplete")
    if not isinstance(competitor_list, Exception) and competitor_list:
        evidence.competitor_titles = competitor_list
        evidence.sources.append("youtube_search")

    return evidence


async def _empty() -> list[dict]:
    return []
