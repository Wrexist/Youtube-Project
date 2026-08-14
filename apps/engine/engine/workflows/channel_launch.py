"""The channel launch workflow.

One input — a niche, in plain words — produces a complete channel identity: name
candidates, a handle, the About text, keywords, visual direction, the series
configuration, and a first backlog of de-duplicated video ideas.

    grounding → positioning → naming → about → visuals → series → backlog

Grounded the same way everything else here is: names and keywords are chosen against
real search queries and the channels already ranking in the niche, not invented.
"""

from __future__ import annotations

from engine import channel as ch
from engine import trending
from engine.ideas import build_backlog_async
from engine.providers import llm
from engine.providers.llm import DEFAULT_OLLAMA_URL
from engine.research import keywords as kw
from engine.workflows.base import Provenance, Stage, StageOutput, WorkflowContext


class NicheResearchStage(Stage[kw.KeywordEvidence]):
    name = "grounding"
    title = "Niche research"
    estimated_cost_usd = 0.02
    timeout_s = 90.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[kw.KeywordEvidence]:
        niche = ctx.inputs["niche"]
        await ctx.progress("querying autocomplete")

        evidence = await kw.gather(niche, youtube_client=ctx.inputs.get("youtube_client"))
        if not evidence.is_grounded:
            raise RuntimeError(
                "no search evidence for this niche — refusing to design a channel around a guess"
            )
        return StageOutput(
            value=evidence,
            provenance=Provenance(
                sources=evidence.sources,
                params={"queries": len(evidence.suggestions)},
            ),
        )


class PositioningStage(Stage[dict]):
    name = "positioning"
    title = "Positioning"
    depends_on = ("grounding",)
    estimated_cost_usd = 0.06

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        evidence: kw.KeywordEvidence = ctx.get("grounding")
        model = llm.for_task("positioning")

        competitors = (
            "\n".join(f"- {c['title']} ({c['channel']})" for c in evidence.competitor_titles[:20])
            or "(none retrieved — work from the queries alone)"
        )

        result, completion = await model.json(
            f"""Niche: {evidence.seed}

What people actually search for here:
{chr(10).join("- " + s for s in evidence.suggestions[:40])}

What already ranks:
{competitors}

Define the position for a new channel entering this niche.

A new channel cannot win the broad term. It wins a specific slice and expands from
there. Name the slice precisely enough that someone could tell whether a given video
belongs on this channel or not.

Return: {{"audience": str, "slice": str, "promise": str,
          "why_this_gap_exists": str, "adjacent_expansion": [str],
          "saturation": "low"|"medium"|"high",
          "honest_assessment": str}}""",
            max_tokens=2000,
        )
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class NamingStage(Stage[dict]):
    name = "naming"
    title = "Name and handle"
    depends_on = ("positioning",)
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        position = ctx.get("positioning")
        evidence: kw.KeywordEvidence = ctx.get("grounding")
        model = llm.for_task("naming")

        result, completion = await model.json(
            f"""Slice: {position["slice"]}
Audience: {position["audience"]}
Promise: {position["promise"]}

Propose 6 channel names.

Rules:
- Sayable out loud without spelling it. If it needs explaining, it is wrong.
- Not a keyword string. "Bridge Engineering Facts Daily" reads as spam.
- Room to grow into {", ".join(position.get("adjacent_expansion", [])[:3]) or "adjacent topics"}.
- Two to three words, under 30 characters, so the handle survives.
- Avoid anything that could read as an existing brand.

For each, give the handle you would claim (letters, numbers, periods, underscores or
hyphens only) and one line on what it signals.

Return: {{"names": [{{"name": str, "handle": str, "signals": str,
                     "risk": str}}], "recommended": int}}""",
            max_tokens=2000,
        )

        # Normalise and validate every candidate rather than trusting the model to
        # have followed the handle rules.
        for candidate in result["names"]:
            candidate["handle"] = ch.normalize_handle(candidate["handle"])
            probe = ch.ChannelIdentity(
                name=candidate["name"], handle=candidate["handle"], niche=evidence.seed
            )
            candidate["problems"] = [
                {"field": p.field, "message": p.message, "fatal": p.fatal}
                for p in ch.validate(probe)
                if p.field in ("name", "handle")
            ]

        clean = [
            i for i, c in enumerate(result["names"]) if not any(p["fatal"] for p in c["problems"])
        ]
        if clean and result["recommended"] not in clean:
            result["recommended"] = clean[0]

        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class AboutStage(Stage[dict]):
    name = "about"
    title = "About and keywords"
    depends_on = ("naming", "positioning", "grounding")
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        naming = ctx.get("naming")
        position = ctx.get("positioning")
        evidence: kw.KeywordEvidence = ctx.get("grounding")
        chosen = naming["names"][naming["recommended"]]
        model = llm.for_task("about")

        result, completion = await model.json(
            f"""Channel: {chosen["name"]}
Slice: {position["slice"]}
Audience: {position["audience"]}

Real search queries in this niche:
{chr(10).join("- " + s for s in evidence.suggestions[:30])}

Write:

1. `tagline` — under 60 characters. What this channel is, said once.
2. `description` — the About text, 400-900 characters. First sentence states plainly
   what the channel covers and for whom; it is both a ranking signal and the first
   thing a potential subscriber reads. Weave the search language in as prose, never
   as a list. No "welcome to my channel".
3. `keywords` — 12-18 channel keywords drawn from the queries above. Broad terms
   first, then specific. Total under 500 characters including quotes.

Return: {{"tagline": str, "description": str, "keywords": [str]}}""",
            max_tokens=2000,
        )

        result["keywords"] = ch.trim_keywords(result["keywords"], suggestions=evidence.suggestions)
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                sources=evidence.sources,
            ),
        )


