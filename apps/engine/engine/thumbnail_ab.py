"""Thumbnail A/B swapping (FIX-TASKS.md E2).

Phase 8's attribution has always been able to trace a video's CTR back to its
`thumbnail_concept` (`insights.analyze`, grouped by dimension) — this is the other
half: acting on it. A published video sitting well below the channel's own median
CTR gets its live thumbnail swapped to an untried variant from the same render
(`ThumbnailStage` already generated three; `api/thumbnails.py` may have appended
more), and every swap is logged so a later analysis can segment a video's CTR
history at the swap date instead of blending pre- and post-swap clicks under one
concept label.

Two guardrails, both about *when*, not just *whether*:

  * **48 hours since publish.** YouTube's own Analytics numbers keep moving for a
    day or two after a video goes up as the initial push settles. Judging
    "underperforming" from data that young is judging noise, not the thumbnail.
  * **14 days between swaps on the same video.** A video is not a slot machine —
    swapping on a tight loop makes the CTR history unreadable (which swap produced
    which number?) and spends quota deciding on too little post-swap data to mean
    anything.

The decision logic (`should_swap`, `channel_median_ctr`, `pick_next_variant`) is
pure — no client, no database, no wall clock other than the one passed in — so it
is tested directly, the same way `insights.analyze` is. `sweep()` is the only
function that touches YouTube, the database, or `datetime.now`.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from engine.insights import VideoRecord

#: Hours since publish before a video's CTR is trusted enough to act on.
MIN_HOURS_SINCE_PUBLISH = 48

#: Days required between two swaps on the same video.
MIN_DAYS_BETWEEN_SWAPS = 14

#: A video counts as "underperforming" only below this fraction of the channel
#: median — not simply below it. A raw "< median" threshold flags roughly half of
#: every channel's videos by definition, forever, which is not a signal. This
#: margin is deliberately generous: the channel median is itself often thin (few
#: published videos), and swapping should be reserved for videos that are clearly,
#: not marginally, behind.
UNDERPERFORM_RATIO = 0.75


def channel_median_ctr(records: list[VideoRecord]) -> float:
    """The channel's own median CTR across *measured* videos.

    `ctr == 0.0` means "Analytics has not reported on this one yet" (there is a
    reporting lag), not a genuine zero — including unmeasured videos would drag
    the median toward zero as a channel published more, and would eventually flag
    the channel's *best* videos as underperforming once enough freshly-published,
    not-yet-measured ones piled up.
    """
    measured = [r.ctr for r in records if r.ctr > 0]
    if not measured:
        return 0.0
    return statistics.median(measured)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _parse_published_at(value: str) -> datetime | None:
    """`VideoRecord.published_at` is a plain string — an ISO datetime in
    production (`main.py`'s `_published_record`), sometimes a bare date in tests
    and fixtures. Both parse with `fromisoformat`; anything else is not a video
    this logic can safely act on, so it is treated as "cannot judge age" rather
    than crashing a whole sweep over one malformed row.
    """
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        logger.warning("unparseable published_at {!r}; skipping for thumbnail A/B", value)
        return None


@dataclass
class SwapDecision:
    should_swap: bool
    reason: str


def should_swap(
    record: VideoRecord,
    *,
    channel_median: float,
    last_swap_at: datetime | None,
    now: datetime | None = None,
) -> SwapDecision:
    """Whether `record` should have its thumbnail swapped right now.

    Order matters for the reason string a human reads: timing guardrails are
    checked before the performance comparison, because "too soon to tell" is a
    different, less alarming statement than "this is underperforming" and a video
    that is both should be described by the more precise one.
    """
    now = _aware(now or datetime.now(UTC))

    published = _parse_published_at(record.published_at)
    if published is None:
        return SwapDecision(False, "publish date is unknown or unparseable")

    age = now - published
    if age < timedelta(hours=MIN_HOURS_SINCE_PUBLISH):
        hours = age.total_seconds() / 3600
        return SwapDecision(
            False, f"published {hours:.0f}h ago — CTR is still provisional before 48h"
        )

    if last_swap_at is not None:
        since_swap = now - _aware(last_swap_at)
        if since_swap < timedelta(days=MIN_DAYS_BETWEEN_SWAPS):
            days = since_swap.total_seconds() / 86400
            return SwapDecision(
                False, f"swapped {days:.1f} days ago — too soon for another (14-day minimum)"
            )

    if channel_median <= 0:
        return SwapDecision(False, "no channel median yet — nothing to compare against")

    if record.ctr <= 0:
        return SwapDecision(False, "not measured yet")

    threshold = channel_median * UNDERPERFORM_RATIO
    if record.ctr >= threshold:
        return SwapDecision(
            False,
            f"ctr {record.ctr:.2f}% is within range of the {channel_median:.2f}% channel median",
        )

    return SwapDecision(
        True,
        f"ctr {record.ctr:.2f}% is well below the {channel_median:.2f}% channel median "
        f"(threshold {threshold:.2f}%)",
    )


def pick_next_variant(
    variants: list[dict],
    *,
    current_concept: str,
    tried_concepts: set[str],
) -> dict | None:
    """The next thumbnail variant to try, or `None` if there is nothing left to try.

    Prefers a variant whose template has never been the live thumbnail *and* has
    never already been swapped to (`tried_concepts` — the video's own swap
    history, so a two-variant video does not oscillate forever between the same
    two images). Falls back to any variant that merely differs from the current
    one if every other variant has already been tried, so a video is not
    permanently stuck once it has cycled through everything once. Returns `None`
    only when every variant *is* the current one — nothing to swap to at all.
    """
    untried = [
        v
        for v in variants
        if isinstance(v, dict)
        and v.get("template") != current_concept
        and v.get("template") not in tried_concepts
    ]
    if untried:
        return untried[0]

    other = [v for v in variants if isinstance(v, dict) and v.get("template") != current_concept]
    return other[0] if other else None


@dataclass
class SwapResult:
    video_id: str
    swapped: bool
    reason: str
    from_concept: str = ""
    to_concept: str = ""


async def sweep(*, now: datetime | None = None) -> list[SwapResult]:
    """Check every published video and swap the ones that qualify.

    Meant to run on a cron (`worker.py`'s `thumbnail_swap_task`), not per-request:
    it reads the channel's whole record set, so calling it from an endpoint would
    make an operator's page load pay for a decision nobody asked it to make.

    A video failing for any reason (no client, no job, no untried variant, the
    YouTube call itself failing) is recorded as `swapped=False` with a reason and
    the sweep continues — one bad row must not stop every other video from being
    considered, the same principle `review.run()` applies to its own provider calls.
    """
    from engine.api.insights import RECORDS
    from engine.api.publishing import credentials_for
    from engine.providers.youtube import YouTube
    from engine.storage import store
    from engine.workflows import video

    now = _aware(now or datetime.now(UTC))

    from engine import repository

    records = list(RECORDS.values())
    median = channel_median_ctr(records)

    results: list[SwapResult] = []
    if not records:
        return results

    creds = await credentials_for("default")
    client = YouTube(creds) if creds else None

    for record in records:
        last_swap = await repository.last_thumbnail_swap(record.video_id)
        last_swap_at = last_swap.at if last_swap else None
        decision = should_swap(record, channel_median=median, last_swap_at=last_swap_at, now=now)
        if not decision.should_swap:
            results.append(SwapResult(record.video_id, False, decision.reason))
            continue

        if client is None:
            results.append(
                SwapResult(record.video_id, False, "no channel connected — cannot set a thumbnail")
            )
            continue

        job_id = await repository.job_id_for_video(record.video_id)
        if not job_id:
            results.append(SwapResult(record.video_id, False, "no job on record for this video"))
            continue

        jobs = await repository.reload_jobs([job_id], video.get)
        job = jobs.get(job_id)
        thumbnail_state = job.get("states", {}).get("thumbnail") if job else None
        variant_list = (
            thumbnail_state.output.value
            if thumbnail_state is not None and thumbnail_state.output is not None
            else []
        )
        if not variant_list:
            results.append(SwapResult(record.video_id, False, "no thumbnail variants on record"))
            continue

        tried = {s.to_concept for s in await repository.thumbnail_swaps_for(record.video_id)}
        next_variant = pick_next_variant(
            variant_list, current_concept=record.thumbnail_concept, tried_concepts=tried
        )
        if next_variant is None:
            results.append(SwapResult(record.video_id, False, "no untried variant left"))
            continue

        try:
            path = await store.local_path(next_variant["key"])
            await client.set_thumbnail(record.video_id, path)
        except Exception as exc:  # noqa: BLE001 — one video's failure must not stop the sweep
            logger.warning("thumbnail swap failed for {}: {}", record.video_id, exc)
            results.append(SwapResult(record.video_id, False, f"YouTube call failed: {exc}"))
            continue

        to_concept = str(next_variant.get("template", ""))
        await repository.record_thumbnail_swap(
            video_id=record.video_id,
            from_concept=record.thumbnail_concept,
            to_concept=to_concept,
            variant_key=str(next_variant.get("key", "")),
            reason=decision.reason,
            at=now,
        )
        results.append(
            SwapResult(record.video_id, True, decision.reason, record.thumbnail_concept, to_concept)
        )

    return results
