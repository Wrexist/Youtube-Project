"""Web research for script grounding.

Kept deliberately small and provider-agnostic: the script workflow only needs a
digest of source text plus the URLs it came from. Swap the search backend by
changing `_search` — everything above it is unaffected.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from loguru import logger

USER_AGENT = "StudioBot/0.1 (+research; contact via project owner)"


async def research(topic: str, *, max_sources: int = 8) -> dict:
    """Return `{"digest": str, "sources": [url]}` for a topic.

    Sources that fail to fetch are dropped silently — one dead link should not fail
    the run — but if *nothing* is retrievable the caller must treat that as fatal.
    """
    urls = await _search(topic, limit=max_sources)
    if not urls:
        return {"digest": "", "sources": []}

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

    return {"digest": "\n\n".join(digest_parts), "sources": sources}


async def _search(topic: str, *, limit: int) -> list[str]:
    """Search backend.

    DuckDuckGo's HTML endpoint needs no key, which keeps first-run setup to zero
    configuration. Replace with a proper search API when volume justifies it.
    """
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.post("https://html.duckduckgo.com/html/", data={"q": topic})
            resp.raise_for_status()
        hrefs = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', resp.text)
        return _dedupe_hosts(hrefs)[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.warning("search failed for {!r}: {}", topic, exc)
        return []


def _dedupe_hosts(urls: list[str]) -> list[str]:
    """One source per host — ten pages from the same site is not eight sources."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        host = re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", url)
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
