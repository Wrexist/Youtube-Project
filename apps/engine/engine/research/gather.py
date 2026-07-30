"""One entry point for "find me sources on this", whichever path can deliver.

Two paths exist and both have to: the model doing its own searching is better in every
way except that it needs a key and a provider that offers it, and the keyless scrape
chain works on a fresh clone with nothing configured. Which one runs is decided by the
model routed to the `research` task on the Models screen — so choosing a searching
model there is how an operator turns on real research, in the same place they choose
every other model.
"""

from __future__ import annotations

from loguru import logger

from engine.models import ModelSpec
from engine.providers.llm import ProviderUnavailable
from engine.research import agentic, web
from engine.research.agentic import Findings


async def find_sources(topic: str, spec: ModelSpec, *, max_sources: int = 8) -> Findings:
    """Sources and a digest for `topic`, by the best route the routed model allows.

    A search-capable model that fails does not fail the stage: scraping is tried
    afterwards, and if that comes up empty too, both reasons are reported. Diagnosing
    this from the pipeline row is the difference between an operator fixing their key
    and an operator rewriting a perfectly good topic.
    """
    reasons: list[str] = []

    if spec.supports_web_search:
        try:
            findings = await agentic.research(topic, spec, max_sources=max_sources)
            if findings.sources:
                return findings
            reasons.append(findings.problem or f"{spec.key()} found nothing")
        except ProviderUnavailable as exc:
            logger.warning("research: {} could not search ({}) — falling back", spec.key(), exc)
            reasons.append(str(exc))

    scraped = await web.research(topic, max_sources=max_sources)
    if scraped["sources"]:
        return Findings(
            digest=scraped["digest"],
            sources=scraped["sources"],
            via="web-scrape",
        )

    reasons.append(scraped["problem"] or "no reason reported")
    return Findings(problem="; ".join(r for r in reasons if r), via="none")
