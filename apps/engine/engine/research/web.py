"""Web research for script grounding.

Kept deliberately small and provider-agnostic: the script workflow only needs a
digest of source text plus the URLs it came from. Swap the search backend by
changing `_search` — everything above it is unaffected.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, quote, urlparse

import httpx
from loguru import logger

USER_AGENT = "StudioBot/0.1 (+research; contact via project owner)"

#: Hosts that are never a source: the search engine's own pages, and the ad and
#: redirect domains its result lists carry.
_NOT_SOURCES = frozenset(
    {"duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com", "duck.com", "bing.com"}
)


async def research(topic: str, *, max_sources: int = 8) -> dict:
    """Return `{"digest": str, "sources": [url], "problem": str}` for a topic.

    Sources that fail to fetch are dropped silently — one dead link should not fail
    the run — but if *nothing* is retrievable the caller must treat that as fatal.
    `problem` carries why, because "no usable sources found" on its own sends
    someone looking at their topic when the actual answer is that a search backend
    changed its markup or refused the request.
    """
    urls, problem = await _search(topic, limit=max_sources)
    if not urls:
        return {"digest": "", "sources": [], "problem": problem}

    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        pages = await asyncio.gather(*(_fetch(client, url) for url in urls), return_exceptions=True)

    digest_parts: list[str] = []
    sources: list[str] = []
    for url, page in zip(urls, pages, strict=True):
        if isinstance(page, Exception) or not page:
            logger.debug("source unusable: {}", url)
            continue
        sources.append(url)
        digest_parts.append(f"--- {url} ---\n{page[:6000]}")

    if not sources:
        return {
            "digest": "",
            "sources": [],
            "problem": f"search returned {len(urls)} result(s), none of which could be fetched",
        }

    return {"digest": "\n\n".join(digest_parts), "sources": sources, "problem": ""}


async def _search(topic: str, *, limit: int) -> tuple[list[str], str]:
    """URLs to read, and — when there are none — what went wrong trying.

    Three keyless backends in order of quality, because a single scraped endpoint is
    not a dependency worth failing a whole render on. DuckDuckGo's markup is not a
    contract and changes without notice; Wikipedia's API is, which is why it is last
    rather than absent. Nothing here needs a key: the first run of a fresh clone has
    to be able to research something.
    """
    attempts: list[str] = []

    for name, backend in (
        # Lite first: the same index, markup simple enough that it does not rot.
        ("duckduckgo-lite", _ddg_lite),
        ("duckduckgo-html", _ddg_html),
        # Not as good, but it always answers and it is genuinely citable.
        ("wikipedia", _wikipedia),
    ):
        try:
            urls = await backend(topic, limit)
        except Exception as exc:  # noqa: BLE001 — one backend failing is not fatal
            attempts.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if urls:
            logger.info("research: {} returned {} source(s) for {!r}", name, len(urls), topic)
            return urls, ""
        attempts.append(f"{name}: parsed 0 results")

    problem = "; ".join(attempts)
    logger.warning("research: every search backend failed for {!r} — {}", topic, problem)
    return [], problem


async def _ddg_lite(topic: str, limit: int) -> list[str]:
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.post("https://lite.duckduckgo.com/lite/", data={"q": topic})
        resp.raise_for_status()
    # The class first, then every link on the page: the markup is not a contract,
    # and `_unwrap` already rejects anything that is not a result destination.
    linked = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="result-link"', resp.text)
    return _pick(linked, limit) or _pick(re.findall(r'href="([^"]+)"', resp.text), limit)


async def _ddg_html(topic: str, limit: int) -> list[str]:
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": topic})
        resp.raise_for_status()
    linked = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', resp.text)
    return _pick(linked, limit) or _pick(re.findall(r'href="([^"]+)"', resp.text), limit)


async def _wikipedia(topic: str, limit: int) -> list[str]:
    """The floor under the other two. Keyless, stable, and a real citation."""
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": topic,
                "srlimit": limit,
                "format": "json",
            },
        )
        resp.raise_for_status()
        hits = resp.json().get("query", {}).get("search", [])
    return [
        f"https://en.wikipedia.org/wiki/{quote(hit['title'].replace(' ', '_'))}"
        for hit in hits
        if hit.get("title")
    ][:limit]


def _pick(hrefs: list[str], limit: int) -> list[str]:
    """Result links only, unwrapped, absolute, one per host."""
    return _dedupe_hosts([url for url in (_unwrap(h) for h in hrefs) if url])[:limit]


def _unwrap(href: str) -> str | None:
    """The real destination behind a search result link, or None if it is not one.

    DuckDuckGo does not link results directly. It emits its own redirector, and
    scheme-relative at that:

        //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx&rut=…

    Handed straight to httpx, every one of those raises `UnsupportedProtocol:
    Request URL is missing an 'http://' or 'https://' protocol` — so *all* sources
    failed to fetch, and the render died at Research with "no usable sources found"
    on a topic with plenty of coverage. That was the reported failure.

    Prepending the scheme alone does not fix it: the hrefs then all share the host
    `duckduckgo.com`, and one-source-per-host collapses a full page of results to a
    single URL, so one dead link is again zero sources. The destination has to come
    out of `uddg` *before* the host rule sees it.
    """
    if href.startswith("//"):
        href = f"https:{href}"

    if "uddg=" in href:
        target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        href = target or href

    if not href.startswith(("http://", "https://")):
        return None
    host = _host(href)
    if not host or host in _NOT_SOURCES:
        return None
    return href


def _host(url: str) -> str:
    return re.sub(r"^www\.", "", urlparse(url).netloc.lower())


def _dedupe_hosts(urls: list[str]) -> list[str]:
    """One source per host — ten pages from the same site is not eight sources."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        host = _host(url)
        if host in seen:
            continue
        seen.add(host)
        out.append(url)
    return out


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    resp.raise_for_status()
    if "text/html" not in resp.headers.get("content-type", ""):
        return ""
    return _strip_html(resp.text)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", " ", text)
    return re.sub(r"\s+", " ", text).strip()
