"""The SEO workflow.

Metadata decides whether a video is seen; production quality decides whether it is
finished. This chain gets more care than the render pipeline for that reason.

    grounding → titles → description → tags → chapters

The hard rule: no stage here writes copy before `grounding` has produced real search
evidence. An ungrounded SEO package fails the job rather than shipping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine import feedback
from engine.providers import llm
from engine.research import keywords
from engine.workflows.base import Provenance, Stage, StageOutput, WorkflowContext

# YouTube's actual limits. Enforced here so a package can never fail at upload time.
TITLE_HARD_MAX = 100
TITLE_TARGET_MAX = 60  # beyond this it truncates in search and suggested
DESCRIPTION_MAX = 5000
DESCRIPTION_VISIBLE = 150  # all most viewers ever read
TAGS_TOTAL_MAX = 500

STRATEGIES = (
    "curiosity_gap",
    "number_list",
    "contrarian",
    "outcome",
    "question",
    "warning",
    "authority_specific",
    "story",
)


@dataclass
class TitleVariant:
    text: str
    strategy: str
    score: float = 0.0
    reasons: dict[str, float] = field(default_factory=dict)
    notes: str = ""


@dataclass
class SeoPackage:
    titles: list[TitleVariant]
    chosen_title: int
    description: str
    tags: list[str]
    chapters: list[tuple[str, str]] = field(default_factory=list)
    keyword_sources: list[str] = field(default_factory=list)
    primary_keyword: str = ""
    competitor_gap: str = ""

    def summary(self) -> str:
        return f"{self.titles[self.chosen_title].text[:50]}… · {len(self.tags)} tags"


# ── scoring ─────────────────────────────────────────────────────────────────


def score_title(text: str, keyword: str) -> tuple[float, dict[str, float]]:
    """Deterministic title scoring.

    The model proposes; this decides. Keeping the scoring in code rather than in the
    prompt means it's testable, consistent across runs, and can be tuned against the
    CTR data the analytics loop collects.
    """
    reasons: dict[str, float] = {}
    length = len(text)

    if length > TITLE_HARD_MAX:
        reasons["length"] = -100.0  # unusable, will be rejected by the API
    elif length <= TITLE_TARGET_MAX:
        reasons["length"] = 1.0
    else:
        # Linear decay from 60 to 100 chars — truncation gets worse, not binary.
        reasons["length"] = max(0.0, 1.0 - (length - TITLE_TARGET_MAX) / 40)

    lowered, kw = text.lower(), keyword.lower()
    if kw and kw in lowered:
        position = lowered.index(kw) / max(len(lowered), 1)
        reasons["keyword_position"] = 1.0 if position < 0.4 else 0.5
    else:
        reasons["keyword_position"] = 0.0

    # Mobile truncates hardest, so the first three words carry the click.
    opening = " ".join(text.split()[:3]).lower()
    weak_openings = ("the", "a", "an", "how to", "this is", "why i", "my")
    reasons["front_loading"] = 0.4 if opening.startswith(weak_openings) else 1.0

    # A number, a proper noun, or a concrete year beats any adjective.
    has_number = bool(re.search(r"\d", text))
    has_proper = bool(re.search(r"\b[A-Z][a-z]{2,}", text[1:]))
    reasons["specificity"] = min(1.0, 0.5 * has_number + 0.5 * has_proper + 0.2)

    reasons["no_clickbait_markers"] = 0.6 if text.count("!") or "😱" in text else 1.0

    weights = {
        "length": 0.25,
        "keyword_position": 0.25,
        "front_loading": 0.20,
        "specificity": 0.20,
        "no_clickbait_markers": 0.10,
    }
    total = sum(reasons[k] * w for k, w in weights.items())
    return round(total, 3), reasons


def validate_tags(
    tags: list[str],
    *,
    exact_title: str | None = None,
    suggestions: list[str] | None = None,
) -> list[str]:
    """Trim to the 500-character total budget, keeping the highest-value tags.

    ``exact_title`` is pinned at the front unconditionally — YouTube weights
    the exact-match title tag most heavily and it must survive the budget cut.

    When ``suggestions`` are provided the remaining tags are ranked by position
    in the autocomplete list (lower index = higher search volume signal).  Tags
    absent from the autocomplete data are sorted to the end rather than
    discarded, preserving any head terms the model generated.

    Fills greedily: a tag that would exceed the budget is skipped so that
    shorter high-value terms aren't lost because of one long tag.
    """
    title_norm = exact_title.lower().strip() if exact_title else None

    # Separate the exact-title tag so it is always placed first.
    pinned: list[str] = []
    rest: list[str] = []
    for tag in tags:
        if title_norm and tag.lower().strip() == title_norm:
            pinned = [tag]
        else:
            rest.append(tag)

    if suggestions:
        suggestion_rank: dict[str, int] = {s.lower(): i for i, s in enumerate(suggestions)}

        def _tag_rank(tag: str) -> int:
            lower = tag.lower()
            if lower in suggestion_rank:
                return suggestion_rank[lower]
            tag_words = set(lower.split())
            for phrase, rank in suggestion_rank.items():
                phrase_words = set(phrase.split())
                if tag_words <= phrase_words or phrase_words <= tag_words:
                    return rank
            return len(suggestions)

        rest = sorted(rest, key=_tag_rank)

    out: list[str] = []
    used = 0
    for tag in pinned + rest:
        cost = len(tag) + 1  # comma separator
        if used + cost <= TAGS_TOTAL_MAX:
            out.append(tag)
            used += cost
    return out


# ── stages ──────────────────────────────────────────────────────────────────


class GroundingStage(Stage[keywords.KeywordEvidence]):
    name = "grounding"
    title = "Keyword research"
    estimated_cost_usd = 0.02
    timeout_s = 60.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[keywords.KeywordEvidence]:
        topic = ctx.inputs["topic"]
        await ctx.progress("querying autocomplete")

        evidence = await keywords.gather(topic, youtube_client=ctx.inputs.get("youtube_client"))
        if not evidence.is_grounded:
            # The refusal is policy (CLAUDE.md: no ungrounded SEO copy). The
            # diagnosis is so the operator knows which of network, rate-limit or
            # topic to fix — this is the first stage of the only workflow.
            raise RuntimeError(f"{evidence.diagnosis()} Refusing to write ungrounded SEO copy.")

        return StageOutput(
            value=evidence,
            provenance=Provenance(
                sources=evidence.sources,
                params={"suggestion_count": len(evidence.suggestions)},
            ),
        )


class TitlesStage(Stage[list]):
    name = "titles"
    title = "Titles"
    depends_on = ("grounding",)
    estimated_cost_usd = 0.06

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        evidence: keywords.KeywordEvidence = ctx.get("grounding")
        script = ctx.try_get("revision") or ctx.try_get("draft")
        model = llm.for_task("titles")

        competitor_block = (
            "\n".join(f"- {c['title']}" for c in evidence.competitor_titles[:20])
            or "(none retrieved — rely on the search queries above)"
        )

        # Only confirmed findings reach the prompt; suggestive ones are shown to the
        # user and withheld from the generator.
        learned = (
            feedback.guidance_for(ctx.inputs["insights"], "titles")
            if ctx.inputs.get("insights")
            else ""
        )

        result, completion = await model.json(
            f"""Topic: {evidence.seed}

