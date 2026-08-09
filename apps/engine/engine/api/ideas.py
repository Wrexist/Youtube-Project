"""Video ideas worth making next, scored against real demand.

What this is not: a virality predictor. Nobody has one, and a number invented by a
model would be worse than no number — it reads as evidence and is not. What is on
offer here is the same thing the SEO chain is built on, applied one step earlier:

  * **demand** — how many real YouTube autocomplete queries the topic covers.
    Queries people actually typed, not a model's opinion about interest.
  * **competition** — how crowded the topic already is. Nothing competing is
    usually no audience rather than an opening, and `score_idea` says so.
  * **fit** — adjacency to what this channel has already made.
  * **freshness** — overlap with anything currently moving, when a trend source is
    configured. Zero otherwise, honestly, rather than guessed.

The model's only job is proposing candidates. Every number attached to them comes
from `engine.ideas`, which is the existing scorer used by the channel-launch
backlog — the same weighting, so a suggestion here and a backlog item there cannot
disagree about what a good idea looks like.

Cached, because this costs a model call plus an autocomplete sweep per candidate
and the Create screen would otherwise pay for it on every page load.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from engine.ideas import build_backlog_async
from engine.providers import llm
from engine.providers.llm import ProviderUnavailable
from engine.research import keywords

router = APIRouter(prefix="/v1/ideas", tags=["ideas"])

#: How long a set of suggestions stays good for. Long enough that clicking around
#: the app is free, short enough that a channel which just published something
#: sees the effect the same day.
_TTL_SECONDS = 30 * 60

#: How many candidates to ask for. Each one costs an autocomplete sweep, which is
#: free but not instant, and they are scored in parallel.
_CANDIDATES = 8

_cache: dict[str, tuple[float, list[dict]]] = {}


class Suggestion(BaseModel):
    topic: str
    score: float
    demand: float
    competition: float
    why: str


class Suggestions(BaseModel):
    suggestions: list[Suggestion]
    #: What the suggestions were based on. Empty when there is no history yet,
    #: which is a state the screen shows differently rather than filling with
    #: generic ideas nobody asked for.
    based_on: list[str]


@router.get("/suggestions")
async def suggestions(limit: int = 4, refresh: bool = False) -> Suggestions:
    recent = _recent_topics()
    if not recent:
        # A first run has nothing to be adjacent to. Suggesting something anyway
        # would mean inventing a niche for someone who has not chosen one.
        return Suggestions(suggestions=[], based_on=[])

    key = "|".join(sorted(recent))
    cached = _cache.get(key)
    if cached and not refresh and time.monotonic() - cached[0] < _TTL_SECONDS:
        return Suggestions(
            suggestions=[Suggestion(**s) for s in cached[1][:limit]], based_on=recent
        )

    try:
        candidates = await _propose(recent)
    except ProviderUnavailable as exc:
        logger.warning("cannot suggest ideas: {}", exc)
        return Suggestions(suggestions=[], based_on=recent)

    scored = await _score(candidates, published=recent)
    _cache[key] = (time.monotonic(), scored)
    return Suggestions(suggestions=[Suggestion(**s) for s in scored[:limit]], based_on=recent)


def _recent_topics(count: int = 8) -> list[str]:
    """What this channel has already made, newest first.

    Imported inside the function: `engine.main` imports this router at module
    level, so a module-level import back into it would be a cycle. By the time a
    request arrives, main is long since loaded.
    """
    from engine.main import JOBS

    seen: list[str] = []
    for job in reversed(list(JOBS.values())):
        topic = str(job.get("inputs", {}).get("topic", "")).strip()
        if topic and topic not in seen:
            seen.append(topic)
        if len(seen) >= count:
            break
    return seen


async def _propose(recent: list[str]) -> list[str]:
    """Candidate topics in this channel's niche. The model's only job here."""
    made = "\n".join(f"- {t}" for t in recent)
    model = llm.for_task("backlog")
    result, _ = await model.json(
        f"""This channel has made these videos:

{made}

Propose {_CANDIDATES} new video topics for the same channel.

Each one must be:

- **A single video, not a category.** "how MrBeast's giveaways make money", not
  "MrBeast's business".
- **Phrased the way a viewer would search for it.** These are checked against
  YouTube autocomplete immediately after you return them, and a topic nobody
  types scores zero however interesting it sounds.
- **Adjacent, not identical.** Anything too close to the list above is discarded
  automatically, so a near-duplicate is a wasted slot.
- **Answerable from public sources.** No invented statistics.

Spread them out: some squarely in the middle of what this channel does, some one
step to the side. Do not rank them — that is done from real data afterwards.

Return: {{"topics": [str]}}""",
        max_tokens=1200,
    )
    topics = [str(t).strip() for t in (result.get("topics") or []) if str(t).strip()]
    return topics[:_CANDIDATES]


async def _score(candidates: list[str], *, published: list[str]) -> list[dict]:
    """Ground every candidate in autocomplete, then score and rank them.

    The sweeps run together — they are free, independent, and doing them in
    sequence would make this take as long as the number of candidates.
    """
    gathered = await asyncio.gather(
        *(keywords.suggest(topic, expand=False) for topic in candidates),
        return_exceptions=True,
    )

    pooled: list[str] = []
    for result in gathered:
        if isinstance(result, list):
            pooled.extend(result)

    ideas = await build_backlog_async(
        candidates,
        published_topics=published,
        suggestions=pooled,
    )

    out: list[dict] = []
    for idea in ideas:
        if idea.duplicate_of:
            continue
        out.append(
            {
                "topic": idea.topic,
                "score": idea.score,
                "demand": idea.demand,
                "competition": idea.competition,
                # Deliberately not `idea.notes`. With no competitor counts passed,
                # `score_idea` fills that field with "no competing videos found —
                # often means no audience, not an opening", which is a statement
                # about a search that never happened. See `_why`.
                "why": _why(idea),
            }
        )
    return out


def _why(idea) -> str:
    """One line the operator can act on, in terms of the evidence behind it.

    Says nothing about competition, because competition was not measured.
    Counting incumbents means `search.list`, which costs 100 quota units against
    the same 10,000/day budget uploads draw from and needs a channel connected —
    far too much to spend on eight speculative candidates every time someone
    opens the Create screen. `score_idea` therefore sees a count of zero for all
    of them, which is uniform and so does not skew the ranking, but it must not
    be reported as evidence of an empty field.
    """
    if idea.demand >= 0.5:
        return f"{round(idea.demand * 20)} autocomplete queries match this"
    if idea.demand > 0:
        return f"only {round(idea.demand * 20)} autocomplete queries match this"
    return "no autocomplete matches - nobody is searching this phrasing"
