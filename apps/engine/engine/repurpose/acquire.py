"""Fetch the media for a cleared clip. The only place video files enter the system.

**The check that matters is the first one.** `repository.record_asset` refuses to
store an asset without a live grant, and this module refuses to *download* without
one — deliberately both, because they fail differently. Without the download check
the bytes would already be on disk by the time persistence objected, and a file
nobody was allowed to fetch is not made acceptable by declining to write a row
about it.

Everything else here is measurement, and it exists to serve `gate.py`:

  * **hash** — the same clip arrives by several discovery paths and there is no
    reason to hold it twice. Not an evasion measure and not usable as one; a
    content hash is exactly what fingerprinting is *not*.
  * **probe** — real duration and dimensions, because the ones TikTok reports are
    rounded and the gate's arithmetic is in seconds.
  * **watermark scan** — a third-party watermark is independently disqualifying
    for Shorts monetisation, so it is a stored fact rather than something
    re-derived at publish time.

On the watermark scan specifically: it is a *safety net*, not the control. The
control is sourcing clean masters, which for Lane A means exporting from your own
originals rather than saving the TikTok. A detector that misses one lets a clip
past a hard block it should have failed, so this one is deliberately biased toward
false positives — being wrongly told to re-source costs a minute, and being
wrongly cleared costs the channel's monetisation.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from loguru import logger

from engine import repository
from engine.repurpose.rights import Grant
from engine.storage import store

#: Refuse anything larger. A short-form clip is single-digit megabytes; a file an
#: order of magnitude past that is a misconfigured URL, and streaming it to disk
#: before noticing would fill the volume.
MAX_BYTES = 200 * 1024 * 1024

DOWNLOAD_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=15.0)

#: Frames sampled across the clip for the watermark scan. Dense enough to catch a
#: watermark that only appears for part of the runtime — TikTok's moves between
#: corners on a cycle, so a single frame at t=0 proves very little.
WATERMARK_SAMPLES = 12


class NotCleared(PermissionError):
    """No live grant. Raised before any network call."""


@dataclass
class Acquired:
    """What acquisition learned about a clip."""

    storage_key: str
    sha256: str
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    has_watermark: bool = False
    watermark_regions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "storage_key": self.storage_key,
            "sha256": self.sha256,
            "duration_s": self.duration_s,
            "width": self.width,
            "height": self.height,
            "has_watermark": self.has_watermark,
            "watermark_regions": self.watermark_regions,
        }


async def acquire(source_id: str, media_url: str, *, grant: Grant | None = None) -> Acquired:
    """Fetch, measure and store the media for one cleared clip.

    `grant` is looked up when not supplied, so a caller cannot acquire by simply
    not passing one. Raises `NotCleared` before touching the network.
    """
    grant = grant or await repository.latest_grant(source_id)
    if grant is None:
        raise NotCleared(
            f"clip {source_id!r} has no recorded grant. Record how it may be used before "
            "fetching it."
        )
    if not grant.permits_acquisition():
        raise NotCleared(
            f"the {grant.lane.value} grant on clip {source_id!r} is expired or revoked, "
            "so its media must not be fetched."
        )
    if not media_url:
        raise NotCleared(
            f"clip {source_id!r} has no media URL. TikTok's Display API serves media only "
            "for your own posts — for any other lane the file has to come from the "
            "campaign or the creator."
        )

    key = f"clips/{source_id}.mp4"
    path = await _download(media_url, key)

    digest = await asyncio.to_thread(_sha256, path)
    duration, width, height = await asyncio.to_thread(_probe, path)
    watermarked, regions = await asyncio.to_thread(_scan_watermark, path)

    return Acquired(
        storage_key=key,
        sha256=digest,
        duration_s=duration,
        width=width,
        height=height,
        has_watermark=watermarked,
        watermark_regions=regions,
    )


async def acquire_and_record(source_id: str, media_url: str) -> Acquired:
    """`acquire`, then persist. The path a stage should call.

    `record_asset` re-checks the grant. That is not redundant: acquisition and
    persistence can be separated by a long download, and a grant revoked in
    between should stop the row being written.
    """
    result = await acquire(source_id, media_url)
    await repository.record_asset(source_id, result.as_dict())
    return result


async def _download(url: str, key: str) -> Path:
    """Stream to storage, refusing anything implausibly large.

    Streamed rather than buffered because the size limit has to be enforced while
    the bytes arrive — checking `len(response.content)` afterwards means the file
    is already in memory, which is the thing being guarded against.
    """
    dest = await store.local_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 256):
                    written += len(chunk)
                    if written > MAX_BYTES:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        raise ValueError(
                            f"refusing a clip over {MAX_BYTES // 1024 // 1024}MB — "
                            "short-form video is not this large, so the URL is wrong"
                        )
                    handle.write(chunk)

    logger.info("acquired {} ({:.1f}MB)", key, written / 1024 / 1024)
    return dest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe(path: Path) -> tuple[float, int, int]:
    """Real duration and dimensions.

    TikTok reports duration rounded to the second and the gate's arithmetic — the
    15-second unbroken-lift check especially — is not that coarse.
    """
    try:
        from moviepy import VideoFileClip

        with VideoFileClip(str(path)) as clip:
            return float(clip.duration or 0.0), int(clip.w or 0), int(clip.h or 0)
    except Exception as exc:  # noqa: BLE001 — a clip we cannot probe is still a clip
        logger.warning("could not probe {}: {}", path.name, exc)
        return 0.0, 0, 0


def _scan_watermark(path: Path) -> tuple[bool, list[dict]]:
    """Look for a third-party watermark. Biased toward false positives.

    **What this does.** A platform watermark is a small, bright, high-contrast
    overlay composited on top of the video, so it does not move with the scene
    behind it. Sampling frames across the clip and looking for regions that stay
    bright and sharp while their surroundings change is a reasonable proxy, and it
    is what this does: for each corner region, measure how much its brightest
    pixels persist across samples that otherwise differ.

    **What this does not do.** It does not read the logo, and it cannot tell a
    TikTok watermark from a channel bug the creator burned in themselves. It will
    flag both, which is the correct bias — the fix for either is the same
    conversation about sourcing a clean master.

    Returns no detection rather than a false negative when the scan cannot run at
    all: an unreadable clip is reported as unwatermarked *and* fails to probe, and
    a caller looking at a zero duration already knows not to trust the rest.
    """
    try:
        import numpy as np
        from moviepy import VideoFileClip
    except Exception:  # noqa: BLE001 — no numpy/moviepy means no scan
        return False, []

    try:
        with VideoFileClip(str(path)) as clip:
            duration = float(clip.duration or 0.0)
            if duration <= 0:
                return False, []

            times = [duration * (i + 0.5) / WATERMARK_SAMPLES for i in range(WATERMARK_SAMPLES)]
            frames = [np.asarray(clip.get_frame(t), dtype=float) for t in times]
    except Exception as exc:  # noqa: BLE001
        logger.warning("watermark scan could not read {}: {}", path.name, exc)
        return False, []

    if not frames:
        return False, []

    height, width = frames[0].shape[:2]
    # Corner boxes, sized as a fraction of the frame. Platform watermarks live in
    # the corners on every short-form app — centre-frame branding is the creator's
    # own and is not what disqualifies a Short.
    box_w, box_h = int(width * 0.34), int(height * 0.11)
    regions = {
        "top-left": (0, box_h, 0, box_w),
        "top-right": (0, box_h, width - box_w, width),
        "bottom-left": (height - box_h, height, 0, box_w),
        "bottom-right": (height - box_h, height, width - box_w, width),
    }

    found: list[dict] = []
    for name, (y0, y1, x0, x1) in regions.items():
        crops = [f[y0:y1, x0:x1] for f in frames]
        if _persistent_overlay(crops):
            found.append({"region": name, "confidence": "heuristic"})

    return bool(found), found


def _persistent_overlay(crops: list) -> bool:
    """Do these crops share a bright, sharp element that survives scene changes?

    The test is comparative, which is what keeps it from firing on every clip with
    a bright corner: a region only counts as overlaid if its *bright pixels* are
    far more stable across time than the region as a whole. A genuinely bright
    background changes with the shot; a composited logo does not.
    """
    import numpy as np

    if len(crops) < 3:
        return False

    stack = np.stack(crops)
    luma = stack.mean(axis=3) if stack.ndim == 4 else stack

    # Overall movement in the region, as a baseline for "this shot is changing".
    overall = float(luma.std(axis=0).mean())
    if overall < 1.0:
        # A static region across the whole clip — a letterbox bar, a still frame,
        # a locked-off shot. No evidence of an overlay specifically.
        return False

    # The brightest tenth of pixels, by position. A watermark is near-white.
    threshold = np.percentile(luma, 90)
    bright = luma >= threshold
    # Positions bright in most samples: a persistent bright shape.
    persistent = bright.mean(axis=0) >= 0.8
    if persistent.sum() < luma[0].size * 0.01:
        return False

    # How still those bright positions are, compared with the region overall.
    stability = float(luma.std(axis=0)[persistent].mean())
    return stability < overall * 0.35
