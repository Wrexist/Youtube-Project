"""Metered LLM access across providers.

Every call returns its cost alongside its content. No stage calls a provider SDK
directly — cost tracking and provenance both depend on going through here, and an
unmetered call is invisible until the bill arrives.

Four transports cover everything worth supporting:

  * **anthropic** — native Messages API
  * **openai_compatible** — OpenAI itself, plus Groq, DeepSeek, OpenRouter, Together,
    LM Studio, vLLM, and anything else that speaks `/v1/chat/completions`
  * **gemini** — native generateContent
  * **ollama** — local models over `/api/chat`

Which model handles which task is decided by `engine.models.routing`, not here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from engine.models import ModelSpec, routing
from engine.settings import get_settings, named_credential

DEFAULT_OLLAMA_URL = "http://localhost:11434"

#: Extra output budget handed to models that reason before answering.
#:
#: On those, `max_tokens` caps the reasoning *and* the answer together. A stage asking
#: for 4,096 tokens of script would get a reasoning trace plus whatever was left —
#: truncated mid-sentence, and truncated worse the harder the prompt. The reserve keeps
#: the caller's number meaning what it says. It costs nothing unless the reasoning
#: actually uses it, and output tokens are metered either way.
THINKING_RESERVE = 8192

#: Ceiling on the doubling in `json()` when a response comes back truncated.
#: Generous enough for the longest thing any stage asks for — a full script
#: revision — and finite so that a model which never emits a closing brace cannot
#: walk the budget up attempt after attempt on someone's bill.
_MAX_JSON_TOKENS = 32_768


class Truncated(ValueError):
    """The response was valid JSON that ran out of output budget.

    A `ValueError` so every existing `except ValueError` around `_extract_json`
    still catches it, and a distinct type so the retry loop can tell "the model
    wrapped its JSON in prose" (ask again) apart from "the model needed more
    room" (give it more room).
    """


class ProviderUnavailable(RuntimeError):
    """The provider could not be reached — distinct from it returning bad output."""


@dataclass
class Completion:
    text: str
    model: str
    prompt: str
    input_tokens: int
    output_tokens: int
    spec: ModelSpec | None = None
    #: How many *earlier*, discarded calls are folded into the token counts above.
    #: Set by `LLM.json`, whose retry loop pays for every attempt but only ever
    #: returns the last one. Zero for a plain `complete()`.
    discarded_attempts: int = 0

    @property
    def cost_usd(self) -> float:
        if self.spec is None:
            return 0.0
        return self.spec.cost(self.input_tokens, self.output_tokens)

    @property
    def was_local(self) -> bool:
        return bool(self.spec and self.spec.is_local)


class LLM:
    """A model, wherever it lives."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.settings = get_settings()

    def _credential(self, default: str) -> str:
        """The key to send for this model.

        `ModelSpec.api_key_env` wins when it is set, because the alternative is
        sending the provider's own key to whatever `base_url` points at — and
        `base_url` is how every supported gateway is reached, so that is the normal
        case, not an exotic one. Empty `api_key_env` keeps the settings key, which
        is what every catalogue entry and every existing route relies on.

        An `api_key_env` naming an unset variable is an error rather than a silent
        fall back to the default key: falling back would send the OpenAI key to the
        gateway, which is the exact thing the field exists to stop.
        """
        if not self.spec.api_key_env:
            return default
        key = named_credential(self.spec.api_key_env)
        if not key:
            raise ProviderUnavailable(
                f"{self.spec.key()} is configured to authenticate with "
                f"${self.spec.api_key_env}, which is unset or empty. Add it to .env."
            )
        return key

    # ── transports ──────────────────────────────────────────────────────────

    async def _anthropic(self, prompt: str, system: str | None, max_tokens: int, temp: float):
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=self._credential(self.settings.anthropic_api_key),
            base_url=self.spec.base_url or None,
        )
        kwargs: dict[str, Any] = {
            "model": self.spec.model,
            "max_tokens": max_tokens + (THINKING_RESERVE if self.spec.thinks_by_default else 0),
            "messages": [{"role": "user", "content": prompt}],
        }
        # `temperature` is not universally accepted any more, and the models that
        # dropped it reject it with a 400 rather than ignoring it — so sending it
        # unconditionally broke every stage routed to the strongest model available.
        # Via `extra_body` rather than as a keyword: the 1.x SDK removed the
        # sampling parameters from `messages.create()`'s signature entirely
        # (a client-side TypeError before any request is made), while the models
        # this policy admits still accept them on the wire. `extra_body` merges
        # into the request JSON as-is, so the wire shape is unchanged.
        policy = self.spec.temperature_policy
        if policy == "any" or (policy == "default-only" and temp == 1.0):
            kwargs["extra_body"] = {"temperature": temp}
        if system:
            kwargs["system"] = system
        resp = await client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    async def _openai_compatible(
        self, prompt: str, system: str | None, max_tokens: int, temp: float
    ):
        base = self.spec.base_url or "https://api.openai.com/v1"
        key = self._credential(self.settings.openai_api_key)
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        # Same shape as the Anthropic transport above, and for the same reasons:
        # OpenAI's reasoning models renamed the output ceiling, spend it on
        # thinking before the answer, and reject any temperature but the default.
        # All three are 400s rather than ignored parameters, so a route to GPT-5
        # failed on its first call while GPT-4o on the identical code worked.
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            self.spec.max_tokens_field: max_tokens
            + (THINKING_RESERVE if self.spec.thinks_by_default else 0),
        }
        policy = self.spec.temperature_policy
        if policy == "any" or (policy == "default-only" and temp == 1.0):
            body["temperature"] = temp

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                json=body,
            )
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"{base} returned {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        usage = payload.get("usage", {})
        return (
            payload["choices"][0]["message"]["content"],
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    async def _gemini(self, prompt: str, system: str | None, max_tokens: int, temp: float):
        base = self.spec.base_url or "https://generativelanguage.googleapis.com/v1beta"
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base}/models/{self.spec.model}:generateContent",
                # Header, not `?key=`. Google accepts both, but a query string is the
                # one part of an HTTPS request that leaks by default — into proxy
                # logs, into `httpx`'s own INFO line, into any traceback that quotes
                # `request.url`. The header form is documented for generateContent and
                # keeps the key out of every one of those.
                headers={"x-goog-api-key": self._credential(self.settings.gemini_api_key)},
                json=body,
            )
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"gemini returned {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        text = "".join(
            part.get("text", "") for part in payload["candidates"][0]["content"].get("parts", [])
        )
        usage = payload.get("usageMetadata", {})
        return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)

    async def _ollama(
        self, prompt: str, system: str | None, max_tokens: int, temp: float, want_json: bool
    ):
        """Local models.

        No timeout ceiling worth setting: a 32B model on CPU can legitimately take
        minutes for a long draft, and killing it halfway is never the right call.

        `format: json` is passed when the caller wants JSON — Ollama constrains
        decoding to valid JSON, which is what makes small local models usable for the
        structured stages at all.
        """
        base = self.spec.base_url or DEFAULT_OLLAMA_URL
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temp, "num_predict": max_tokens},
        }
        if want_json:
            body["format"] = "json"

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                resp = await client.post(f"{base.rstrip('/')}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(
                f"cannot reach Ollama at {base}. Is it running? `ollama serve`"
            ) from exc

        if resp.status_code == 404:
            raise ProviderUnavailable(
                f"Ollama has no model '{self.spec.model}'. Pull it: `ollama pull {self.spec.model}`"
            )
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"ollama returned {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        return (
            payload["message"]["content"],
            payload.get("prompt_eval_count", 0),
            payload.get("eval_count", 0),
        )

    # ── public ──────────────────────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        want_json: bool = False,
    ) -> Completion:
        temp = self.spec.temperature if temperature is None else temperature

        if self.spec.provider == "anthropic":
            text, inp, out = await self._anthropic(prompt, system, max_tokens, temp)
        elif self.spec.provider == "gemini":
            text, inp, out = await self._gemini(prompt, system, max_tokens, temp)
        elif self.spec.provider == "ollama":
            text, inp, out = await self._ollama(prompt, system, max_tokens, temp, want_json)
        else:  # openai and anything OpenAI-compatible
            text, inp, out = await self._openai_compatible(prompt, system, max_tokens, temp)

        return Completion(
            text=text,
            model=self.spec.key(),
            prompt=prompt,
            input_tokens=inp,
            output_tokens=out,
            spec=self.spec,
        )

    async def json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        retries: int = 2,
    ) -> tuple[Any, Completion]:
        """Complete and parse JSON.

        Models wrap JSON in prose and fences no matter how firmly you ask them not
        to, so parse defensively and retry with the parse error fed back rather than
        failing the stage on a formatting hiccup. Local models need this more, which
        is why they get an extra attempt.

        **Every attempt is billed, and the returned `Completion` says so.** The
        provider charges for a response that could not be parsed exactly as it
        charges for one that could, but only the last one is returned — and that
        object is the entire cost record a stage keeps. So a call that succeeded on
        its third try recorded a third of its own bill, `spent_usd` under-reported
        the whole run, and the per-video ceiling guarded a number that was never the
        real one. The discarded attempts' tokens are folded into the winner's counts
        (same spec, so the arithmetic is exact) rather than tracked separately,
        because `cost_usd` is derived from them and every consumer reads that.
        """
        instruction = f"{prompt}\n\nRespond with valid JSON only. No prose, no markdown fences."
        attempts = retries + (1 if self.spec.is_local else 0)
        last_error = ""
        was_truncated = False
        budget = max_tokens
        discarded_input = discarded_output = 0

        for attempt in range(attempts + 1):
            if not last_error:
                body = instruction
            elif was_truncated:
                # It did not get the format wrong — it ran out of room. Telling it
                # to "return only valid JSON this time" is both useless and
                # slightly wrong, and re-sending the same ceiling reproduces the
                # same cut-off object.
                body = (
                    f"{instruction}\n\nYour previous response was cut off before it "
                    f"finished. You have more room now; keep every field but be "
                    f"concise in the long ones."
                )
            else:
                body = (
                    f"{instruction}\n\nYour previous response could not be parsed: "
                    f"{last_error}\nReturn only valid JSON this time."
                )

            completion = await self.complete(
                body,
                system=system,
                max_tokens=budget,
                temperature=temperature,
                want_json=True,
            )
            try:
                value = _extract_json(completion.text)
            except ValueError as exc:
                discarded_input += completion.input_tokens
                discarded_output += completion.output_tokens
                last_error = str(exc)
                was_truncated = isinstance(exc, Truncated)
                if was_truncated:
                    # Doubled, and capped so a model that simply will not stop
                    # cannot walk the budget up indefinitely on someone's bill.
                    budget = min(budget * 2, _MAX_JSON_TOKENS)
                logger.warning(
                    "JSON parse failed on {} (attempt {}): {}{}",
                    self.spec.key(),
                    attempt + 1,
                    exc,
                    f" - retrying with max_tokens={budget}" if was_truncated else "",
                )
                continue

            completion.input_tokens += discarded_input
            completion.output_tokens += discarded_output
            completion.discarded_attempts = attempt
            return value, completion

        hint = (
            f" {self.spec.label or self.spec.model} is marked as unreliable at strict "
            f"JSON; consider routing this task to a stronger model."
            if not self.spec.json_mode
            else ""
        )
        # The spend is named because it is real and nothing else will record it: the
        # stage raises, so there is no Completion and no StageOutput to carry a cost.
        # A run that burned four frontier-model calls and recorded $0 for them is the
        # one case where the log is the only ledger there is.
        wasted = self.spec.cost(discarded_input, discarded_output)
        raise ValueError(
            f"{self.spec.key()} did not return parseable JSON after {attempts + 1} "
            f"attempts (${wasted:.4f} spent and unrecorded).{hint}"
        )


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Salvage only when the model wrapped its JSON in prose. When the response
    # *begins* with a delimiter it was already trying to answer in pure JSON, and
    # anything that failed to parse from there is cut off rather than surrounded —
    # so scanning for an inner span does not recover the answer, it invents a
    # smaller one.
    #
    # That is not hypothetical and it is silent, which makes it the worse half of
    # this bug: `[{"a": 1}, {"b": ` finds `{` at index 1 and `}` at index 8 and
    # returns `{"a": 1}`. A beats array truncated at twenty items came back as one
    # item, parsed cleanly, and every stage downstream believed the script had a
    # single beat.
    if not text or text[0] not in "{[":
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    # Truncation is not a formatting problem and must not be reported as one. A
    # response that opens with `{` and never closes it is valid JSON that ran out
    # of room, and the remedy is a bigger budget rather than a firmer instruction
    # — retrying with the same ceiling produces the same cut-off object, which is
    # what happened to a critique on Opus 5: two three-minute attempts, both
    # billed, both truncated at the same point.
    if text and text[0] in "{[":
        raise Truncated(
            f"response was cut off mid-JSON after {len(text)} characters "
            f"(max_tokens too low): {text[:160]!r}"
        )
    raise ValueError(f"no JSON found in response: {text[:200]!r}")


def for_task(task: str) -> LLM:
    """The model routed to a task. This is how stages should acquire a model."""
    return LLM(routing.spec_for(task))


# Kept so existing stages keep working; both now resolve through the routing table.
def primary() -> LLM:
    return for_task("draft")


def fast() -> LLM:
    return for_task("tags")


async def probe_ollama(base_url: str = DEFAULT_OLLAMA_URL) -> dict:
    """What Ollama actually has installed.

    Used by the Models screen so it offers models that exist on this machine rather
    than a hardcoded list the user has to guess at.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)[:200], "models": []}

    return {
        "available": True,
        "models": [
            {
                "name": m["name"],
                "size_gb": round(m.get("size", 0) / 1e9, 1),
                "family": m.get("details", {}).get("family", ""),
                "parameters": m.get("details", {}).get("parameter_size", ""),
            }
            for m in resp.json().get("models", [])
        ],
    }
