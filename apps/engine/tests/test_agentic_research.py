"""Research done by the routed model, with the search running server-side.

The scrape chain in `research/web.py` is the floor: anti-bot endpoints whose markup is
not a contract, which reported "no usable sources found" for a topic with pages of
coverage. This path has the model search for itself. What matters here is that the
sources survive the trip out of the response, that a paused turn is resumed rather
than silently truncated, and that a failure falls back to scraping instead of failing
the render.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from engine.models import CATALOGUE, ModelSpec
from engine.providers.llm import ProviderUnavailable
from engine.research import agentic, gather

MESSAGES = "https://api.anthropic.com/v1/messages"

#: One result, wrapped in the redirector DuckDuckGo actually emits.
RESULT_PAGE = (
    '<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.test%2Fa">t</a>'
)

OPUS = CATALOGUE["anthropic:claude-opus-5"]
HAIKU = CATALOGUE["anthropic:claude-haiku-4-5-20251001"]
LOCAL = ModelSpec("ollama", "qwen2.5:14b", "Qwen (local)")


@pytest.fixture(autouse=True)
def key(monkeypatch):
    from engine.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    yield
    get_settings.cache_clear()


def reply(
    *,
    text: str = "Beast spent $4m on a single video.",
    urls: tuple[str, ...] = ("https://example.com/a",),
    stop: str = "end_turn",
    searches: int = 2,
) -> dict:
    """A response in the shape the API actually returns for a server-side search."""
    content: list[dict] = []
    if urls:
        content.append(
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": url,
                        "title": "t",
                        "encrypted_content": "x",
                        "page_age": None,
                    }
                    for url in urls
                ],
            }
        )
    content.append({"type": "text", "text": text})
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "server_tool_use": {"web_search_requests": searches},
        },
    }


# ── the search itself ───────────────────────────────────────────────────────


class TestAgenticResearch:
    @respx.mock
    async def test_sources_and_digest_come_back_with_what_they_cost(self):
        route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=reply()))

        findings = await agentic.research("mrbeast", OPUS, max_sources=8)

        assert findings.sources == ["https://example.com/a"]
        assert "Beast spent $4m" in findings.digest
        assert findings.problem == ""
        assert (findings.input_tokens, findings.output_tokens, findings.searches) == (1000, 500, 2)
        assert findings.cost_usd(OPUS) == pytest.approx(
            (1000 * 5 + 500 * 25) / 1_000_000 + 2 * agentic.SEARCH_USD
        )

        body = route.calls.last.request.content.decode()
        assert agentic.TOOL_CURRENT in body
        # The models worth routing here reject sampling parameters outright.
        assert "temperature" not in body

    @respx.mock
    async def test_an_older_model_gets_the_tool_version_it_supports(self):
        route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=reply()))
        await agentic.research("mrbeast", HAIKU)
        body = route.calls.last.request.content.decode()
        assert agentic.TOOL_LEGACY in body
        assert agentic.TOOL_CURRENT not in body

    @respx.mock
    async def test_a_paused_turn_is_resumed_rather_than_returned_half_done(self):
        """`pause_turn` is the server-side loop hitting its iteration ceiling.

        Treating it as a finished answer is the documented way to silently truncate
        research: no error, no warning, just half the sources.
        """
        pages = [
            httpx.Response(
                200,
                json=reply(text="Partial.", urls=("https://example.com/a",), stop="pause_turn"),
            ),
            httpx.Response(
                200, json=reply(text="Complete.", urls=("https://other.org/b",), stop="end_turn")
            ),
        ]
        route = respx.post(MESSAGES).mock(side_effect=pages)

        findings = await agentic.research("mrbeast", OPUS)

        assert route.call_count == 2
        assert findings.sources == ["https://example.com/a", "https://other.org/b"]
        assert "Partial." in findings.digest and "Complete." in findings.digest
        # Both turns are billed.
        assert findings.input_tokens == 2000
        assert findings.searches == 4

    @respx.mock
    async def test_a_turn_that_never_unpauses_stops_instead_of_looping_forever(self):
        route = respx.post(MESSAGES).mock(
            return_value=httpx.Response(200, json=reply(stop="pause_turn"))
        )
        findings = await agentic.research("mrbeast", OPUS)
        assert route.call_count == agentic.MAX_RESUMES + 1
        assert findings.sources == ["https://example.com/a"]

    @respx.mock
    async def test_a_refusal_is_reported_as_such_not_as_an_empty_search(self):
        respx.post(MESSAGES).mock(
            return_value=httpx.Response(200, json=reply(stop="refusal", urls=()))
        )
        with pytest.raises(ProviderUnavailable, match="declined"):
            await agentic.research("mrbeast", OPUS)

    @respx.mock
    async def test_a_write_up_with_no_citations_is_distinguished_from_no_write_up(self):
        respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=reply(urls=())))
        findings = await agentic.research("mrbeast", OPUS)
        assert findings.sources == []
        assert "cited no sources" in findings.problem

    async def test_a_model_that_cannot_search_says_so_rather_than_trying(self):
        with pytest.raises(ProviderUnavailable, match="cannot search"):
            await agentic.research("mrbeast", LOCAL)

    async def test_a_missing_key_names_the_variable_and_the_alternative(self, monkeypatch):
        from engine.settings import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(ProviderUnavailable, match="ANTHROPIC_API_KEY"):
            await agentic.research("mrbeast", OPUS)


# ── URL extraction ──────────────────────────────────────────────────────────


class TestUrlExtraction:
    """Structural, because this payload is the part of the API most likely to grow.

    Losing the source list to an unrecognised block name would fail the stage on a
    search that worked.
    """

    def test_dicts_and_objects_are_both_read(self):
        class Block:
            type = "web_search_tool_result"
            content = [{"url": "https://a.test/1"}]
            citations = None

        assert agentic._urls([Block()]) == ["https://a.test/1"]
        assert agentic._urls(
            [{"type": "some_future_tool_result", "content": [{"url": "https://b.test/2"}]}]
        ) == ["https://b.test/2"]

    def test_citations_on_a_text_block_count_as_sources(self):
        blocks = [{"type": "text", "text": "x", "citations": [{"url": "https://c.test/3"}]}]
        assert agentic._urls(blocks) == ["https://c.test/3"]

    def test_anything_that_is_not_a_url_is_ignored(self):
        blocks = [
            {"type": "text", "text": "no citations here"},
            {"type": "web_search_tool_result", "content": [{"title": "no url"}]},
            {"type": "web_search_tool_result", "content": [{"url": "javascript:void(0)"}]},
        ]
        assert agentic._urls(blocks) == []


# ── the choice between the two paths ────────────────────────────────────────


class TestFindSources:
    @respx.mock
    async def test_a_searching_model_searches_and_nothing_is_scraped(self):
        respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=reply()))
        scrape = respx.post("https://lite.duckduckgo.com/lite/")

        findings = await gather.find_sources("mrbeast", OPUS)

        assert findings.sources == ["https://example.com/a"]
        assert findings.via == "web-search:claude-opus-5"
        assert not scrape.called, "scraped a search endpoint despite the model searching"

    @respx.mock
    async def test_a_model_that_cannot_search_falls_back_to_scraping(self):
        respx.post("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(200, text=RESULT_PAGE)
        )
        respx.get("https://x.test/a").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/html"}, text="<p>Scraped</p>"
            )
        )

        findings = await gather.find_sources("mrbeast", LOCAL)

        assert findings.sources == ["https://x.test/a"]
        assert findings.via == "web-scrape"

    @respx.mock
    async def test_a_failed_search_falls_back_rather_than_failing_the_render(self):
        respx.post(MESSAGES).mock(return_value=httpx.Response(500, text="upstream is down"))
        respx.post("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(200, text=RESULT_PAGE)
        )
        respx.get("https://x.test/a").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/html"}, text="<p>Scraped</p>"
            )
        )

        findings = await gather.find_sources("mrbeast", OPUS)

        assert findings.sources == ["https://x.test/a"]
        assert findings.via == "web-scrape"

    @respx.mock
    async def test_when_both_paths_fail_both_reasons_are_reported(self):
        """Fixing a key and rewriting a topic are different actions."""
        respx.post(MESSAGES).mock(return_value=httpx.Response(429, text="rate limited"))
        respx.post("https://lite.duckduckgo.com/lite/").mock(
            return_value=httpx.Response(403, text="no")
        )
        respx.post("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(403, text="no")
        )
        respx.get("https://en.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(200, json={"query": {"search": []}})
        )

        findings = await gather.find_sources("mrbeast", OPUS)

        assert findings.sources == []
        assert findings.via == "none"
        assert "claude-opus-5" in findings.problem
        assert "duckduckgo-lite" in findings.problem
        assert "wikipedia" in findings.problem