class VisualsStage(Stage[dict]):
    name = "visuals"
    title = "Visual identity"
    depends_on = ("naming", "positioning")
    estimated_cost_usd = 0.04

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        naming = ctx.get("naming")
        position = ctx.get("positioning")
        chosen = naming["names"][naming["recommended"]]
        model = llm.for_task("visuals")

        result, completion = await model.json(
            f"""Channel: {chosen["name"]} — {position["slice"]}

Design the visual identity.

The avatar renders as a 98-pixel circle in most places it appears. Anything with
detail, or more than two or three letters, is invisible there — design for that size
first.

The banner is {ch.BANNER_SIZE[0]}×{ch.BANNER_SIZE[1]}, but only the centre
{ch.BANNER_SAFE_AREA[0]}×{ch.BANNER_SAFE_AREA[1]} survives on every device. All text
and logo must sit inside it.

Also define the thumbnail signature: the one consistent element that makes this
channel's videos recognisable in a feed before the title is read.

Return: {{"avatar_concept": str, "banner_concept": str,
          "palette": [str], "thumbnail_signature": str, "typography": str}}""",
            max_tokens=1500,
        )
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class SeriesPlanStage(Stage[dict]):
    name = "series"
    title = "Series plan"
    depends_on = ("positioning",)
    estimated_cost_usd = 0.04

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        position = ctx.get("positioning")
        model = llm.for_task("series")

        result, completion = await model.json(
            f"""Slice: {position["slice"]}
Audience: {position["audience"]}

Define 2-3 repeatable series for this channel. A series is a format, not a topic —
something that can carry fifty videos without becoming a different channel.

For each: name, format (short or long), what every episode has in common, and a
sustainable weekly cadence. Be conservative on cadence; a channel that misses its own
schedule is worse than one with a slower honest one.

Return: {{"series": [{{"name": str, "format": "short"|"long", "pattern": str,
                      "per_week": int, "example_titles": [str]}}]}}""",
            max_tokens=2000,
        )
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class BacklogStage(Stage[list]):
    name = "backlog"
    title = "First 30 ideas"
    depends_on = ("series", "positioning", "grounding")
    estimated_cost_usd = 0.08

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        series = ctx.get("series")
        position = ctx.get("positioning")
        evidence: kw.KeywordEvidence = ctx.get("grounding")
        model = llm.for_task("backlog")

        # Built outside the f-string: nested same-quotes are 3.12+ only and this
        # project targets 3.11.
        series_lines = chr(10).join(
            "- {}: {}".format(s["name"], s["pattern"]) for s in series["series"]
        )

        result, completion = await model.json(
            f"""Slice: {position["slice"]}
Series: {series_lines}

Real search queries:
{chr(10).join("- " + s for s in evidence.suggestions[:40])}

Propose 30 specific video topics. Each must be a concrete, answerable question or
claim — not a category. "Why the Tacoma Narrows bridge twisted itself apart" is a
topic; "bridge failures" is a category.

Order them so the first five are the strongest, because a new channel is judged on
its opening run.

Return: {{"topics": [str]}}""",
            max_tokens=3000,
        )

        # FIX-TASKS E3: the same niche seed and (if the launch already has one) the
        # same YouTube client the grounding stage used, so a brand-new channel's
        # first thirty ideas are not scored with freshness pinned at zero just
        # because nothing supplied `trending_terms` yet.
        trending_terms = await trending.gather_trending_terms(
            youtube_client=ctx.inputs.get("youtube_client"),
            seed=ctx.inputs["niche"],
        )

        # Runs through the same duplicate detection as every other backlog — a fresh
        # channel is exactly where thirty near-identical ideas would slip through.
        # Ollama embedding check is used when available; falls back to Jaccard-only.
        ideas = await build_backlog_async(
            result["topics"],
            published_topics=[],
            suggestions=evidence.suggestions,
            trending_terms=trending_terms,
            ollama_base_url=DEFAULT_OLLAMA_URL,
        )
        return StageOutput(
            value=ideas,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                params={
                    "rejected_duplicates": sum(1 for i in ideas if i.duplicate_of),
                    "trending_terms": len(trending_terms),
                },
            ),
        )


CHANNEL_LAUNCH_STAGES: list[Stage] = [
    NicheResearchStage(),
    PositioningStage(),
    NamingStage(),
    AboutStage(),
    VisualsStage(),
    SeriesPlanStage(),
    BacklogStage(),
]


def assemble(states: dict) -> ch.ChannelIdentity:
    """Collapse the workflow's outputs into one identity object."""

    def value(name):
        state = states.get(name)
        return state.output.value if state and state.output else None

    naming, about, visuals = value("naming"), value("about"), value("visuals")
    grounding = value("grounding")

    if not naming or not about:
        raise RuntimeError("naming and about must complete before assembling")

    chosen = naming["names"][naming["recommended"]]
    return ch.ChannelIdentity(
        name=chosen["name"],
        handle=chosen["handle"],
        tagline=about["tagline"],
        description=about["description"],
        keywords=about["keywords"],
        niche=grounding.seed if grounding else "",
        audience=(value("positioning") or {}).get("audience", ""),
        avatar_concept=(visuals or {}).get("avatar_concept", ""),
        banner_concept=(visuals or {}).get("banner_concept", ""),
        palette=(visuals or {}).get("palette", []),
    )
