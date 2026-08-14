"""Find clips worth making a video from, and score them for one channel.

Discovery and acquisition are separate problems and this module only does the
first. Nothing here fetches a video file: it collects *metadata* about public
posts, scores each against the channel, and writes rows. Media happens later, in
`acquire.py`, and only for clips that have picked up a grant in between.

That split is not fussiness. Metadata about a public post is free to keep
indefinitely; the file is not, and the moment the two are gathered in one step
there is no longer a point in the pipeline where a human decides which clips are
allowed to become files.

**Untrusted throughout.** Captions, hashtags and handles are written by strangers
and end up in an LLM prompt that decides what gets published under the operator's
name. Everything user-authored goes through `untrusted.fence()` before it reaches
a prompt — see `repurpose/narrate.py:write_thesis` and `write_commentary`, the
actual interpolation sites.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from engine import repository
from engine.providers import tiktok
from engine.repurpose.fit import score_clip
from engine.research import keywords

#: How many autocomplete sweeps to run per discovery pass. Each is free but not
#: instant, and they are pooled rather than run per clip — twenty clips about the
#: same niche do not need twenty separate sweeps of the same terms.
_SWEEP_SEEDS = 4


async def discover_own(
    access_token: str,
    *,
    channel_key: str,
    channel_topics: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Lane A: sweep the operator's own TikToks, score them, store them.

    Returns the scored clips. Storage is upsert-and-skip, so re-running a sweep is
    cheap and cannot resurrect a dismissal — `upsert_clip_sources` leaves known
    rows alone precisely because discovery is expected to run repeatedly over the
    same data.
    """
    clips = await tiktok.own_videos(access_token, limit=limit)
    if not clips:
        return []

    return await _score_and_store(
        clips,
        channel_key=channel_key,
        channel_topics=channel_topics or [],
        # The operator's own footage carries a standing `own` grant, so it ranks as
        # ready rather than as "no rights recorded" — which is what it is.
        cleared=True,
    )


async def _score_and_store(
    clips: list[tiktok.Clip],
    *,
    channel_key: str,
    channel_topics: list[str],
    cleared: bool,
) -> list[dict]:
    suggestions = await _pooled_suggestions(clips)

    scored: list[dict] = []
    for clip in clips:
        fit = score_clip(
            caption=clip.caption,
            hashtags=clip.hashtags,
            duration_s=clip.duration_s,
            views=clip.stats.get("views", 0),
            channel_topics=channel_topics,
            suggestions=suggestions,
            cleared=cleared,
        )
        payload = clip.as_dict()
        payload["fit_score"] = fit.score
        payload["fit_reasons"] = fit.reasons
        scored.append(payload)

    scored.sort(key=lambda c: c["fit_score"], reverse=True)

    try:
        added = await repository.upsert_clip_sources(scored, channel_key=channel_key)
        logger.info("discovery: {} clips seen, {} new", len(scored), added)
    except Exception as exc:  # noqa: BLE001 — a sweep that scored is worth returning
        logger.warning("could not store discovered clips: {}", exc)

    return scored


async def _pooled_suggestions(clips: list[tiktok.Clip]) -> list[str]:
    """One autocomplete sweep per theme, not per clip.

    Seeded from the longest captions on the assumption that they carry the most
    subject matter — a three-word caption sweeps to noise. Failures are dropped
    rather than raised: demand scoring at zero is a worse ranking, not a broken
    discovery pass.
    """
    seeds = [c.caption.strip() for c in clips if len(c.caption.strip()) > 15]
    seeds = sorted(seeds, key=len, reverse=True)[:_SWEEP_SEEDS]
    if not seeds:
        return []

    gathered = await asyncio.gather(
        *(keywords.suggest(seed, expand=False) for seed in seeds),
        return_exceptions=True,
    )

    pooled: list[str] = []
    for result in gathered:
        if isinstance(result, list):
            pooled.extend(result)
        else:
            logger.debug("autocomplete sweep failed during discovery: {}", result)
    return pooled
