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

from engine.settings import get_settings

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


@dataclass
class KeywordEvidence:
    seed: str
    suggestions: list[str] = field(default_factory=list)
    competitor_titles: list[dict] = field(default_factory=list)
    volumes: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    # Why each source produced nothing, e.g. {"youtube_autocomplete": "27/27
    # requests failed (ConnectError)"}. Empty when everything worked.
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def is_grounded(self) -> bool:
        return bool(self.suggestions or self.competitor_titles)

    def summary(self) -> str:
        return f"{len(self.suggestions)} queries · {len(self.competitor_titles)} competitors"

    def diagnosis(self) -> str:
        """Why grounding came back empty, in terms an operator can act on.

        A blocked network, a rate-limit and a genuinely obscure topic all used to
        produce the same bare "no keyword evidence". They need different fixes, so
        they need different messages — and this is the first stage of the only
        workflow, so it is the first thing every new user meets.
        """
        if not self.failures:
            return (
                f"no keyword evidence for {self.seed!r}: every source responded but "
                "returned nothing. The topic may be too obscure or too long — try a "
                "shorter, more common phrasing."
            )
        detail = "; ".join(f"{source} {why}" for source, why in sorted(self.failures.items()))
        return (
            f"no keyword evidence for {self.seed!r}: {detail}. "
            "Check outbound network access — these endpoints are commonly blocked "
            "on datacenter and CI networks."
        )


async def suggest(seed: str, *, expand: bool = True, timeout: float = 8.0) -> list[str]:
    """YouTube autocomplete for a seed, optionally expanded with 'seed a'..'seed z'.

    Alphabet expansion is 27 requests. They're free and parallel, and the long-tail
    phrases they surface are the ones worth ranking for.
    """
    phrases, _ = await suggest_with_failures(seed, expand=expand, timeout=timeout)
    return phrases


async def suggest_with_failures(
    seed: str, *, expand: bool = True, timeout: float = 8.0
) -> tuple[list[str], str]:
    """`suggest`, plus a description of what went wrong when nothing comes back.

    The exceptions used to be dropped on the floor, which made a blocked network
    and a genuinely empty result indistinguishable — and the caller then raised a
    message that helped with neither.
    """
    queries = [seed]
    if expand:
        queries += [f"{seed} {letter}" for letter in string.ascii_lowercase]

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_suggest_one(client, q) for q in queries), return_exceptions=True
        )

    seen: dict[str, None] = {}
    errors: list[Exception] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(result)
            continue
        for phrase in result:
            seen.setdefault(phrase.lower().strip(), None)

    if errors:
        # One line naming the dominant cause, not 27 identical tracebacks.
        logger.warning(
            "autocomplete: {}/{} requests failed for {!r}, first: {}",
            len(errors),
            len(queries),
            seed,
            _describe(errors[0]),
        )

    if seen or not errors:
        return list(seen), ""
    return [], f"{len(errors)}/{len(queries)} requests failed ({_describe(errors[0])})"


def _describe(exc: Exception) -> str:
    """A short, specific cause. `repr` of an httpx error is mostly noise."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    return type(exc).__name__


async def _suggest_one(client: httpx.AsyncClient, query: str) -> list[str]:
    resp = await client.get(SUGGEST_URL, params={"client": "firefox", "ds": "yt", "q": query})
    resp.raise_for_status()
    payload = json.loads(resp.text)
    return payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []


async def competitors(keyword: str, youtube_client=None, limit: int = 20) -> list[dict]:
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

    suggestions_task = suggest_with_failures(seed)
    competitor_task = competitors(seed, youtube_client) if use_competitors else _empty()
    suggestions, competitor_list = await asyncio.gather(
        suggestions_task, competitor_task, return_exceptions=True
    )

    if isinstance(suggestions, Exception):
        evidence.failures["youtube_autocomplete"] = _describe(suggestions)
    else:
        phrases, failure = suggestions
        evidence.suggestions = phrases
        if phrases:
            evidence.sources.append("youtube_autocomplete")
        if failure:
            evidence.failures["youtube_autocomplete"] = failure

    if isinstance(competitor_list, Exception):
        evidence.failures["youtube_search"] = _describe(competitor_list)
    elif competitor_list:
        evidence.competitor_titles = competitor_list
        evidence.sources.append("youtube_search")
    elif youtube_client is None:
        evidence.failures["youtube_search"] = "skipped (no channel connected)"

    # Only when the free sources produced nothing. Both are unauthenticated
    # endpoints that routinely block datacenter IPs, which is precisely where this
    # gets deployed — so grounding had a single point of failure and no way past
    # it. The fallback is tried second rather than in parallel because it costs
    # money and the free path usually works.
    if not evidence.is_grounded:
        phrases, failure = await _fallback_suggest(seed)
        if phrases:
            evidence.suggestions = phrases
            evidence.sources.append("keyword_api")
        elif failure:
            evidence.failures["keyword_api"] = failure

    return evidence


async def _fallback_suggest(seed: str, *, timeout: float = 10.0) -> tuple[list[str], str]:
    """A keyed keyword source, for when the free ones are blocked.

    OpenAI-compatible in shape only: any endpoint that answers
    `GET {base}?q={seed}` with a JSON list of strings, or `{"keywords": [...]}`,
    will do. Kept deliberately generic because the point is *having a second
    source*, not endorsing a particular vendor — and an operator on a blocked
    network needs to be able to point this at whatever they already pay for.

    Returns ([], "") when unconfigured: not having a fallback is the default, and
    it must not read as a failure in the diagnosis.
    """
    settings = get_settings()
    base = settings.keyword_api_url
    if not base:
        return [], ""

    headers = {}
    if settings.keyword_api_key:
        headers["Authorization"] = f"Bearer {settings.keyword_api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(base, params={"q": seed}, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — a dead fallback is not fatal
        logger.warning("keyword fallback failed for {!r}: {}", seed, _describe(exc))
        return [], _describe(exc)

    if isinstance(payload, dict):
        payload = payload.get("keywords") or payload.get("results") or []
    if not isinstance(payload, list):
        return [], "unexpected response shape (expected a list of keywords)"

    phrases = [str(p).lower().strip() for p in payload if isinstance(p, str | int | float)]
    if phrases:
        logger.info("keyword fallback supplied {} phrases for {!r}", len(phrases), seed)
    return phrases, ""


async def _empty() -> list[dict]:
    return []
