"""The search layer under `ResearchStage`, which had no test and one backend.

Every case here is a way a render died at Research on a topic that was perfectly
researchable — reported from a real install as:

    RuntimeError: no usable sources found - refusing to generate an ungrounded script
"""

from __future__ import annotations

import httpx
import pytest
import respx

from engine.research import web

DDG_LITE = "https://lite.duckduckgo.com/lite/"
DDG_HTML = "https://html.duckduckgo.com/html/"
WIKI = "https://en.wikipedia.org/w/api.php"


def wrapped(url: str) -> str:
    """A result link in the shape DuckDuckGo actually emits."""
    from urllib.parse import quote

    return f"//duckduckgo.com/l/?uddg={quote(url, safe='')}&rut=deadbeef"


def lite_page(urls: list[str]) -> str:
    links = "".join(
        f'<a rel="nofollow" href="{wrapped(u)}" class="result-link">t</a>' for u in urls
    )
    return f"<html><body>{links}</body></html>"


# ── the collapse ────────────────────────────────────────────────────────────


def test_wrapped_results_are_unwrapped_rather_than_deduped_to_one():
    """The second half of the bug, and the reason a scheme fix alone is not enough.

    Once the href is absolute, every result shares the host `duckduckgo.com`.
    One-source-per-host is right for real hosts and catastrophic for a redirector:
    a full page of results collapses to a single URL, so one dead link is zero
    sources again. Measured: three results in, one out.
    """
    hrefs = [
        wrapped("https://example.com/a"),
        wrapped("https://another.org/b"),
        wrapped("https://third.net/c"),
    ]
    picked = web._pick(hrefs, 8)

    assert picked == [
        "https://example.com/a",
        "https://another.org/b",
        "https://third.net/c",
    ], "results were collapsed by the redirector's own host"


def test_a_scheme_relative_href_becomes_fetchable():
    """The first half, and the one that actually failed the render.

    `//duckduckgo.com/l/?uddg=…` has no scheme, so httpx raises
    `UnsupportedProtocol` for every result before a single page is read.
    """
    assert web._unwrap(wrapped("https://example.com/a")) == "https://example.com/a"
    assert web._unwrap("//example.com/plain") == "https://example.com/plain"


def test_the_search_engines_own_pages_are_not_sources():
    for href in (
        "https://duckduckgo.com/about",
        "//duckduckgo.com/y.js?ad_provider=x",
        "https://lite.duckduckgo.com/lite/",
        "/relative/path",
        "javascript:void(0)",
    ):
        assert web._unwrap(href) is None, href


def test_one_source_per_real_host_still_holds():
    picked = web._pick(
        [
            wrapped("https://example.com/a"),
            wrapped("https://www.example.com/b"),
            wrapped("https://other.com/c"),
        ],
        8,
    )
    assert picked == ["https://example.com/a", "https://other.com/c"]


# ── the fallback chain ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_lite_is_tried_first_and_html_is_not_called_when_it_works():
    respx.post(DDG_LITE).mock(
        return_value=httpx.Response(200, text=lite_page(["https://example.com/a"]))
    )
    html = respx.post(DDG_HTML)

    urls, problem = await web._search("anything", limit=8)

    assert urls == ["https://example.com/a"]
    assert problem == ""
    assert not html.called, "the second backend ran despite the first succeeding"


@pytest.mark.asyncio
@respx.mock
async def test_a_refused_scrape_falls_through_to_the_next_backend():
    """403 from one endpoint used to be the end of the whole render."""
    respx.post(DDG_LITE).mock(return_value=httpx.Response(403, text="no"))
    respx.post(DDG_HTML).mock(
        return_value=httpx.Response(
            200,
            text=f'<a class="result__a" href="{wrapped("https://example.com/a")}">t</a>',
        ),
    )

    urls, problem = await web._search("anything", limit=8)

    assert urls == ["https://example.com/a"]
    assert problem == ""


@pytest.mark.asyncio
@respx.mock
async def test_wikipedia_is_the_floor_when_both_scrapes_are_dead():
    respx.post(DDG_LITE).mock(side_effect=httpx.ConnectError("blocked"))
    respx.get(DDG_LITE).mock(side_effect=httpx.ConnectError("blocked"))
    respx.post(DDG_HTML).mock(return_value=httpx.Response(200, text="<html>changed markup</html>"))
    respx.get(DDG_HTML).mock(return_value=httpx.Response(200, text="<html>changed markup</html>"))
    respx.get(WIKI).mock(
        return_value=httpx.Response(
            200,
            json={"query": {"search": [{"title": "MrBeast"}, {"title": "YouTube history"}]}},
        )
    )

    urls, problem = await web._search("how did mrbeast take over youtube", limit=8)

    assert urls == [
        "https://en.wikipedia.org/wiki/MrBeast",
        "https://en.wikipedia.org/wiki/YouTube_history",
    ]
    assert problem == ""


@pytest.mark.asyncio
@respx.mock
async def test_a_redirected_search_is_followed_rather_than_parsed_as_empty():
    """The second reported failure: `parsed 0 results` on a well-covered topic.

    `follow_redirects` defaults to False in httpx and a 30x is not an error status, so
    `raise_for_status()` passed, the body was a redirect stub, and the regexes matched
    nothing. Both DuckDuckGo endpoints redirect a POST to their canonical host, so
    every backend reported an empty index for a query it never actually ran.
    """
    respx.post(DDG_LITE).mock(
        return_value=httpx.Response(302, headers={"location": f"{DDG_LITE}?q=x"})
    )
    respx.get(DDG_LITE).mock(
        return_value=httpx.Response(200, text=lite_page(["https://example.com/a"]))
    )

    urls, problem = await web._search("anything", limit=8)

    assert urls == ["https://example.com/a"]
    assert problem == ""


