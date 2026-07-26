"""Tests for keyword-grounding failure diagnosis.

The bug: `suggest()` dropped every exception on the floor, so a blocked network, a
429, a TLS failure and a genuinely obscure topic all produced `[]`. The stage then
raised a bare "no keyword evidence retrieved" — correct policy, useless diagnosis,
and it is the first stage of the only workflow, so it is the first thing every new
user meets.

These tests fix the *distinguishability*: different causes must produce different,
actionable messages.
"""

from __future__ import annotations

import httpx
import pytest

from engine.research.keywords import KeywordEvidence, _describe, gather, suggest_with_failures
from engine.settings import get_settings

# ── _describe ───────────────────────────────────────────────────────────────


def test_describe_names_the_status_for_http_errors():
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert _describe(exc) == "HTTP 429"


def test_describe_names_timeouts_plainly():
    assert _describe(httpx.ConnectTimeout("slow")) == "timed out"


def test_describe_falls_back_to_the_exception_type():
    assert _describe(httpx.ConnectError("refused")) == "ConnectError"


# ── suggest_with_failures ───────────────────────────────────────────────────


async def test_total_failure_is_reported_with_a_count_and_a_cause(monkeypatch):
    async def boom(client, query):
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr("engine.research.keywords._suggest_one", boom)

    phrases, failure = await suggest_with_failures("bridges", expand=False)
    assert phrases == []
    assert "1/1 requests failed" in failure
    assert "ConnectError" in failure


async def test_a_genuinely_empty_result_is_not_reported_as_a_failure(monkeypatch):
    """No suggestions and no errors is a real answer, not a broken network."""

    async def empty(client, query):
        return []

    monkeypatch.setattr("engine.research.keywords._suggest_one", empty)

    phrases, failure = await suggest_with_failures("bridges", expand=False)
    assert phrases == []
    assert failure == ""


async def test_partial_failure_still_returns_what_worked(monkeypatch):
    """One dead request out of 27 must not discard the other 26."""
    calls = {"n": 0}

    async def flaky(client, query):
        calls["n"] += 1
        if calls["n"] % 2:
            raise httpx.ConnectError("nope")
        return [f"{query} result"]

    monkeypatch.setattr("engine.research.keywords._suggest_one", flaky)

    phrases, failure = await suggest_with_failures("bridges", expand=True)
    assert phrases  # the successful half survived
    assert failure == ""  # partial success is not a failure to report


# ── gather ──────────────────────────────────────────────────────────────────


async def test_gather_records_which_source_failed(monkeypatch):
    async def boom(client, query):
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr("engine.research.keywords._suggest_one", boom)

    evidence = await gather("bridges", youtube_client=None)
    assert not evidence.is_grounded
    assert "youtube_autocomplete" in evidence.failures
    assert "youtube_search" in evidence.failures  # skipped, no channel


async def test_a_working_source_is_not_recorded_as_failed(monkeypatch):
    async def ok(client, query):
        return ["why bridges collapse"]

    monkeypatch.setattr("engine.research.keywords._suggest_one", ok)

    evidence = await gather("bridges", youtube_client=None)
    assert evidence.is_grounded
    assert "youtube_autocomplete" not in evidence.failures
    assert "youtube_autocomplete" in evidence.sources


# ── diagnosis ───────────────────────────────────────────────────────────────


def test_diagnosis_points_at_the_network_when_sources_failed():
    evidence = KeywordEvidence(
        seed="bridges",
        failures={"youtube_autocomplete": "27/27 requests failed (ConnectError)"},
    )
    message = evidence.diagnosis()
    assert "bridges" in message
    assert "27/27" in message
    assert "network" in message.lower()


def test_diagnosis_points_at_the_topic_when_nothing_failed():
    """Every source answered and had nothing — that is a topic problem."""
    message = KeywordEvidence(seed="asdkjhasd").diagnosis()
    assert "obscure" in message
    assert "network" not in message.lower()


def test_diagnosis_lists_every_failed_source():
    evidence = KeywordEvidence(
        seed="bridges",
        failures={
            "youtube_autocomplete": "27/27 requests failed (HTTP 429)",
            "youtube_search": "skipped (no channel connected)",
        },
    )
    message = evidence.diagnosis()
    assert "youtube_autocomplete" in message
    assert "youtube_search" in message


