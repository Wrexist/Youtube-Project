"""The metered LLM client.

This file exists because `tests/conftest.py` used to install a `types.ModuleType`
stub over `engine.providers.llm` before collection, so the real module — four
transports, the JSON-retry loop, `_extract_json` — was imported by no test at all,
while the docs claimed it was covered. `test_models.py` covers `engine/models.py`:
the routing table and the cost arithmetic, not this.

The transports are exercised against `respx` rather than a live provider, so what
is proven here is the request shape and the response parsing — the two things that
break silently when a provider changes a field name. It is not proof that any real
API accepts these requests; see KNOWN-ISSUES §1.3.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from engine.models import ModelSpec
from engine.providers.llm import (
    LLM,
    THINKING_RESERVE,
    Completion,
    ProviderUnavailable,
    _extract_json,
)
from engine.settings import get_settings


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    """Known keys, so a transport test never depends on the developer's `.env`.

    `Settings` reads `../../.env`, so without this a machine with a real
    `OPENAI_API_KEY` and one without it would take different branches through the
    header construction below.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    yield
    get_settings.cache_clear()


def spec(provider: str, **kw) -> ModelSpec:
    return ModelSpec(provider, kw.pop("model", "m"), **kw)


# ── _extract_json ───────────────────────────────────────────────────────────
#
# Every one of these shapes has been seen from a real model. The function is the
# only thing between them and a stage crashing on `None`.


