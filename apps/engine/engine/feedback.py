"""Closing the loop.

Confirmed findings become guidance injected into the prompts that generate the next
video. This is the point of the whole analytics phase: the dashboard is not a report,
it changes what the generator does.

Two rules keep this from degrading the system:

  1. **Only `confirmed` findings are used.** Suggestive ones are shown to the user and
     ignored by the generator. See `insights.py` for what that gate costs.
  2. **Guidance is a nudge, never a rule.** The prompt is told which patterns have
     performed, and explicitly told not to abandon a better idea to match them.
     Without that, a channel converges on one title shape and stops improving —
     exploitation with no exploration.
"""

from __future__ import annotations

from engine.insights import InsightReport, Verdict

# Findings older than this stop being applied. Audiences and the recommender both
# drift; a pattern confirmed a year ago is not evidence about today.
MAX_FINDING_AGE_DAYS = 120

DIMENSION_TARGETS = {
    "title_strategy": "titles",
    "hook_device": "hook",
    "thumbnail_concept": "thumbnail",
    "script_model": None,  # informational — never fed back as instruction
}


def guidance_for(report: InsightReport, stage: str) -> str:
    """Prompt guidance for a stage, or an empty string when there is nothing to say.

    An empty string is the common case early on, and it is the correct one — a
    channel with nine videos has nothing to teach its own generator yet.
    """
    relevant = [
        finding
        for finding in report.findings
        if finding.verdict is Verdict.CONFIRMED
        and DIMENSION_TARGETS.get(finding.dimension) == stage
    ]
    if not relevant:
        return ""

    lines = [
        "",
        "What has actually performed on this channel:",
    ]
    for finding in relevant:
        lines.append(
            f"- {finding.winner} outperforms {finding.loser} on "
            f"{finding.metric} by {abs(finding.lift):.0f}% "
            f"(n={finding.comparison.a.n} vs {finding.comparison.b.n}, "
            f"p={finding.comparison.p_value:.3f})"
        )
    lines += [
        "",
        "Weight these patterns, but do not force them. If a different approach is "
        "genuinely stronger for this specific topic, use it and say why — a channel "
        "that only repeats what already worked stops finding what works better.",
    ]
    return "\n".join(lines)


def retention_guidance(beat_map: list[dict]) -> str:
    """Turn a retention drop-off into a concrete instruction for the next script."""
    if not beat_map:
        return ""
    worst = next((b for b in beat_map if b.get("worst")), None)
    if not worst or worst["drop"] < 5:
        return ""
    return (
        f"\nOn the last video, retention fell {worst['drop']:.0f} points during the "
        f"“{worst['label']}” beat. Whatever that beat did — stalling, stating a "
        f"figure without setting up why it matters, over-explaining — do not repeat "
        f"it here."
    )