Real search queries people type (from YouTube autocomplete):
{chr(10).join("- " + s for s in evidence.suggestions[:40])}

Titles currently ranking for this topic:
{competitor_block}

Script hook (the title must be deliverable by this — an overpromise costs retention,
and retention outranks click-through):
{getattr(script, "hook", "(script not yet written)")}

Write exactly 8 titles, one per strategy: {", ".join(STRATEGIES)}.
Eight rewordings of one idea is a failed response — these must be genuinely different
approaches.

Then identify the primary keyword (the query with the best volume-to-competition
trade-off from the list above) and the gap: what are all the ranking titles doing that
you are deliberately not doing?
{learned}
Return: {{"titles": [{{"text": str, "strategy": str}}],
          "primary_keyword": str, "competitor_gap": str}}""",
            max_tokens=2500,
        )

        variants = []
        for item in result["titles"]:
            score, reasons = score_title(item["text"], result["primary_keyword"])
            variants.append(
                TitleVariant(
                    text=item["text"],
                    strategy=item["strategy"],
                    score=score,
                    reasons=reasons,
                    notes=f"{len(item['text'])} chars",
                )
            )
        variants.sort(key=lambda v: v.score, reverse=True)

        return StageOutput(
            value=variants,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                sources=evidence.sources,
                params={
                    "primary_keyword": result["primary_keyword"],
                    "competitor_gap": result["competitor_gap"],
                },
            ),
        )


class DescriptionStage(Stage[str]):
    name = "description"
    title = "Description"
    depends_on = ("titles", "grounding")
    estimated_cost_usd = 0.06

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        variants: list[TitleVariant] = ctx.get("titles")
        evidence: keywords.KeywordEvidence = ctx.get("grounding")
        script = ctx.try_get("revision") or ctx.try_get("draft")
        model = llm.for_task("description")

        result, completion = await model.json(
            f"""Title: {variants[0].text}