@pytest.mark.asyncio
@respx.mock
async def test_a_post_that_returns_nothing_is_retried_as_a_get():
    respx.post(DDG_LITE).mock(return_value=httpx.Response(200, text="<html>nothing</html>"))
    get = respx.get(DDG_LITE).mock(
        return_value=httpx.Response(200, text=lite_page(["https://example.com/a"]))
    )

    urls, _ = await web._search("anything", limit=8)

    assert get.called
    assert urls == ["https://example.com/a"]


# ── phrasing ────────────────────────────────────────────────────────────────


def test_a_question_is_reduced_to_the_words_a_full_text_index_can_match():
    """The reported topic, verbatim: "How did mrbeast take over youtube".

    Wikipedia's search ANDs its terms — every word has to appear in a page — so the
    interrogative scaffolding takes a well-covered subject to zero hits. `take over`
    is kept: it is part of the subject, not the question.
    """
    assert web._keywords("How did mrbeast take over youtube") == "mrbeast take over youtube"
    assert web._variants("How did mrbeast take over youtube") == [
        "How did mrbeast take over youtube",
        "mrbeast take over youtube",
    ]


def test_a_topic_that_is_already_keywords_is_searched_once():
    assert web._variants("mrbeast youtube") == ["mrbeast youtube"]


@pytest.mark.asyncio
@respx.mock
async def test_the_keyword_form_is_tried_when_the_question_finds_nothing():
    """Wikipedia answering 0 hits for the question and hits for its keywords."""
    respx.post(DDG_LITE).mock(side_effect=httpx.ConnectError("blocked"))
    respx.get(DDG_LITE).mock(side_effect=httpx.ConnectError("blocked"))
    respx.post(DDG_HTML).mock(side_effect=httpx.ConnectError("blocked"))
    respx.get(DDG_HTML).mock(side_effect=httpx.ConnectError("blocked"))

    def by_phrasing(request):
        from urllib.parse import parse_qs, urlparse

        srsearch = parse_qs(urlparse(str(request.url)).query)["srsearch"][0]
        hits = [{"title": "MrBeast"}] if srsearch == "mrbeast take over youtube" else []
        return httpx.Response(200, json={"query": {"search": hits}})

    respx.get(WIKI).mock(side_effect=by_phrasing)

    urls, problem = await web._search("How did mrbeast take over youtube", limit=8)

    assert urls == ["https://en.wikipedia.org/wiki/MrBeast"]
    assert problem == ""


@pytest.mark.asyncio
@respx.mock
async def test_the_same_backend_failing_on_both_phrasings_is_reported_once():
    """The row is truncated in the UI; six clauses for three problems reads as noise."""
    respx.post(DDG_LITE).mock(return_value=httpx.Response(403, text="no"))
    respx.post(DDG_HTML).mock(return_value=httpx.Response(403, text="no"))
    respx.get(WIKI).mock(return_value=httpx.Response(200, json={"query": {"search": []}}))

    urls, problem = await web._search("How did mrbeast take over youtube", limit=8)

    assert urls == []
    assert problem.count("duckduckgo-lite") == 1
    assert problem.count("wikipedia") == 1


@pytest.mark.asyncio
@respx.mock
async def test_when_everything_fails_the_reason_is_reported_not_swallowed():
    """The operator's actual complaint: a bare "no usable sources" names nothing."""
    respx.post(DDG_LITE).mock(return_value=httpx.Response(403, text="no"))
    respx.post(DDG_HTML).mock(return_value=httpx.Response(429, text="slow down"))
    respx.get(WIKI).mock(side_effect=httpx.ConnectTimeout("timeout"))

    urls, problem = await web._search("anything", limit=8)

    assert urls == []
    assert "duckduckgo-lite" in problem
    assert "duckduckgo-html" in problem
    assert "wikipedia" in problem


# ── end to end through research() ───────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_research_reports_fetchable_sources_and_a_digest():
    respx.post(DDG_LITE).mock(
        return_value=httpx.Response(
            200, text=lite_page(["https://example.com/a", "https://other.org/b"])
        )
    )
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body><script>junk()</script><p>Beast spent $4m</p></body></html>",
        )
    )
    respx.get("https://other.org/b").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>Second source</p>"
        )
    )

    findings = await web.research("mrbeast", max_sources=8)

    assert findings["sources"] == ["https://example.com/a", "https://other.org/b"]
    assert "Beast spent $4m" in findings["digest"]
    assert "junk()" not in findings["digest"], "script bodies are not source text"
    assert findings["problem"] == ""


@pytest.mark.asyncio
@respx.mock
async def test_search_found_things_but_none_could_be_read():
    """Distinguished from "found nothing", because the fix is a different one."""
    respx.post(DDG_LITE).mock(
        return_value=httpx.Response(200, text=lite_page(["https://example.com/a"]))
    )
    respx.get("https://example.com/a").mock(return_value=httpx.Response(500))

    findings = await web.research("mrbeast", max_sources=8)

    assert findings["sources"] == []
    assert "none of which could be fetched" in findings["problem"]