class TestExtractJson:
    def test_bare_object(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_bare_array(self):
        assert _extract_json('["a", "b"]') == ["a", "b"]

    def test_surrounding_whitespace(self):
        assert _extract_json('\n\n  {"a": 1}\n  ') == {"a": 1}

    def test_fenced_with_language(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_without_language(self):
        assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_fence_wins_over_prose_around_it(self):
        text = 'Sure! Here is the JSON:\n```json\n{"a": 1}\n```\nLet me know if…'
        assert _extract_json(text) == {"a": 1}

    def test_embedded_in_prose_without_a_fence(self):
        assert _extract_json('Here you go: {"a": 1} — hope that helps!') == {"a": 1}

    def test_array_embedded_in_prose(self):
        assert _extract_json('The titles are ["one", "two"] as requested.') == ["one", "two"]

    def test_object_is_tried_before_array(self):
        """A payload containing both must not be sliced from the wrong bracket.

        `{"items": ["a"]}` has a `[` and a `]` too. Slicing on those yields `["a"]`,
        which parses — so the wrong answer would be returned silently rather than
        raising.
        """
        assert _extract_json('noise {"items": ["a"]} noise') == {"items": ["a"]}

    def test_nested_object_slices_to_the_outermost_braces(self):
        assert _extract_json('x {"a": {"b": 2}} y') == {"a": {"b": 2}}

    def test_prose_with_no_json_raises(self):
        with pytest.raises(ValueError, match="no JSON found"):
            _extract_json("I'm sorry, I can't help with that.")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="no JSON found"):
            _extract_json("")

    def test_malformed_json_raises_rather_than_returning_partial(self):
        """Still a ValueError, now a more specific one: a response that opens with
        a delimiter and never closes it ran out of room rather than being badly
        formatted, and the remedy is a bigger budget, not a firmer instruction."""
        from engine.providers.llm import Truncated

        with pytest.raises(Truncated, match="cut off"):
            _extract_json('{"a": 1,')

    def test_a_truncated_array_is_not_salvaged_into_its_first_element(self):
        """The silent half of the bug. The old salvage scanned for any `{`...`}`
        span, so a beats array cut off at twenty items found the first object and
        returned it — parsed cleanly, and every stage downstream believed the
        script had one beat."""
        from engine.providers.llm import Truncated

        with pytest.raises(Truncated):
            _extract_json('[{"purpose": "hook"}, {"purpose": "pro')

    def test_prose_around_real_json_is_still_salvaged(self):
        """The case the salvage exists for, and the reason it is kept."""
        assert _extract_json('Sure! {"ok": true} hope that helps') == {"ok": True}

    def test_the_error_quotes_the_response_so_the_log_is_diagnosable(self):
        with pytest.raises(ValueError, match="cannot help"):
            _extract_json("I cannot help with that request")

    def test_a_long_response_is_truncated_in_the_error(self):
        """The raw text goes into a log line and an exception message.

        Without the 200-char cap a 4000-token refusal lands in both, which is how a
        log file becomes unreadable at exactly the moment it is needed.
        """
        with pytest.raises(ValueError) as caught:
            _extract_json("x" * 5000)
        assert len(str(caught.value)) < 300


# ── transports ──────────────────────────────────────────────────────────────


class TestOpenAiCompatible:
    """OpenAI itself plus Groq, DeepSeek, OpenRouter, Together, LM Studio, vLLM."""

    @respx.mock
    async def test_request_shape_and_parsing(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "hello"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                },
            )
        )
        completion = await LLM(spec("openai", model="gpt-4o")).complete(
            "say hi", system="be brief", max_tokens=100, temperature=0.2
        )

        assert (completion.text, completion.input_tokens, completion.output_tokens) == (
            "hello",
            11,
            7,
        )
        assert completion.model == "openai:gpt-4o"

        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer sk-test-openai"
        body = _json(sent)
        assert body["model"] == "gpt-4o"
        assert body["max_tokens"] == 100
        assert body["temperature"] == 0.2
        # System prompt first, then the user turn — the order is the contract.
        assert body["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "say hi"},
        ]

    @respx.mock
    async def test_a_reasoning_model_gets_the_body_it_will_accept(self):
        """Three 400s, verified against the live API rather than inferred:

            max_tokens + temperature       -> 'max_tokens' is not supported with
                                              this model. Use 'max_completion_tokens'
            max_completion_tokens + temp   -> 'temperature' does not support 0.2
                                              with this model. Only the default (1)
            max_completion_tokens alone    -> 200

        So a route to GPT-5 failed on its first call while GPT-4o, on identical
        code, worked — which reads as "the model is broken" rather than "the
        request shape changed".
        """
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        )
        await LLM(spec("openai", model="gpt-5.6-sol")).complete(
            "say hi", max_tokens=100, temperature=0.2
        )

        body = _json(route.calls.last.request)
        assert "max_tokens" not in body, "the old spelling is a 400 on this model"
        # The caller's 100 plus the reasoning reserve: on these models the
        # thinking is drawn from the same budget as the answer, so handing the
        # number straight through returns an empty completion.
        assert body["max_completion_tokens"] > 100
        assert "temperature" not in body, "only the default is accepted"

    @respx.mock
    async def test_a_gateway_still_gets_the_old_spelling(self):
        """`base_url` points this same transport at Groq, DeepSeek and vLLM, none
        of which followed OpenAI's rename."""
        route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        )
        await LLM(
            spec(
                "openai_compatible",
                model="llama-3.3-70b",
                base_url="https://api.groq.com/openai/v1",
            )
        ).complete("say hi", max_tokens=100, temperature=0.2)

        body = _json(route.calls.last.request)
        assert body["max_tokens"] == 100
        assert body["temperature"] == 0.2

    @respx.mock
    async def test_no_system_prompt_sends_only_the_user_turn(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        )
        await LLM(spec("openai")).complete("say hi")
        assert _json(route.calls.last.request)["messages"] == [
            {"role": "user", "content": "say hi"}
        ]

    @respx.mock
    async def test_a_missing_usage_block_costs_zero_rather_than_raising(self):
        """LM Studio and vLLM omit `usage`. A KeyError here would fail the stage."""
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        )
        completion = await LLM(spec("openai", input_per_m=5, output_per_m=25)).complete("hi")
        assert (completion.input_tokens, completion.output_tokens, completion.cost_usd) == (0, 0, 0)

    @respx.mock
    async def test_a_custom_base_url_is_used_and_its_trailing_slash_stripped(self):
        route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        )
        await LLM(spec("openai", base_url="https://api.groq.com/openai/v1/")).complete("hi")
        assert route.called

    @respx.mock
    async def test_an_http_error_becomes_provider_unavailable_with_the_body(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, text="incorrect api key provided")
        )
        with pytest.raises(ProviderUnavailable, match="401"):
            await LLM(spec("openai")).complete("hi")

    @respx.mock
    async def test_no_key_configured_sends_no_authorization_header(self, monkeypatch):
        """Local servers reject `Bearer ` with an empty key rather than ignoring it."""
        get_settings.cache_clear()
        monkeypatch.setenv("OPENAI_API_KEY", "")
        route = respx.post("http://localhost:1234/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        )
        await LLM(spec("openai", base_url="http://localhost:1234/v1")).complete("hi")
        assert "authorization" not in route.calls.last.request.headers


class TestGemini:
    @respx.mock
    async def test_request_shape_and_parsing(self):
        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "hel"}, {"text": "lo"}]}}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
                },
            )
        )
        completion = await LLM(spec("gemini", model="gemini-2.0-flash")).complete(
            "say hi", system="be brief"
        )

        # Gemini splits a response across parts; they concatenate, they do not
        # replace each other.
        assert completion.text == "hello"
        assert (completion.input_tokens, completion.output_tokens) == (3, 2)

        sent = route.calls.last.request
        # Header, not `?key=`. Both authenticate, so a regression here would not fail
        # any call — it would silently put the key back in the URL, where proxy logs
        # and tracebacks pick it up. Assert the absence too, or the leak comes back
        # alongside a passing header assertion.
        assert sent.headers["x-goog-api-key"] == "test-gemini"
        assert "key" not in sent.url.params
        body = _json(sent)
        assert body["contents"] == [{"parts": [{"text": "say hi"}]}]
        assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}

    @respx.mock
    async def test_no_system_prompt_omits_system_instruction(self):
        route = respx.post(url__regex=r".*:generateContent").mock(
            return_value=httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
            )
        )
        await LLM(spec("gemini")).complete("hi")
        assert "systemInstruction" not in _json(route.calls.last.request)

    @respx.mock
    async def test_a_blocked_response_with_no_parts_yields_empty_text(self):
        """Safety-filtered candidates arrive with `content` and no `parts`.

        Empty text is then a JSON parse failure one level up, which retries — the
        right outcome. A KeyError would fail the stage outright.
        """
        respx.post(url__regex=r".*:generateContent").mock(
            return_value=httpx.Response(200, json={"candidates": [{"content": {}}]})
        )
        assert (await LLM(spec("gemini")).complete("hi")).text == ""

    @respx.mock
    async def test_an_http_error_becomes_provider_unavailable(self):
        respx.post(url__regex=r".*:generateContent").mock(
            return_value=httpx.Response(429, text="quota exceeded")
        )
        with pytest.raises(ProviderUnavailable, match="gemini returned 429"):
            await LLM(spec("gemini")).complete("hi")