@pytest.mark.parametrize(
    "failures",
    [{}, {"youtube_autocomplete": "27/27 requests failed (ConnectError)"}],
)
def test_diagnosis_always_names_the_topic_that_failed(failures):
    """With several jobs in flight, a message that omits the topic is unusable."""
    message = KeywordEvidence(seed="why bridges collapse", failures=failures).diagnosis()
    assert "why bridges collapse" in message


# ── the keyed fallback ──────────────────────────────────────────────────────


async def test_no_fallback_configured_is_not_a_failure(monkeypatch):
    """Most installs have no keyed source. That must not show up as an error."""
    from engine.research.keywords import _fallback_suggest

    get_settings.cache_clear()
    monkeypatch.delenv("KEYWORD_API_URL", raising=False)
    try:
        assert await _fallback_suggest("bridges") == ([], "")
    finally:
        get_settings.cache_clear()


async def test_the_fallback_runs_only_when_the_free_sources_are_empty(monkeypatch):
    """It costs money. The free path usually works, so it is tried first."""
    calls = {"n": 0}

    async def ok(client, query):
        return ["why bridges collapse"]

    async def counting_fallback(seed, **_kw):
        calls["n"] += 1
        return ["should not be reached"], ""

    monkeypatch.setattr("engine.research.keywords._suggest_one", ok)
    monkeypatch.setattr("engine.research.keywords._fallback_suggest", counting_fallback)

    evidence = await gather("bridges", youtube_client=None)
    assert evidence.is_grounded
    assert calls["n"] == 0, "the fallback ran even though autocomplete worked"


async def test_the_fallback_rescues_a_blocked_network(monkeypatch):
    """The point of §3.2: both free sources blocked used to end the run."""

    async def blocked(client, query):
        raise httpx.ConnectError("datacenter IP")

    async def fallback(seed, **_kw):
        return ["why bridges collapse", "bridge failure causes"], ""

    monkeypatch.setattr("engine.research.keywords._suggest_one", blocked)
    monkeypatch.setattr("engine.research.keywords._fallback_suggest", fallback)

    evidence = await gather("bridges", youtube_client=None)
    assert evidence.is_grounded
    assert "keyword_api" in evidence.sources
    # The autocomplete failure is still recorded — the run survived, but the
    # operator should still know their network is blocking it.
    assert "youtube_autocomplete" in evidence.failures


async def test_a_dead_fallback_is_recorded_not_raised(monkeypatch):
    async def blocked(client, query):
        raise httpx.ConnectError("blocked")

    async def dead(seed, **_kw):
        return [], "HTTP 502"

    monkeypatch.setattr("engine.research.keywords._suggest_one", blocked)
    monkeypatch.setattr("engine.research.keywords._fallback_suggest", dead)

    evidence = await gather("bridges", youtube_client=None)
    assert not evidence.is_grounded
    assert evidence.failures["keyword_api"] == "HTTP 502"
    assert "keyword_api" in evidence.diagnosis()


@pytest.mark.parametrize(
    "payload,expected",
    [
        (["a", "b"], ["a", "b"]),
        ({"keywords": ["a"]}, ["a"]),
        ({"results": ["B"]}, ["b"]),
        ({}, []),
    ],
)
async def test_the_fallback_accepts_the_common_response_shapes(
    monkeypatch, payload, expected, respx_mock=None
):
    """Deliberately generic: the point is having a second source, not a vendor."""
    import httpx as _httpx

    from engine.research import keywords as kw

    get_settings.cache_clear()
    monkeypatch.setenv("KEYWORD_API_URL", "https://keywords.test/search")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, *_a, **_kw):
            return _httpx.Response(
                200, json=payload, request=_httpx.Request("GET", "https://keywords.test/search")
            )

    monkeypatch.setattr(kw.httpx, "AsyncClient", lambda **_kw: FakeClient())
    try:
        phrases, failure = await kw._fallback_suggest("bridges")
        assert phrases == expected
        assert failure == ""
    finally:
        get_settings.cache_clear()
