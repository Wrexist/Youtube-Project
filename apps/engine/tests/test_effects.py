"""Tests for Ken Burns ramps and transition placement.

Only the pure helpers are covered — the frame transform needs Pillow, numpy and
a real decoded frame, and asserting on resampled pixels tests Pillow rather than
us. What is tested here is the arithmetic that decides how the render looks.
"""

from __future__ import annotations

import pytest

from engine.services.effects import (
    ZOOM_MAX_SCALE,
    direction_for,
    fade_bounds,
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


# ── fades ───────────────────────────────────────────────────────────────────


def test_the_video_does_not_open_or_close_on_a_fade():
    """The hook has to land on frame one, and the end is cut to the audio."""
    assert fade_bounds(0, 4, 0.35) == (0.0, 0.35)
    assert fade_bounds(3, 4, 0.35) == (0.35, 0.0)


def test_interior_clips_fade_both_ways():
    assert fade_bounds(1, 4, 0.35) == (0.35, 0.35)


def test_a_single_clip_video_has_no_fades_at_all():
    assert fade_bounds(0, 1, 0.35) == (0.0, 0.0)


def test_a_zero_fade_setting_disables_transitions():
    assert fade_bounds(1, 4, 0.0) == (0.0, 0.0)