class TestOllama:
    @respx.mock
    async def test_request_shape_and_parsing(self):
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": {"content": "hello"},
                    "prompt_eval_count": 9,
                    "eval_count": 4,
                },
            )
        )
        completion = await LLM(spec("ollama", model="qwen2.5")).complete("say hi", system="brief")

        assert (completion.text, completion.input_tokens, completion.output_tokens) == (
            "hello",
            9,
            4,
        )
        body = _json(route.calls.last.request)
        assert body["stream"] is False
        assert body["options"] == {"temperature": 1.0, "num_predict": 4096}
        assert body["messages"][0] == {"role": "system", "content": "brief"}

    @respx.mock
    async def test_a_local_model_is_free_however_many_tokens_it_burns(self):
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": {"content": "x"},
                    "prompt_eval_count": 90_000,
                    "eval_count": 9_000,
                },
            )
        )
        completion = await LLM(spec("ollama")).complete("hi")
        assert completion.cost_usd == 0.0
        assert completion.was_local is True

    @respx.mock
    async def test_format_json_is_sent_only_when_json_is_wanted(self):
        """This is what makes small local models usable for the structured stages.

        Ollama constrains decoding to valid JSON when `format` is set. Without it a
        7B model fails `_extract_json` often enough to exhaust the retries.
        """
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": "{}"}})
        )
        await LLM(spec("ollama")).complete("hi", want_json=True)
        assert _json(route.calls.last.request)["format"] == "json"

        await LLM(spec("ollama")).complete("hi")
        assert "format" not in _json(route.calls.last.request)

    @respx.mock
    async def test_a_refused_connection_says_how_to_start_ollama(self):
        """The single most common local failure. The message is the whole fix."""
        respx.post("http://localhost:11434/api/chat").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with pytest.raises(ProviderUnavailable, match="ollama serve"):
            await LLM(spec("ollama")).complete("hi")

    @respx.mock
    async def test_a_404_says_how_to_pull_the_model(self):
        """Ollama 404s an absent model. "404" alone reads as a wrong URL."""
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(404, text="model not found")
        )
        with pytest.raises(ProviderUnavailable, match="ollama pull qwen2.5"):
            await LLM(spec("ollama", model="qwen2.5")).complete("hi")

    @respx.mock
    async def test_another_error_status_is_reported_verbatim(self):
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(500, text="out of memory")
        )
        with pytest.raises(ProviderUnavailable, match="500"):
            await LLM(spec("ollama")).complete("hi")


