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

# Cross-clip fade. Long enough to register, short enough not to eat a beat.
DEFAULT_FADE_S = 0.35


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


def fade_bounds(index: int, count: int, fade_s: float) -> tuple[float, float]:
    """(fade-in, fade-out) for clip `index` of `count`.

    The first clip does not fade in and the last does not fade out — the video
    opens on the hook and ends on the audio, and fading either is a retention
    cost for no visual gain.
    """
    if fade_s <= 0 or count <= 1:
        return (0.0, 0.0)
    return (0.0 if index == 0 else fade_s, 0.0 if index == count - 1 else fade_s)


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


def apply_fades(clip, fade_in_s: float, fade_out_s: float):
    """Fade a clip in and/or out, skipping effects with a zero duration.

    A zero-length `FadeIn` is not a no-op in MoviePy 2 — it divides by the
    duration — so the guards are load-bearing.
    """
    from moviepy import vfx

    effects = []
    if fade_in_s > 0:
        effects.append(vfx.FadeIn(fade_in_s))
    if fade_out_s > 0:
        effects.append(vfx.FadeOut(fade_out_s))
    return clip.with_effects(effects) if effects else clip


def style_segment(clip, *, index: int, count: int, ken_burns: KenBurns, fade_s: float):
    """Everything a single timeline segment gets, in one call.

    Ken Burns before the fades: the fade operates on opacity and the zoom on
    geometry, and applying geometry to an already-composited fade would resample
    the blend rather than the source.
    """
    styled = apply_ken_burns(clip, direction_for(index, ken_burns))
    fade_in_s, fade_out_s = fade_bounds(index, count, fade_s)
    return apply_fades(styled, fade_in_s, fade_out_s)
