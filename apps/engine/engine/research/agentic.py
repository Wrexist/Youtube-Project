"""Research done by the model, with the search running on Anthropic's side.

`web.py` scrapes keyless search endpoints. That is the right floor — a fresh clone
with no keys has to be able to research something — but it is a floor: the endpoints
are anti-bot, their markup is not a contract, and every failure lands on the operator
as "no usable sources found" for a topic with pages of coverage.

When the model routed to `research` is one that can search, it does the searching.
Claude issues the queries, reads the results server-side, and answers with prose that
cites what it read. There is no scraping, no user-agent guessing, and no per-endpoint
markup to rot. It costs tokens, which is the trade: grounding is the one place in this
pipeline where paying for quality is not optional (see `docs`/CLAUDE.md non-negotiables).

Returns the same shape as `web.research`, plus what it spent, so the stage above does
not care which path produced its digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from engine.models import ModelSpec
from engine.providers.llm import ProviderUnavailable
from engine.settings import get_settings, named_credential

#: Server-side search, current generation: Claude filters results before they reach
#: the context window. Older models do not have it and take the previous version.
TOOL_CURRENT = "web_search_20260209"
TOOL_LEGACY = "web_search_20250305"

#: Published rate for server-side search at the time of writing, in USD per search.
#: Tokens are metered exactly; this is the one number here that is a constant rather
#: than a measurement, so it lives in one place and is easy to correct.
SEARCH_USD = 10.0 / 1000

#: `pause_turn` means the server-side loop hit its iteration ceiling mid-research.
#: Resuming is a normal part of the protocol, not an error — but it is not unbounded.
MAX_RESUMES = 4

SYSTEM = (
    "You are the research step of a video production pipeline. You search the web and "
    "report what you actually read. Never state a fact you did not find in a source. "
    "If the sources disagree, say so. If the topic is thin, say that too rather than "
    "padding — a later step will refuse to write an ungrounded script, and that is "
    "the correct outcome."
)


@dataclass
class Findings:
    """What a research pass produced, and what it cost to produce."""

    digest: str = ""
    sources: list[str] = field(default_factory=list)
    problem: str = ""
    #: How this was produced, for the provenance record: the operator needs to know
    #: whether a digest came from the model's own searching or from scraped HTML.
    via: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0

    def cost_usd(self, spec: ModelSpec) -> float:
        return spec.cost(self.input_tokens, self.output_tokens) + self.searches * SEARCH_USD

    def as_dict(self) -> dict:
        return {"digest": self.digest, "sources": self.sources, "problem": self.problem}


async def research(topic: str, spec: ModelSpec, *, max_sources: int = 8) -> Findings:
    """Have the model research `topic` itself. Raises `ProviderUnavailable` if it cannot."""
    if not spec.supports_web_search:
        raise ProviderUnavailable(f"{spec.key()} cannot search the web")

    from anthropic import AsyncAnthropic

    settings = get_settings()
    key = named_credential(spec.api_key_env) if spec.api_key_env else settings.anthropic_api_key
    if not key:
        raise ProviderUnavailable(
            "researching with the model needs ANTHROPIC_API_KEY. Add it to .env, or "
            "route the research task to a model that has a key."
        )

    client = AsyncAnthropic(api_key=key, base_url=spec.base_url or None)
    prompt = (
        f"Research this video topic: {topic}\n\n"
        f"Search for it, read the best {max_sources} sources you can find, and write up "
        f"what you learned: the specific numbers, dates, names, studies and quotes a "
        f"video would be built from. Attribute each claim to the source it came from. "
        f"Prefer primary sources and reporting over listicles and SEO filler.\n\n"
        f"If a phrasing returns nothing useful, search again with different words "
        f"before concluding there is nothing there."
    )

    findings = Findings(via=f"web-search:{spec.model}")
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    texts: list[str] = []
    cited: list[str] = []

    for attempt in range(MAX_RESUMES + 1):
        try:
            resp = await client.messages.create(
                model=spec.model,
                # Room for the reasoning, the searching and the write-up. `max_tokens`
                # bounds all three together on a thinking model.
                max_tokens=16_000,
                system=SYSTEM,
                # No `temperature`: the models worth routing here reject it outright.
                tools=[{"type": _tool_version(spec), "name": "web_search"}],
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — the caller falls back to scraping
            raise ProviderUnavailable(f"{spec.key()} web search failed: {exc}") from exc

        findings.input_tokens += resp.usage.input_tokens
        findings.output_tokens += resp.usage.output_tokens
        findings.searches += _search_count(resp.usage)
        texts.extend(b.text for b in resp.content if getattr(b, "type", "") == "text")
        cited.extend(_urls(resp.content))

        if getattr(resp, "stop_reason", "") == "refusal":
            raise ProviderUnavailable(
                f"{spec.key()} declined to research this topic. Rephrase it, or route "
                f"the research task to another model."
            )
        if getattr(resp, "stop_reason", "") != "pause_turn":
            break

        # Documented resume shape: the original ask plus the paused turn, and no
        # "continue" message — the trailing server-tool block is the signal.
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": resp.content},
        ]
        logger.info("research: {} paused mid-search, resuming ({})", spec.key(), attempt + 1)

    findings.digest = "\n\n".join(t.strip() for t in texts if t.strip())
    findings.sources = _dedupe(cited)[:max_sources]

    if not findings.digest:
        findings.problem = f"{spec.key()} searched but returned no write-up"
    elif not findings.sources:
        # Worth distinguishing: the write-up is usable, the citation list is not, and
        # a stage that demands sources would otherwise reject a good digest.
        findings.problem = f"{spec.key()} wrote a digest but cited no sources"

    logger.info(
        "research: {} produced {} source(s) in {} search(es) for {!r}",
        spec.key(),
        len(findings.sources),
        findings.searches,
        topic,
    )
    return findings


def _tool_version(spec: ModelSpec) -> str:
    return TOOL_CURRENT if spec.web_search_filters else TOOL_LEGACY


def _search_count(usage: Any) -> int:
    server = _get(usage, "server_tool_use")
    return int(_get(server, "web_search_requests") or 0) if server else 0


def _urls(blocks: Any) -> list[str]:
    """Every URL the model read or cited, however the SDK happens to shape the block.

    Deliberately structural rather than keyed to one block type: the search result
    and citation payloads are the part of this API most likely to gain a field, and
    losing the source list to an unrecognised block name would fail the stage on a
    successful search. Anything carrying a `url` counts.
    """
    found: list[str] = []
    for block in blocks or []:
        kind = _get(block, "type") or ""
        if kind.endswith("tool_result"):
            for item in _get(block, "content") or []:
                url = _get(item, "url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    found.append(url)
        for citation in _get(block, "citations") or []:
            url = _get(citation, "url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                found.append(url)
    return found


def _get(obj: Any, name: str) -> Any:
    """Attribute or key, because SDK models and raw dicts both turn up here."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
