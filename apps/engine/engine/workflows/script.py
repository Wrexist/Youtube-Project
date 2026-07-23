"""The script workflow.

MoneyPrinterTurbo generates a script in one LLM call, which is precisely why its
output is generic. This is the replacement: seven stages, each separately inspectable
and re-runnable from the Create screen.

    research → angle → hook → beats → draft → critique → revision

The two stages that carry most of the quality are `hook` (the first three seconds
decide whether the video gets recommended at all) and `critique` (a cold second pass
is the single largest quality delta in the chain).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine import feedback
from engine.providers import llm
from engine.research import web
from engine.workflows.base import Provenance, Stage, StageOutput, WorkflowContext

# Phrases that are pure retention leak. Every one of them is standard LLM output,
# so they get prompted against explicitly and then checked for after the fact.
BANNED_OPENERS = (
    "in this video",
    "in today's video",
    "welcome back",
    "hey guys",
    "let's dive in",
    "but first",
    "before we get started",
    "make sure to subscribe",
)


@dataclass
class Beat:
    purpose: str
    text_direction: str
    visual_direction: str  # drives per-beat material matching in the render
    energy: str  # high | medium | low — drives clip pacing
    est_seconds: float


@dataclass
class Script:
    hook: str
    body: str
    beats: list[Beat] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f"{self.hook}\n\n{self.body}"

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())

    def summary(self) -> str:
        secs = int(self.word_count / 2.5)  # refined by the real TTS rate downstream
        return f"{self.word_count:,} words · ~{secs // 60}:{secs % 60:02d}"


# ── stages ──────────────────────────────────────────────────────────────────


class ResearchStage(Stage[dict]):
    name = "research"
    title = "Research"
    estimated_cost_usd = 0.15
    timeout_s = 240.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        topic = ctx.inputs["topic"]
        await ctx.progress("searching")

        findings = await web.research(topic, max_sources=8)
        if not findings["sources"]:
            # A script with no sources is exactly the "inauthentic content" YouTube
            # demonetises. Failing here is the correct behaviour, not a nuisance.
            raise RuntimeError(
                "no usable sources found — refusing to generate an ungrounded script"
            )

        await ctx.progress("extracting facts", 0.6)
        model = llm.for_task("research")
        facts, completion = await model.json(
            f"""Topic: {topic}

Source material:
{findings["digest"]}

Extract the specific, checkable facts a video on this topic should be built from.
Prefer numbers, dates, names, studies, and direct quotes over general statements.
Discard anything you could have written without reading the sources.

Return: {{"facts": [{{"claim": str, "detail": str, "source_url": str}}],
          "surprising": [str], "common_misconception": str}}""",
            max_tokens=3000,
        )

        value = {**facts, "sources": findings["sources"]}
        return StageOutput(
            value=value,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                sources=findings["sources"],
            ),
        )


class AngleStage(Stage[dict]):
    name = "angle"
    title = "Angle"
    depends_on = ("research",)
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        topic = ctx.inputs["topic"]
        research = ctx.get("research")
        model = llm.for_task("angle")

        result, completion = await model.json(
            f"""Topic: {topic}

Research findings:
{_render_facts(research)}

Generate 3 genuinely different angles on this topic, then pick the strongest.

An angle is the tension the video is built around — not a subtopic. A generic angle
produces an unwatchable video no matter how good the production is. The strongest
angle is usually the one that contradicts what the audience already believes, or the
one that no existing video on this topic takes.

Return: {{"options": [{{"angle": str, "tension": str, "why_it_works": str}}],
          "chosen": int, "reasoning": str}}""",
            max_tokens=2000,
        )
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class HookStage(Stage[dict]):
    """The highest-leverage 30 seconds of work in the entire system."""

    name = "hook"
    title = "Hook"
    depends_on = ("angle",)
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        angle = ctx.get("angle")
        chosen = angle["options"][angle["chosen"]]
        fmt = ctx.inputs.get("format", "short")
        model = llm.for_task("hook")

        # Confirmed findings from this channel's own performance, if there are any.
        # Empty for a new channel, which is correct — nine videos teach nothing.
        learned = (
            feedback.guidance_for(ctx.inputs["insights"], "hook")
            if ctx.inputs.get("insights")
            else ""
        )
        learned += feedback.retention_guidance(ctx.inputs.get("last_retention_map", []))

        result, completion = await model.json(
            f"""Angle: {chosen["angle"]}