Script:
{getattr(script, "full_text", "(not available)")[:4000]}

Search queries to weave in naturally:
{chr(10).join("- " + s for s in evidence.suggestions[:25])}

Write the description in three parts:

1. `hook` — at most {DESCRIPTION_VISIBLE} characters. This is the only part most
   people see, in search results and above the fold. Restate the promise and include
   the primary keyword naturally.
2. `body` — 200-400 words expanding the topic, with secondary keywords as prose.
   YouTube reads this to classify the video. A keyword list here is actively
   penalised, so write actual sentences.
3. `hashtags` — exactly 3. The first three appear above the title.

Return: {{"hook": str, "body": str, "hashtags": [str]}}""",
            max_tokens=2500,
        )

        sources = getattr(script, "sources", [])
        parts = [result["hook"], "", result["body"]]
        if sources:
            parts += ["", "Sources:", *[f"— {url}" for url in sources[:8]]]
        parts += ["", " ".join(f"#{h.lstrip('#')}" for h in result["hashtags"][:3])]

        description = "\n".join(parts)[:DESCRIPTION_MAX]
        return StageOutput(
            value=description,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                sources=sources,
                params={"visible_chars": len(result["hook"])},
            ),
        )


class TagsStage(Stage[list]):
    name = "tags"
    title = "Tags"
    depends_on = ("grounding", "titles")
    estimated_cost_usd = 0.01

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        evidence: keywords.KeywordEvidence = ctx.get("grounding")
        variants: list[TitleVariant] = ctx.get("titles")
        # Tags are a weak ranking signal now — the fast model is the right spend here.
        model = llm.for_task("tags")

        result, completion = await model.json(
            f"""Topic: {evidence.seed}
Real search queries:
{chr(10).join("- " + s for s in evidence.suggestions[:40])}

Produce 22 tags: 3 head terms, 10 mid-tail, 9 long-tail phrases taken from the actual
queries above. Include the exact title as one tag.

Title: {variants[0].text}

Return: {{"tags": [str]}}""",
            max_tokens=1200,
        )

        tags = validate_tags(
            [t.strip() for t in result["tags"] if t.strip()],
            exact_title=variants[0].text,
            suggestions=evidence.suggestions,
        )
        return StageOutput(
            value=tags,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                params={"total_chars": sum(len(t) + 1 for t in tags)},
            ),
        )


class ChaptersStage(Stage[list]):
    """Chapters come from the *rendered* subtitle timings, never from estimates.

    Estimated timings drift by several seconds over a 10-minute video, which puts
    every chapter marker in the wrong place. This stage is optional precisely because
    it cannot run until the render has produced a real subtitle file.
    """

    name = "chapters"
    title = "Chapters"
    depends_on = ("titles",)
    optional = True
    estimated_cost_usd = 0.01

    def should_skip(self, ctx: WorkflowContext) -> bool:
        return ctx.try_get("subtitles") is None

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        cues = ctx.get("subtitles")
        beats = ctx.try_get("beats", [])
        model = llm.for_task("chapters")

        result, completion = await model.json(
            f"""Subtitle cues with real timings:
{chr(10).join(f"{c['start']:.1f}s {c['text']}" for c in cues[:400])}

Beat structure:
{chr(10).join(f"- {b.purpose}" for b in beats)}

Produce YouTube chapters. The first must start at 0:00, there must be at least 3, and
each must be at least 10 seconds long. Snap each start to the nearest cue boundary —
never invent a timestamp.

Return: {{"chapters": [{{"time": "M:SS", "title": str}}]}}""",
            max_tokens=1500,
        )

        chapters = [(c["time"], c["title"]) for c in result["chapters"]]
        if chapters and chapters[0][0] not in ("0:00", "00:00"):
            chapters[0] = ("0:00", chapters[0][1])

        return StageOutput(
            value=chapters,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


SEO_STAGES: list[Stage] = [
    GroundingStage(),
    TitlesStage(),
    DescriptionStage(),
    TagsStage(),
    ChaptersStage(),
]
