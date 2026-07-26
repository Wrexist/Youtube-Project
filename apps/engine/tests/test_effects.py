"""Tests for Ken Burns ramps and transition placement.

Only the pure helpers are covered — the frame transform needs Pillow, numpy and
a real decoded frame, and asserting on resampled pixels tests Pillow rather than
us. What is tested here is the arithmetic that decides how the render looks.
"""

from __future__ import annotations

import pytest

from engine.services.effects import (
    ZOOM_MAX_SCALE,
    concat_padding,
    crossfade_bounds,
    direction_for,
    zoom_factor,
)

# ── zoom ramp ───────────────────────────────────────────────────────────────


def test_zoom_in_ramps_from_one_to_max():
    assert zoom_factor(0.0, 4.0, "in") == pytest.approx(1.0)
    assert zoom_factor(2.0, 4.0, "in") == pytest.approx(1.0 + (ZOOM_MAX_SCALE - 1) / 2)
    assert zoom_factor(4.0, 4.0, "in") == pytest.approx(ZOOM_MAX_SCALE)


def test_zoom_out_is_the_mirror_of_zoom_in():
    for t in (0.0, 1.0, 2.5, 4.0):
        assert zoom_factor(t, 4.0, "out") == pytest.approx(
            1.0 + ZOOM_MAX_SCALE - zoom_factor(t, 4.0, "in")
        )


def test_the_ramp_is_clamped_past_the_clip_end():
    """MoviePy asks for frames marginally past `duration` while compositing.

    Unclamped, those overshoot 1.2 and the last frame visibly jumps.
    """
    assert zoom_factor(5.0, 4.0, "in") == pytest.approx(ZOOM_MAX_SCALE)
    assert zoom_factor(-1.0, 4.0, "in") == pytest.approx(1.0)
    assert zoom_factor(5.0, 4.0, "out") == pytest.approx(1.0)


def test_a_zero_duration_clip_does_not_divide_by_zero():
    """A clip whose duration MoviePy reports as 0 must not blow up the render."""
    assert zoom_factor(0.0, 0.0, "in") == pytest.approx(1.0)
    assert zoom_factor(0.5, 0.0, "in") == pytest.approx(ZOOM_MAX_SCALE)


def test_the_ramp_never_shrinks_below_the_frame():
    """Any factor under 1.0 would crop outside the source and show black edges."""
    for direction in ("in", "out"):
        for step in range(21):
            assert zoom_factor(step * 0.2, 4.0, direction) >= 1.0


# ── direction ───────────────────────────────────────────────────────────────


def test_alternate_flips_every_clip():
    assert [direction_for(i, "alternate") for i in range(4)] == ["in", "out", "in", "out"]


def test_a_fixed_direction_applies_to_every_clip():
    assert [direction_for(i, "in") for i in range(3)] == ["in", "in", "in"]
    assert [direction_for(i, "out") for i in range(3)] == ["out", "out", "out"]


def test_none_disables_motion_everywhere():
    assert [direction_for(i, "none") for i in range(3)] == ["none"] * 3


# ── dissolves ───────────────────────────────────────────────────────────────


def test_the_video_does_not_open_on_a_dissolve():
    """The hook has to land on frame one."""
    assert crossfade_bounds(0, 4, 0.35) == 0.0


def test_every_later_clip_dissolves_in():
    assert [crossfade_bounds(i, 4, 0.35) for i in range(4)] == [0.0, 0.35, 0.35, 0.35]


def test_a_single_clip_video_has_no_dissolve():
    assert crossfade_bounds(0, 1, 0.35) == 0.0


def test_a_zero_setting_means_hard_cuts():
    assert crossfade_bounds(1, 4, 0.0) == 0.0
    assert concat_padding(4, 0.0) == 0.0


def test_hard_cuts_are_the_default():
    """A dissolve on every cut reads as a slideshow, so 0 is the shipped value."""
    from engine.services.effects import DEFAULT_FADE_S
    from engine.settings import Settings

    assert DEFAULT_FADE_S == 0.0
    assert Settings().transition_fade_s == 0.0


def test_a_dissolve_overlaps_the_timeline_rather_than_dipping_to_black():
    """The regression this whole API shape exists for.

    FadeOut-then-FadeIn on sequential clips does not overlap them, so the
    timeline goes to black at every seam — a real render measured luma 3.7/255
    at a cut. Negative padding is what puts both clips on screen together.
    """
    fade = 0.35
    assert concat_padding(4, fade) == pytest.approx(-fade)
    # The dissolve is on the incoming clip only; the outgoing one is untouched,
    # so nothing ever ramps toward black.
    assert crossfade_bounds(1, 4, fade) == pytest.approx(fade)


def test_padding_is_zero_when_there_is_nothing_to_overlap():
    assert concat_padding(1, 0.35) == 0.0
