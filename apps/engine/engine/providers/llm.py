"""Metered LLM access.

Every call returns its cost alongside its content. No stage is allowed to call a
provider SDK directly — cost tracking and provenance both depend on going through
here, and an unmetered call is invisible until the bill arrives.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from engine.settings import get_settings

# USD per million tokens (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
_DEFAULT_PRICE = (3.00, 15.00)


@dataclass
class Completion:
    text: str
    model: str
    prompt: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        inp, out = PRICING.get(self.model, _DEFAULT_PRICE)
        return (self.input_tokens * inp + self.output_tokens * out) / 1_000_000


class LLM:
    """Thin, provider-agnostic wrapper. Anthropic is the reference implementation."""

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.settings = s
        self.model = model or s.llm_model
        self._client: Any = None

    def _anthropic(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self.settings.anthropic_api_key,
                base_url=self.settings.llm_base_url or None,
            )
        return self._client

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Completion:
        client = self._anthropic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        resp = await client.messages.create(**kwargs)
        text = "".join(block.text for block in resp.content if block.type == "text")
        return Completion(
            text=text,
            model=self.model,
            prompt=prompt,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    async def json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        retries: int = 2,
    ) -> tuple[Any, Completion]:
        """Complete and parse JSON.

        Models wrap JSON in prose and fences no matter how firmly you ask them not to,
        so parse defensively and retry with the parse error fed back rather than
        failing the stage on a formatting hiccup.
        """
        instruction = (
            f"{prompt}\n\nRespond with valid JSON only. No prose, no markdown fences."
        )
        last_error = ""
        for attempt in range(retries + 1):
            body = instruction if not last_error else (
                f"{instruction}\n\nYour previous response could not be parsed: "
                f"{last_error}\nReturn only valid JSON this time."
            )
            completion = await self.complete(
                body, system=system, max_tokens=max_tokens, temperature=temperature
            )
            try:
                return _extract_json(completion.text), completion
            except ValueError as exc:
                last_error = str(exc)
                logger.warning("JSON parse failed (attempt {}): {}", attempt + 1, exc)
        raise ValueError(f"model did not return parseable JSON after {retries + 1} attempts")


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost balanced object or array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in response: {text[:200]!r}")


def primary() -> LLM:
    """The model that does script and SEO work. Quality matters more than cost here."""
    return LLM(get_settings().llm_model)


def fast() -> LLM:
    """The model for bulk, mechanical, or high-volume steps."""
    return LLM(get_settings().llm_fast_model)
