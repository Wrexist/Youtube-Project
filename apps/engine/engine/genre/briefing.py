"""Genre evidence, formatted for the prompt chains.

The patterns module computes what the niche rewards; this module decides what
the generator is *told*. The two are separate because the prompt needs three
sentences of quotable fact, not a JSON report — and because "empty watchlist"
must degrade to an empty string rather than a paragraph of hedging.

Same gate as Phase 8's feedback loop, one notch lighter: feedback needs
statistical confirmation before it may change a prompt, while genre evidence
is observational (what competitors publish), not causal (what works for us).
It therefore reaches prompts immediately but is phrased as evidence about the
niche, never as instruction — the model still owns the decision.
"""

from __future__ import annotations

from typing import Any

from engine.genre import patterns

#: Cap the example list — three titles carry the shape without eating context.
_EXAMPLES = 3


def _corpus_line(report: dict[str, Any]) -> str | None:
    count = report.get("video_count") or 0
    if not count:
        return None
    return f"Genre evidence ({count} recent videos from watched competitor channels):"


def _strategy_lines(report: dict[str, Any], *, limit: int = 4) -> list[str]:
    """The hook-strategy table, strongest first, as single-line facts."""
    lines = []
    for entry in report.get("hook_patterns", [])[:limit]:
        views = entry.get("median_views_per_day")
        views_txt = f"{views:,.0f} views/day median" if views else "no velocity data yet"
        lines.append(
            f"- {entry['pattern']}-led titles: {entry['share']:.0%} of the corpus, {views_txt}"
        )
    return lines


def _shape_lines(report: dict[str, Any]) -> list[str]:
    """Runtime and cadence, when measurable."""
    lines = []
    duration = report.get("median_duration_s")
    if duration:
        minutes = duration / 60
        lines.append(f"- median runtime ≈ {minutes:.0f} min")
    cadence = report.get("uploads_per_week")
    if cadence is not None:
        lines.append(f"- competitors upload ≈ {cadence:g}×/week")
    return lines


def _example_lines(report: dict[str, Any]) -> list[str]:
    out = []
    for item in report.get("top_by_velocity", [])[:_EXAMPLES]:
        title = item.get("title", "")[:90]
        vpd = item.get("views_per_day")
        if title and vpd:
            out.append(f'- "{title}" ({vpd:,.0f} views/day)')
    return out


def _brief(report: dict[str, Any]) -> str:
    """The whole evidence block, or "" for an empty corpus."""
    header = _corpus_line(report)
    if not header:
        return ""
    lines = [header, *_strategy_lines(report), *_shape_lines(report)]
    examples = _example_lines(report)
    if examples:
        lines.append("- moving fastest right now:")
        lines.extend(examples)
    return "\n".join(lines)


async def hook_guidance() -> str:
    """Evidence block for the script chain's HookStage."""
    return _brief(await _report())


async def title_guidance() -> str:
    """Evidence block for the SEO chain's TitlesStage."""
    return _brief(await _report())


async def _report() -> dict[str, Any]:
    # Imported here per the package convention — keeps the module graph free of
    # repository cycles at load time.
    from engine import repository

    return patterns.analyze(await repository.watched_videos_for_mining())