Tension: {chosen["tension"]}
Format: {"short (under 60s)" if fmt == "short" else "long-form (8-12 min)"}

Write 3 hook variants. A hook is the first 1-2 sentences of the video and it has
about 3 seconds to work.

A hook must do exactly one of:
  - open a loop the viewer needs closed
  - state something that contradicts what they believe
  - show the payoff up front and promise the how

A hook must NOT: introduce the channel, say "in this video", greet the viewer, or
explain what is coming. Those are retention leaks.

For each variant report `time_to_tension` — how many words pass before the
interesting thing arrives. Lower is better; above 12 is usually fatal.
{learned}
Return: {{"variants": [{{"text": str, "device": str, "time_to_tension": int,
                        "promise": str}}], "chosen": int}}""",
            max_tokens=1500,
        )

        # Cheap deterministic check the model can't talk its way out of.
        for variant in result["variants"]:
            lowered = variant["text"].lower()
            variant["banned_phrases"] = [p for p in BANNED_OPENERS if p in lowered]

        clean = [i for i, v in enumerate(result["variants"]) if not v["banned_phrases"]]
        if clean and result["chosen"] not in clean:
            result["chosen"] = clean[0]

        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class BeatsStage(Stage[list]):
    name = "beats"
    title = "Structure"
    depends_on = ("hook", "research")
    estimated_cost_usd = 0.08

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        hook = ctx.get("hook")
        research = ctx.get("research")
        angle = ctx.get("angle")
        fmt = ctx.inputs.get("format", "short")
        target = 45 if fmt == "short" else ctx.inputs.get("target_seconds", 600)
        model = llm.for_task("beats")

        result, completion = await model.json(
            f"""Hook: {hook["variants"][hook["chosen"]]["text"]}
Angle: {angle["options"][angle["chosen"]]["angle"]}
Facts available:
{_render_facts(research)}

Target runtime: {target} seconds.

Break the body into beats. {
                "4-6 beats."
                if fmt == "short"
                else "12-20 beats, grouped into 5-8 chapters. Include a retention "
                "device around the 40% mark, where drop-off concentrates."
            }

Every beat needs a `visual_direction`: what is literally on screen. This drives
footage selection, and it is the difference between a video that looks intentional
and one that looks like random stock clips. Be concrete — "close-up of hands
counting cash" not "money imagery".

Do not hold energy constant. Alternate it; the renderer uses `energy` to set clip
pacing.

Return: {{"beats": [{{"purpose": str, "text_direction": str, "visual_direction": str,
                     "energy": "high"|"medium"|"low", "est_seconds": float}}]{
                ', "chapters": [{"title": str, "beat_indexes": [int]}]' if fmt == "long" else ""
            }}}""",
            max_tokens=4000,
        )

        beats = [Beat(**b) for b in result["beats"]]
        return StageOutput(
            value=beats,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                params={"chapters": result.get("chapters", []), "target_seconds": target},
            ),
        )


class DraftStage(Stage[Script]):
    name = "draft"
    title = "Draft"
    depends_on = ("beats",)
    estimated_cost_usd = 0.20
    timeout_s = 300.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[Script]:
        beats: list[Beat] = ctx.get("beats")
        hook = ctx.get("hook")
        research = ctx.get("research")
        model = llm.for_task("draft")

        result, completion = await model.json(
            f"""Write the narration from these beats.

Hook (use verbatim, it is already written):
{hook["variants"][hook["chosen"]]["text"]}

Beats:
{_render_beats(beats)}

Rules:
- Second person. Short sentences. One idea per sentence — the text-to-speech has no
  way to convey nested clauses.
- Numbers and specifics instead of adjectives. "Grew 340% in eight months" beats
  "grew dramatically".
