"""Clip motion and transitions.

Ported from `vendor/moneyprinterturbo/app/services/utils/video_effects.py`,
translated from the original Chinese comments, with the slide transitions
dropped — they read as a slideshow, and nothing in our pipeline asks for them.

Why this exists at all: stock footage held static for three seconds looks like
stock footage. A slow push in is the cheapest thing that makes a cut feel shot
rather than sourced, and it costs one resample per frame.

Everything here is called from inside the render thread, so it is plain
synchronous code and imports MoviePy lazily — the pure helpers below are the
part worth unit-testing, and they must import without MoviePy installed.
"""

from __future__ import annotations

from typing import Literal

KenBurns = Literal["none", "in", "out", "alternate"]

# Upstream's 20% push, kept. Smaller is imperceptible on a 2-3s clip; larger
# starts to show source compression artefacts as it magnifies them.
ZOOM_MAX_SCALE = 1.2

# Cross-clip dissolve, when one is asked for. The default is 0 — see
# `crossfade_bounds` for why hard cuts are the right default for this format.
DEFAULT_FADE_S = 0.0


def zoom_factor(elapsed: float, duration: float, direction: str) -> float:
    """Scale factor at `elapsed` seconds into a clip of `duration`.

    Split out from the frame transform so the ramp can be tested without
    MoviePy or a real video file. Clamped at both ends: MoviePy asks for frames
    marginally past `duration` when compositing, and an unclamped ramp would
    push those past 1.2 and jump on the last frame.
    """
    span = max(duration, 1e-3)
    progress = min(max(elapsed / span, 0.0), 1.0)
    if direction == "out":
        return ZOOM_MAX_SCALE - (ZOOM_MAX_SCALE - 1.0) * progress
    return 1.0 + (ZOOM_MAX_SCALE - 1.0) * progress


def direction_for(index: int, mode: KenBurns) -> str:
    """Which way clip `index` moves.

    "alternate" is the default because a whole video pushing in the same
    direction develops a rhythm the viewer starts to anticipate.
    """
    if mode == "none":
        return "none"
    if mode == "alternate":
        return "in" if index % 2 == 0 else "out"
    return mode


def crossfade_bounds(index: int, count: int, fade_s: float) -> float:
    """Dissolve applied to the head of clip `index`, in seconds. 0 means a cut.

    This is a *cross*fade, deliberately. The obvious implementation — `FadeOut`
    on the outgoing clip and `FadeIn` on the incoming one — is wrong for
    sequential clips: they do not overlap, so the timeline dips to black at
    every seam. A real render measured luma 3.7 out of 255 at a cut. On a video
    with twenty cuts that is twenty blinks.

    The dissolve therefore goes on the incoming clip only, and the caller
    overlaps the timeline by `concat_padding` so the two clips are on screen
    together while it runs.

    The first clip never gets one — there is nothing to dissolve from, and the
    hook has to land on frame one.

    `fade_s` defaults to 0. Fast-cut faceless video uses hard cuts; a dissolve
    on every cut reads as a slideshow. Upstream defaults to no transition too.
    """
    if fade_s <= 0 or count <= 1 or index == 0:
        return 0.0
    return fade_s


def concat_padding(count: int, fade_s: float) -> float:
    """Negative padding for `concatenate_videoclips`, so dissolves overlap.

    Returns 0 for hard cuts. Otherwise `-fade_s`: each clip starts `fade_s`
    before its predecessor ends, which is the window the dissolve needs.
    """
    return -fade_s if fade_s > 0 and count > 1 else 0.0


def _zoom_frame(frame, scale: float):
    """Centre-crop and resample a frame at `scale`, without black edges.

    The crop bounds must stay floating point. Rounding them to integers makes
    the box jump by a whole pixel at different moments on each axis, and flips
    the half-pixel sampling phase whenever the size crosses odd/even — which
    reads as a visible shimmer across a slow continuous zoom. Pillow's EXTENT
    transform takes float bounds and samples sub-pixel onto a fixed output
    canvas, so both edges stay symmetric about one float centre.

    BILINEAR, not BICUBIC or LANCZOS: sharper kernels ring on high-frequency
    texture as it crosses the sampling grid, and inter-frame stability matters
    more here than single-frame sharpness.
    """
    import numpy as np
    from PIL import Image

    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    if abs(scale - 1.0) < 1e-9:
        return frame  # Skip a pointless resample that would soften frame one.

    height, width = frame.shape[:2]
    crop_w = width / scale
    crop_h = height / scale
    left = (width - crop_w) / 2
    top = (height - crop_h) / 2

    transformed = Image.fromarray(frame).transform(
        (width, height),
        Image.Transform.EXTENT,
        (left, top, left + crop_w, top + crop_h),
        resample=Image.Resampling.BILINEAR,
    )
    return np.asarray(transformed)


def apply_ken_burns(clip, direction: str):
    """Ramp the whole clip's scale. Returns `clip` unchanged for "none"."""
    if direction == "none":
        return clip

    duration = max(clip.duration or 0.0, 1e-3)

    def transform(get_frame, t: float):
        return _zoom_frame(get_frame(t), zoom_factor(t, duration, direction))

    return clip.transform(transform)


def apply_crossfade(clip, fade_s: float):
    """Dissolve the clip in over `fade_s`. A zero is a no-op.

    `CrossFadeIn`, not `FadeIn`: the former ramps the clip's *mask* so whatever
    is underneath shows through, the latter ramps toward black. With the
    negative padding from `concat_padding`, what is underneath is the previous
    clip — which is the whole point.

    The guard is load-bearing: a zero-duration effect divides by its duration.
    """
    if fade_s <= 0:
        return clip

    from moviepy import vfx

    return clip.with_effects([vfx.CrossFadeIn(fade_s)])


def style_segment(clip, *, index: int, count: int, ken_burns: KenBurns, fade_s: float):
    """Everything a single timeline segment gets, in one call.

    Ken Burns before the dissolve: the zoom is a geometry transform and the
    dissolve is a mask, and applying geometry after the mask would resample the
    blend instead of the source.
    """
    styled = apply_ken_burns(clip, direction_for(index, ken_burns))
    return apply_crossfade(styled, crossfade_bounds(index, count, fade_s))
