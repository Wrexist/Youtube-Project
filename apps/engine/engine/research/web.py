"""Web research for script grounding.

Kept deliberately small and provider-agnostic: the script workflow only needs a
digest of source text plus the URLs it came from. Swap the search backend by
changing `_search` — everything above it is unaffected.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import parse_qs, quote, urlparse

import httpx
from loguru import logger

#: Wikimedia's User-Agent policy is enforced, not advisory, and this string is the
#: whole of what satisfies it: a name, a version, and a URL someone could actually
#: use to get in touch. The previous value said "contact via project owner", which
#: is not a contact, and every Wikipedia request came back:
#:
#:     403 Please respect our robot policy https://w.wiki/4wJS when crawling us.
#:
#: That was the *floor* under the two DuckDuckGo backends failing, so losing it
#: meant losing research entirely. Worth knowing if you are tempted to "fix" a 403
#: by pretending to be a browser: a Chrome user agent is refused here too. The
#: policy wants identification, not disguise.
#:
#: https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
USER_AGENT = "Studio/0.1 (https://github.com/Wrexist/Youtube-Project) python-httpx"

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
    query = _query(topic)

    for name, backend in (
        # Lite first: the same index, markup simple enough that it does not rot.
        ("duckduckgo-lite", _ddg_lite),
        ("duckduckgo-html", _ddg_html),
        # Not as good, but it always answers and it is genuinely citable.
        ("wikipedia", _wikipedia),
    ):
        try:
            urls = await backend(query, limit)
        except SearchBlocked as exc:
            attempts.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — one backend failing is not fatal
            attempts.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if urls:
            logger.info("research: {} returned {} source(s) for {!r}", name, len(urls), query)
            return urls, ""
        attempts.append(f"{name}: parsed 0 results")

    problem = "; ".join(attempts)
    logger.warning("research: every search backend failed for {!r} — {}", query, problem)
    return [], problem


#: The instruction someone types in front of the actual subject. The Create screen
#: asks "What's the video about?", and people answer the question they were asked —
#: "Make a video about how mrbeast overtook youtube" — so the words "make a video
#: about" were being sent to a search engine as if they were part of the topic.
#: Harmless on Wikipedia, which ranks past them; not harmless on a keyword search.
_INSTRUCTION = re.compile(
    r"""^\s*
    (?:please\s+)?
    (?:can\s+you\s+|i\s+(?:want|need)\s+(?:you\s+to\s+)?)?
    (?:make|create|write|generate|do|build)?\s*
    (?:me\s+)?(?:a|an|the)?\s*
    (?:short|video|script|youtube\s+video|explainer)?\s*
    (?:about|on|explaining|covering|regarding)\s+
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _query(topic: str) -> str:
    """The searchable subject of a topic, with any instruction stripped off.

    Conservative on purpose: it only strips when the phrase ends in an explicit
    "about"/"on"/"explaining", so a topic that merely *begins* with one of these
    verbs — "Build a shed from pallets" — is left alone. Falls back to the
    original whenever stripping would leave nothing behind.
    """
    stripped = _INSTRUCTION.sub("", topic, count=1).strip()
    return stripped or topic.strip()


class SearchBlocked(RuntimeError):
    """The endpoint answered, but with a bot check rather than with results."""


def _reject_challenge(resp: httpx.Response, name: str) -> None:
    """Tell a bot check apart from a genuinely empty result page.

    DuckDuckGo does not refuse a scraper with an error status. It returns **202**
    and a 14KB anomaly page, so `raise_for_status()` is satisfied, the regexes
    match nothing, and the backend reported "parsed 0 results" — which reads as
    "nobody has written about this topic" and sent the reader off to rephrase a
    perfectly good one. Both endpoints do it, for every user agent tried
    including a current Chrome, by GET and by POST; it is the IP being scored,
    and nothing about the request will talk it round.

    Saying so is the entire fix available here. There is no version of this that
    gets results back out of DuckDuckGo.
    """
    if resp.status_code == 202 or "anomaly" in resp.text[:4000].lower():
        raise SearchBlocked(
            f"{name} served a bot check (HTTP {resp.status_code}), not results - "
            "this IP is rate-limited or flagged"
        )


async def _ddg_lite(topic: str, limit: int) -> list[str]:
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.post("https://lite.duckduckgo.com/lite/", data={"q": topic})
        resp.raise_for_status()
    _reject_challenge(resp, "duckduckgo-lite")
    # The class first, then every link on the page: the markup is not a contract,
    # and `_unwrap` already rejects anything that is not a result destination.
    linked = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="result-link"', resp.text)
    return _pick(linked, limit) or _pick(re.findall(r'href="([^"]+)"', resp.text), limit)


async def _ddg_html(topic: str, limit: int) -> list[str]:
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": topic})
        resp.raise_for_status()
    _reject_challenge(resp, "duckduckgo-html")
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


#: Bytes read from any one source before we stop. `_strip_html` used to run on
#: `resp.text`, which materialises the whole body first — eight of those in parallel
#: with no ceiling, then truncated to 6,000 characters *after* the fact. One large
#: or deliberately hostile page took the process out.
MAX_SOURCE_BYTES = 2 * 1024 * 1024

#: Networks a research fetch has no business reaching. Search results are attacker-
#: influenced (any page that ranks), redirects are followed, and on a cloud host the
#: link-local address is a credentials endpoint.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def is_public_url(url: str) -> bool:
    """Whether a URL is safe to fetch as a research source.

    Only http(s), and never a literal private or link-local address. A *hostname*
    that resolves to one is not caught here — that needs resolution at connect time,
    which httpx does not expose a hook for. This is the cheap half; the size cap and
    the HTML-only check are what bound the damage from the rest.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        return not any(ipaddress.ip_address(parsed.hostname) in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return True  # a name, not a literal address


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    if not is_public_url(url):
        logger.debug("refusing to fetch non-public source: {}", url)
        return ""

    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        # Checked before reading a byte, and again as it streams: the header is a
        # claim, the counter is a fact.
        if "text/html" not in resp.headers.get("content-type", ""):
            return ""
        if str(resp.url) != url and not is_public_url(str(resp.url)):
            logger.debug("refusing redirect to non-public host: {}", resp.url)
            return ""

        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                logger.debug("source exceeded {} bytes; truncating: {}", MAX_SOURCE_BYTES, url)
                break
            chunks.append(chunk)

    body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    return _strip_html(body)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", " ", text)
    return re.sub(r"\s+", " ", text).strip()
