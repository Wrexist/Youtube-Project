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

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from engine import trending
from engine.api.publishing import CHANNELS
from engine.ideas import build_backlog_async
from engine.providers import llm
from engine.providers.llm import ProviderUnavailable
from engine.providers.youtube import YouTube
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


class BacklogIdeaOut(Suggestion):
    """A suggestion that has been written down, so it can be referred to later."""

    id: int


class Backlog(BaseModel):
    ideas: list[BacklogIdeaOut]
    based_on: list[str]


@router.get("/backlog")
async def backlog(limit: int = Query(6, ge=1, le=50)) -> Backlog:
    """The standing list of ideas, best first, topped up when it runs short.

    The suggestions endpoint proposes, scores, shows and forgets — a cache with a
    thirty-minute life and no memory of what the operator already refused. This is
    the same research, kept.

    Topping up is lazy on purpose: generating costs a model call and an
    autocomplete sweep per candidate, so it happens when the list is nearly empty
    rather than on a schedule nobody asked for.
    """
    from engine import repository

    recent = _recent_topics()
    existing = await repository.open_backlog_ideas(limit)

    if len(existing) < limit and recent:
        try:
            candidates, model, prompt = await _propose(recent)
            scored = await _score(candidates, published=recent)
        except ProviderUnavailable as exc:
            # Whatever is already on the list is still worth showing.
            logger.warning("cannot top up the backlog: {}", exc)
        else:
            added = await repository.add_backlog_ideas(scored, model=model, prompt=prompt)
            if added:
                existing = await repository.open_backlog_ideas(limit)

    return Backlog(ideas=[BacklogIdeaOut(**i) for i in existing], based_on=recent)


@router.post("/backlog/{idea_id}/dismiss", status_code=204)
async def dismiss(idea_id: int) -> None:
    """Refuse an idea, permanently.

    The row is kept rather than deleted. "I said no to this" is a reason not to
    propose it again, and a delete forgets that — the adjacency generator would
    cheerfully re-derive it from the same published history next week.
    """
    from engine import repository

    if not await repository.resolve_backlog_idea(idea_id=idea_id, status="dismissed"):
        raise HTTPException(404, f"no open idea {idea_id}")


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
        candidates, _model, _prompt = await _propose(recent)
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


async def _propose(recent: list[str]) -> tuple[list[str], str, str]:
    """Candidate topics, plus the model and prompt that produced them.

    The provenance used to go in the bin — `result, _ = await model.json(...)`.
    That was fine while the ideas evaporated after thirty minutes and became a
    CLAUDE.md #2 violation the moment they were written to a table: every generated
    artifact records what produced it, throwaway ones included.
    """
    made = "\n".join(f"- {t}" for t in recent)
    model = llm.for_task("backlog")
    result, completion = await model.json(
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
    return topics[:_CANDIDATES], completion.model, completion.prompt


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

    # FIX-TASKS E3. `published[0]` (the channel's most recent topic) stands in for
    # a niche seed here — this endpoint scores ad-hoc candidates for whatever
    # channel is already running, not a channel being founded, so there is no
    # separate "niche" input the way `channel_launch.py`'s BacklogStage has one.
    trending_terms = await trending.gather_trending_terms(
        youtube_client=_default_youtube_client(),
        seed=published[0] if published else "",
    )

    ideas = await build_backlog_async(
        candidates,
        published_topics=published,
        suggestions=pooled,
        trending_terms=trending_terms,
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


def _default_youtube_client() -> YouTube | None:
    """The connected channel's client, or `None` with nothing connected yet.

    Mirrors `api/channels.py`'s `CHANNELS.get("default")` lookup. The only use
    here is the free `videos.list chart=mostPopular` trending poll in `_score` —
    with no channel connected that just means the YouTube-trending half of
    `trending_terms` stays empty, the same honest-empty contract every other
    optional signal in this module already has.
    """
    creds = CHANNELS.get("default")
    return YouTube(creds) if creds else None


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