- Re-open a loop every 30-45 seconds. Explicit turns: "but here's the problem".
- No "in conclusion", no summarising what was just said, no sign-off past one line.
- Expand acronyms and symbols — anything the speech synthesiser would mangle.
- Use the researched facts. A sentence you could have written without the research
  is a sentence to cut.

Facts:
{_render_facts(research)}

Return: {{"body": str}}""",
            max_tokens=8000,
        )

        script = Script(
            hook=hook["variants"][hook["chosen"]]["text"],
            body=result["body"],
            beats=beats,
            sources=research["sources"],
        )
        return StageOutput(
            value=script,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model, prompt=completion.prompt, sources=script.sources
            ),
        )


class CritiqueStage(Stage[dict]):
    """A cold read of the draft. The largest quality delta in the chain."""

    name = "critique"
    title = "Critique"
    depends_on = ("draft",)
    estimated_cost_usd = 0.10

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        script: Script = ctx.get("draft")
        model = llm.for_task("critique")

        result, completion = await model.json(
            f"""Read this script as a viewer who has not seen it before, and who will
leave the moment it stops being worth their time.

{script.full_text}

Answer honestly. A critique that finds nothing is a critique that did not read.

1. Where does attention drop? Quote the exact sentence.
2. Which sentences are filler — true but unnecessary?
3. Does the hook's promise actually get paid? Where?
4. Which claims are vague where a number belongs?
5. What would you cut if you had to lose 20%?

Return: {{"attention_drops": [{{"sentence": str, "why": str}}],
          "filler": [str], "promise_paid": bool, "promise_note": str,
          "vague_claims": [str], "cut_suggestions": [str], "severity": 1-5}}""",
            max_tokens=3000,
        )
        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class RevisionStage(Stage[Script]):
    name = "revision"
    title = "Script"
    depends_on = ("draft", "critique")
    estimated_cost_usd = 0.20
    timeout_s = 300.0

    def should_skip(self, ctx: WorkflowContext) -> bool:
        # A clean draft doesn't need a rewrite, and rewriting it usually makes it
        # blander. Only revise when the critique found something real.
        critique = ctx.try_get("critique", {})
        return bool(critique) and critique.get("severity", 5) <= 2

    async def run(self, ctx: WorkflowContext) -> StageOutput[Script]:
        script: Script = ctx.get("draft")
        critique = ctx.get("critique")
        model = llm.for_task("revision")

        result, completion = await model.json(
            f"""Revise this script using the critique. Fix what was identified; do not
rewrite what was not. Preserve the hook verbatim. Keep the word count within 10% of
the original — this is a repair, not a rewrite.

Script:
{script.full_text}

Critique:
{critique}

Return: {{"body": str, "changes": [str]}}""",
            max_tokens=8000,
        )

        revised = Script(
            hook=script.hook,
            body=result["body"],
            beats=script.beats,
            sources=script.sources,
        )
        return StageOutput(
            value=revised,
            cost_usd=completion.cost_usd,
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                sources=revised.sources,
                params={"changes": result.get("changes", [])},
            ),
        )


# ── helpers ─────────────────────────────────────────────────────────────────


def _render_facts(research: dict[str, Any]) -> str:
    lines = [f"- {f['claim']} ({f.get('detail', '')})" for f in research.get("facts", [])]
    if research.get("surprising"):
        lines.append("Surprising: " + "; ".join(research["surprising"]))
    if research.get("common_misconception"):
        lines.append("Misconception: " + research["common_misconception"])
    return "\n".join(lines)


def _render_beats(beats: list[Beat]) -> str:
    return "\n".join(
        f"{i + 1}. [{b.energy}, ~{b.est_seconds:.0f}s] {b.purpose}\n"
        f"   say: {b.text_direction}\n   show: {b.visual_direction}"
        for i, b in enumerate(beats)
    )


SCRIPT_STAGES: list[Stage] = [
    ResearchStage(),
    AngleStage(),
    HookStage(),
    BeatsStage(),
    DraftStage(),
    CritiqueStage(),
    RevisionStage(),
]
