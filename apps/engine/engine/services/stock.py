"""Stock footage search and download.

Ported from `vendor/moneyprinterturbo/app/services/material.py`, restructured
around httpx/async and `Settings` instead of `requests` plus `config.app.get`.
Coverr and the local-file provider are not carried over — Coverr needs a key
nobody has, and local files are what `ObjectStore` is for.

Three deliberate differences from upstream:

  * **Providers fall back.** Upstream searches whichever single provider is
    configured. Here Pexels is tried first and Pixabay picks up whatever it
    could not fill, because a beat with no footage is a hole in the video and
    the second key is free.
  * **No clip is used twice.** Upstream can return the same clip for several
    beats in one video, which is the single most obvious tell that a video was
    generated. Ids are excluded across the whole render.
  * **Orientation is enforced, not requested.** Pixabay has no orientation
    parameter at all, so its results are filtered on the returned dimensions.
    Upstream compares width only, which lets a landscape clip through on a
    portrait render and then crops the subject out of frame.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from engine.settings import get_settings
from engine.storage import store

# Search returns more than we need so that duplicates and the orientation
# filter have something to discard before the count runs out.
_OVERSAMPLE = 3

_MIN_DURATION_S = 3.0


def _target_orientation(aspect: str) -> str:
    """What to ask Pexels for. It accepts landscape, portrait *and* square.

    The two-way ternary this replaces asked for landscape on a 1:1 render, while
    `_matches_orientation` then rejected anything wider than 1.45 — so every result
    of a square job was thrown away on arrival and the beat logged "no footage
    found" with a perfectly good API key. Request side and filter side have to agree.
    """
    return {"9:16": "portrait", "1:1": "square"}.get(aspect, "landscape")


def _matches_orientation(width: int, height: int, aspect: str) -> bool:
    """Is a clip of these dimensions usable for this aspect?

    Square-ish footage is accepted for both — cropping a 1:1 clip to either
    orientation keeps the centre, which is where the subject is. What this
    rejects is using landscape for portrait or the reverse, where the crop
    throws away most of the frame.
    """
    if not width or not height:
        return False
    ratio = width / height
    if aspect == "9:16":
        return ratio <= 1.05
    if aspect == "1:1":
        return 0.7 <= ratio <= 1.45
    return ratio >= 0.95


def _shorten(query: str) -> str:
    """A stock-library query, out of a sentence written for a human.

    Beat visual directions come from an LLM and read like a shot list - "wide aerial
    shot of a bustling city skyline at dusk, neon reflections in wet asphalt". Stock
    APIs match that against tags and return nothing at all, so the beat silently got
    no footage and a whole render could reach "no footage found for any beat" with a
    working API key. Three content words is what these libraries actually index on.
    """
    words = [
        w for w in re.findall(r"[a-zA-Z']+", query) if len(w) > 3 and w.lower() not in _STOPWORDS
    ]
    return " ".join(words[:3])


#: Shot-language and filler that no stock library indexes on.
_STOPWORDS = frozenset(
    {
        "shot",
        "shots",
        "wide",
        "close",
        "closeup",
        "angle",
        "view",
        "footage",
        "scene",
        "camera",
        "showing",
        "with",
        "over",
        "from",
        "into",
        "that",
        "this",
        "their",
        "there",
        "while",
        "very",
        "some",
        "then",
        "than",
    }
)


async def search(
    query: str,
    *,
    aspect: str,
    count: int,
    exclude: set[str],
    client: httpx.AsyncClient | None = None,
    fallback: str = "",
) -> list[dict]:
    """Find up to `count` clips for `query`, skipping ids in `exclude`.

    Returns `{id, url, duration, query, provider}` dicts. Never raises: a failed
    search for one beat should cost that beat its footage, not the whole render.

    Tries progressively broader queries rather than accepting nothing: the phrase as
    written, then its three strongest content words, then `fallback` (the video's
    topic). Generic footage on a beat is worth having; a gap in the timeline is not.
    """
    settings = get_settings()
    seen = set(exclude)
    results: list[dict] = []

    providers: list[tuple[str, Any]] = []
    if settings.pexels_api_key:
        providers.append(("pexels", _search_pexels))
    if settings.pixabay_api_key:
        providers.append(("pixabay", _search_pixabay))

    if not providers:
        raise RuntimeError("no stock provider configured; set PEXELS_API_KEY or PIXABAY_API_KEY")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        # Each attempt is a whole pass over the providers, and the first one to
        # return anything wins. Inside the `try`, not after it: the retry needs the
        # client, and `finally` has closed it by then when this call owns it.
        for attempt in _queries(query, fallback):
            for name, fn in providers:
                if len(results) >= count:
                    break
                try:
                    wanted = (count - len(results)) * _OVERSAMPLE
                    found = await fn(client, attempt, aspect, wanted)
                except Exception as exc:  # noqa: BLE001 — one provider must not sink the beat
                    logger.warning("{} search failed for {!r}: {}", name, attempt, exc)
                    continue
                for clip in found:
                    if clip["id"] in seen:
                        continue
                    seen.add(clip["id"])
                    results.append(clip)
                    if len(results) >= count:
                        break
            if results:
                if attempt != query:
                    logger.info("beat footage found on the broader query {!r}", attempt)
                break
            logger.warning("no footage for {!r} across {} provider(s)", attempt, len(providers))
    finally:
        if owns_client:
            await client.aclose()

    return results


def _queries(query: str, fallback: str) -> list[str]:
    """The phrase as written, then broader, then broadest — de-duplicated."""
    out: list[str] = []
    for candidate in (query, _shorten(query), fallback):
        cleaned = candidate.strip()
        if cleaned and cleaned.lower() not in {q.lower() for q in out}:
            out.append(cleaned)
    return out


async def _search_pexels(
    client: httpx.AsyncClient, query: str, aspect: str, limit: int
) -> list[dict]:
    resp = await client.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": get_settings().pexels_api_key},
        params={
            "query": query,
            "orientation": _target_orientation(aspect),
            "per_page": min(max(limit, 1), 80),
        },
    )
    resp.raise_for_status()
    return _parse_pexels(resp.json(), query, aspect)


def _parse_pexels(payload: dict, query: str, aspect: str) -> list[dict]:
    """Split out from the request so the shape can be tested without a key."""
    clips: list[dict] = []
    for video in payload.get("videos") or []:
        duration = float(video.get("duration") or 0)
        if duration < _MIN_DURATION_S:
            continue
        # Highest resolution first; downscaling to frame is free, upscaling is not.
        files = sorted(
            (f for f in video.get("video_files") or [] if f.get("width") and f.get("link")),
            key=lambda f: int(f["width"]),
            reverse=True,
        )
        best = next(
            (
                f
                for f in files
                if _matches_orientation(int(f["width"]), int(f["height"] or 0), aspect)
            ),
            None,
        )
        if best is None:
            continue
        clips.append(
            {
                "id": f"pexels-{video['id']}",
                "url": best["link"],
                "duration": duration,
                "query": query,
                "provider": "pexels",
            }
        )
    return clips


async def _search_pixabay(
    client: httpx.AsyncClient, query: str, aspect: str, limit: int
) -> list[dict]:
    resp = await client.get(
        "https://pixabay.com/api/videos/",
        params={
            "q": query,
            "video_type": "all",
            # Pixabay has no orientation filter, so the page has to be large
            # enough that the post-hoc filter still leaves usable results.
            "per_page": min(max(limit, 3), 200),
            "key": get_settings().pixabay_api_key,
        },
    )
    # Pixabay sits behind Cloudflare and answers a challenge with HTML, which
    # would otherwise surface as a confusing JSON decode error.
    if "application/json" not in resp.headers.get("content-type", ""):
        raise RuntimeError(
            f"pixabay returned {resp.headers.get('content-type', 'unknown')} "
            f"(status {resp.status_code}) — likely a Cloudflare challenge or a bad key"
        )
    # Not `raise_for_status()`. Pixabay takes its key as a *query parameter*, and
    # httpx puts the full request URL into `HTTPStatusError`'s message — so a 429 or
    # a 403 wrote `key=<the real key>` into the warning that `search()` logs. The
    # message here is deliberately URL-free, matching the RuntimeError above.
    # Nothing depends on the exception type: `search()` catches bare `Exception`.
    #
    # httpx's own "HTTP Request: GET <url>" line is a second, currently dormant copy
    # of the same leak — it is emitted at INFO on the `httpx` logger, which this
    # process does not forward, and it starts writing the key to the log the moment
    # anyone lowers the root level. That is why `providers/images.py` keeps its key
    # out of the URL entirely rather than relying on log configuration.
    if resp.status_code >= 400:
        raise RuntimeError(f"pixabay returned {resp.status_code}")
    return _parse_pixabay(resp.json(), query, aspect)


def _parse_pixabay(payload: dict, query: str, aspect: str) -> list[dict]:
    clips: list[dict] = []
    for hit in payload.get("hits") or []:
        duration = float(hit.get("duration") or 0)
        if duration < _MIN_DURATION_S:
            continue
        renditions = sorted(
            (
                v
                for v in (hit.get("videos") or {}).values()
                if v.get("url") and v.get("width") and v.get("height")
            ),
            key=lambda v: int(v["width"]),
            reverse=True,
        )
        best = next(
            (
                v
                for v in renditions
                if _matches_orientation(int(v["width"]), int(v["height"]), aspect)
            ),
            None,
        )
        if best is None:
            continue
        clips.append(
            {
                "id": f"pixabay-{hit['id']}",
                "url": best["url"],
                "duration": duration,
                "query": query,
                "provider": "pixabay",
            }
        )
    return clips


async def download_all(clips: list[dict], *, concurrency: int = 6) -> None:
    """Fetch clip files, setting `path` on each. Called by the compose step.

    Bounded concurrency rather than an unbounded `gather`: a long-form render
    can queue eighty clips, and eighty simultaneous streams get throttled by the
    provider and starve the machine of sockets.
    """
    if not clips:
        return
    limiter = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:

        async def bounded(clip: dict) -> None:
            async with limiter:
                await _download(client, clip)

        await asyncio.gather(*(bounded(c) for c in clips))


#: A stock clip is seconds of footage. Anything past this is a misbehaving provider
#: or a redirect somewhere unintended, and `resp.content` would have read all of it
#: into memory — several of these download concurrently, so one bad URL could take
#: the process out.
MAX_CLIP_BYTES = 192 * 1024 * 1024


async def _fetch_bounded(client: httpx.AsyncClient, url: str) -> bytes:
    """Download a clip, refusing one that will not fit in memory.

    Streamed rather than buffered so the limit is enforced as bytes arrive; checking
    `content-length` alone trusts a header the sender controls.
    """
    chunks: list[bytes] = []
    total = 0
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()

        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_CLIP_BYTES:
            raise ValueError(f"clip is {int(declared) // 1_048_576}MB; refusing to download")

        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > MAX_CLIP_BYTES:
                raise ValueError(f"clip exceeded {MAX_CLIP_BYTES // 1_048_576}MB; aborted")
            chunks.append(chunk)
    return b"".join(chunks)


async def _download(client: httpx.AsyncClient, clip: dict) -> None:
    # A clip that already points at a readable file needs nothing — this is the
    # resume path, and re-fetching it would undo the point of resuming.
    existing = clip.get("path")
    if existing and Path(existing).is_file():
        return

    url = clip.get("url")
    if not url:
        logger.warning("clip {} has no url; skipping", clip.get("id", "<unknown>"))
        return

    key = f"materials/{clip['id']}.mp4"
    if await store.exists(key):
        clip["path"] = str(await store.local_path(key))
        return
    try:
        clip["path"] = str(await store.put_bytes(await _fetch_bounded(client, url), key))
    except Exception as exc:  # noqa: BLE001 — compose skips clips with no path
        # Read `url` from the local, never re-index the dict: a KeyError raised
        # inside this handler escapes the gather and takes the render with it.
        logger.warning("failed to download {}: {}", url, exc)