class TestAnthropic:
    """Routed through the SDK, so the SDK's transport is what gets mocked."""

    @respx.mock
    async def test_request_shape_and_parsing(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "hello"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                },
            )
        )
        completion = await LLM(
            spec("anthropic", model="claude-sonnet-5", input_per_m=3, output_per_m=15)
        ).complete("say hi", system="be brief", max_tokens=64)

        assert completion.text == "hello"
        assert (completion.input_tokens, completion.output_tokens) == (12, 3)
        assert completion.cost_usd == pytest.approx((12 * 3 + 3 * 15) / 1_000_000)

        body = _json(route.calls.last.request)
        # Anthropic takes the system prompt as a top-level field, not a message.
        assert body["system"] == "be brief"
        assert body["messages"] == [{"role": "user", "content": "say hi"}]
        # Sonnet 5 reasons before answering, and `max_tokens` bounds the reasoning and
        # the answer together — see `THINKING_RESERVE`.
        assert body["max_tokens"] == 64 + THINKING_RESERVE

    @respx.mock
    async def test_temperature_is_not_sent_to_models_that_reject_it(self):
        """The bug that broke every critical stage.

        Opus 4.7 and up, and Fable 5, removed the sampling parameters: `temperature`
        returns a 400 rather than being ignored. It was sent unconditionally, so the
        default route for hook, draft, critique and titles failed on its first call.
        """
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_reply("x"))
        )
        for model in ("claude-opus-5", "claude-opus-4-8", "claude-fable-5"):
            await LLM(spec("anthropic", model=model)).complete("hi")
            body = _json(route.calls.last.request)
            assert "temperature" not in body, model
            assert body["max_tokens"] == 4096 + THINKING_RESERVE, model

    @respx.mock
    async def test_sonnet_5_keeps_the_default_temperature_and_drops_any_other(self):
        """It accepts the field, but only at its default value."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_reply("x"))
        )
        sonnet = spec("anthropic", model="claude-sonnet-5")

        await LLM(sonnet).complete("hi")
        assert _json(route.calls.last.request)["temperature"] == 1.0

        await LLM(sonnet).complete("hi", temperature=0.3)
        assert "temperature" not in _json(route.calls.last.request)

    @respx.mock
    async def test_a_model_with_sampling_still_gets_the_temperature_it_asked_for(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_reply("x"))
        )
        await LLM(spec("anthropic", model="claude-haiku-4-5-20251001")).complete(
            "hi", temperature=0.2, max_tokens=100
        )
        body = _json(route.calls.last.request)
        assert body["temperature"] == 0.2
        # No reserve either: nothing is spent on reasoning here.
        assert body["max_tokens"] == 100

    @respx.mock
    async def test_no_system_prompt_omits_the_field(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_anthropic_reply("x"))
        )
        await LLM(spec("anthropic")).complete("hi")
        assert "system" not in _json(route.calls.last.request)

    @respx.mock
    async def test_non_text_blocks_are_dropped_rather_than_crashing(self):
        """A thinking block alongside the answer must not end up in the text."""
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json=_anthropic_reply(
                    blocks=[
                        {"type": "thinking", "thinking": "hmm", "signature": "s"},
                        {"type": "text", "text": "answer"},
                    ]
                ),
            )
        )
        assert (await LLM(spec("anthropic")).complete("hi")).text == "answer"


# ── LLM.json — the retry loop ───────────────────────────────────────────────


class TestJsonRetry:
    @respx.mock
    async def test_first_attempt_parses_and_does_not_retry(self):
        route = _openai_returning(['{"a": 1}'])
        value, completion = await LLM(spec("openai")).json("give me json")
        assert value == {"a": 1}
        assert route.call_count == 1
        assert isinstance(completion, Completion)

    @respx.mock
    async def test_the_json_only_instruction_is_appended_to_the_prompt(self):
        route = _openai_returning(['{"a": 1}'])
        await LLM(spec("openai")).json("give me json")
        prompt = _json(route.calls.last.request)["messages"][-1]["content"]
        assert prompt.startswith("give me json")
        assert "valid JSON only" in prompt

    @respx.mock
    async def test_a_parse_failure_retries_with_the_error_fed_back(self):
        """Feeding the error back is the point — a bare retry repeats the mistake."""
        route = _openai_returning(["sorry, no", '{"a": 1}'])
        value, _ = await LLM(spec("openai")).json("give me json")

        assert value == {"a": 1}
        assert route.call_count == 2
        second = _json(route.calls[1].request)["messages"][-1]["content"]
        assert "could not be parsed" in second
        assert "no JSON found" in second
        # And the original ask survives the retry — otherwise the model is being
        # asked to fix the format of a question it can no longer see.
        assert second.startswith("give me json")

    @respx.mock
    async def test_the_returned_completion_is_the_successful_attempt(self):
        """Provenance and cost come off this object. Returning attempt 1 would
        attribute the value to a response that did not produce it."""
        route = _openai_returning(["nope", '{"a": 1}'])
        _, completion = await LLM(spec("openai")).json("give me json")
        assert "could not be parsed" in completion.prompt
        assert route.call_count == 2

    @respx.mock
    async def test_every_attempt_is_billed_into_the_returned_completion(self):
        """The provider charges for a response it could not parse just the same.

        Only the last attempt is returned, and that object is the whole cost record
        a stage keeps — so a call that succeeded on its third try used to record a
        third of its own bill. `spent_usd` under-reported the run, and the per-video
        budget ceiling guarded a number that was never the real one.
        """
        priced = spec("openai", input_per_m=1_000_000, output_per_m=1_000_000)  # $1 per token
        _openai_billing(["nope", "still nope", '{"a": 1}'], input_tokens=100, output_tokens=11)

        value, completion = await LLM(priced).json("give me json")

        assert value == {"a": 1}
        assert completion.input_tokens == 300, "two discarded attempts' input went unbilled"
        assert completion.output_tokens == 33
        assert completion.cost_usd == pytest.approx(333.0)
        assert completion.discarded_attempts == 2

    @respx.mock
    async def test_a_first_attempt_success_bills_only_itself(self):
        """The control: no retries means nothing to fold in, and no double count."""
        priced = spec("openai", input_per_m=1_000_000, output_per_m=1_000_000)
        _openai_billing(['{"a": 1}'], input_tokens=100, output_tokens=11)

        _, completion = await LLM(priced).json("give me json")
        assert (completion.input_tokens, completion.output_tokens) == (100, 11)
        assert completion.discarded_attempts == 0

    @respx.mock
    async def test_the_spend_of_a_run_that_never_parsed_is_named_in_the_error(self):
        """There is no Completion to hang it on — the stage raises — so the message
        is the only record that four frontier-model calls were paid for."""
        priced = spec("openai", input_per_m=1_000_000, output_per_m=1_000_000)
        _openai_billing(["no"] * 4, input_tokens=100, output_tokens=11)

        with pytest.raises(ValueError, match=r"\$333\."):
            await LLM(priced).json("x", retries=2)

    @respx.mock
    async def test_a_hosted_model_gets_retries_plus_one_attempts(self):
        """`retries=2` means three tries: the first is not a retry."""
        route = _openai_returning(["no"] * 6)
        with pytest.raises(ValueError, match="after 3 attempts"):
            await LLM(spec("openai")).json("x", retries=2)
        assert route.call_count == 3

    @respx.mock
    async def test_retries_zero_still_makes_one_attempt(self):
        route = _openai_returning(["no"] * 3)
        with pytest.raises(ValueError, match="after 1 attempt"):
            await LLM(spec("openai")).json("x", retries=0)
        assert route.call_count == 1

    @respx.mock
    async def test_a_local_model_gets_one_extra_attempt(self):
        """Small local models fail formatting more often, and the retry is free."""
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": "no json here"}})
        )
        with pytest.raises(ValueError, match="after 4 attempts"):
            await LLM(spec("ollama")).json("x", retries=2)
        assert route.call_count == 4

    @respx.mock
    async def test_a_model_marked_unreliable_at_json_suggests_rerouting(self):
        _openai_returning(["no"] * 4)
        with pytest.raises(ValueError, match="unreliable at strict JSON"):
            await LLM(spec("openai", json_mode=False, label="Tiny")).json("x")

    @respx.mock
    async def test_a_json_capable_model_does_not_suggest_rerouting(self):
        """The hint is advice about the routing table. On a model that is already
        the strong choice it is noise pointing at nothing."""
        _openai_returning(["no"] * 4)
        with pytest.raises(ValueError) as caught:
            await LLM(spec("openai", json_mode=True)).json("x")
        assert "unreliable" not in str(caught.value)

    @respx.mock
    async def test_a_provider_error_is_not_retried_as_a_parse_failure(self):
        """`ProviderUnavailable` means the call did not happen. Burning the JSON
        retries on it turns one 500 into three and reports the wrong cause."""
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="upstream error")
        )
        with pytest.raises(ProviderUnavailable):
            await LLM(spec("openai")).json("x")
        assert route.call_count == 1

    @respx.mock
    async def test_json_asks_for_json_mode_on_the_transport(self):
        """`want_json` has to reach `_ollama`, or the constrained decoding that makes
        local models viable for these stages never switches on."""
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": "{}"}})
        )
        await LLM(spec("ollama")).json("x")
        assert _json(route.calls.last.request)["format"] == "json"


# ── helpers ─────────────────────────────────────────────────────────────────


def _json(request: httpx.Request) -> dict:
    import json as _stdlib_json

    return _stdlib_json.loads(request.content)


def _anthropic_reply(text: str = "x", blocks: list | None = None) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": blocks if blocks is not None else [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _openai_returning(texts: list[str]):
    """One mocked route that returns each text in turn, then repeats the last."""
    replies = [
        httpx.Response(200, json={"choices": [{"message": {"content": t}}], "usage": {}})
        for t in texts
    ]
    return respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=replies)


def _openai_billing(texts: list[str], *, input_tokens: int, output_tokens: int):
    """`_openai_returning` with a usage block, so each attempt costs something.

    The plain helper reports no usage at all, which is why a retry loop that
    dropped the earlier attempts' tokens read as correct against it.
    """
    replies = [
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": t}}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            },
        )
        for t in texts
    ]
    return respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=replies)
