"""Footage must sit on the beat it belongs to, and fill it.

The timeline was built by concatenating every clip end-to-end, so `_beat_spans`'
start times were computed and then thrown away. That was fine only while every
source happened to be at least as long as its slot. Stock clips are accepted from
three seconds and a beat's share of the runtime is routinely longer, so the real
behaviour was: the video finished short of the narration, every beat after the
first short one played early against the audio, and `.with_duration(total)` padded
the rest with transparent frames that encode as black.

Measured on a 10s narration with 2s sources: five seconds of black, and beat 2
playing under beat 1's narration. These tests are the fast version of that — the
full render takes minutes, so they check placement and span-filling directly.
"""

from __future__ import annotations

import pytest

from engine.render import compose


@pytest.fixture
def clip():
    from moviepy import ColorClip

    def make(duration: float, colour=(200, 40, 40)):
        return ColorClip((64, 36), color=colour, duration=duration).with_fps(24)

    return make


# ── filling a beat's span ───────────────────────────────────────────────────


def test_footage_shorter_than_its_beat_is_extended_to_fill_it(clip):
    """The bug. A 2s source in a 5s slot left 3s of nothing."""
    covered = compose._cover_span(clip(2.0), 5.0)
    assert covered.duration == pytest.approx(5.0, abs=0.05)


def test_the_extension_is_footage_not_black(clip):
    """Padding with black is what the old timeline did. Loop instead."""
    covered = compose._cover_span(clip(2.0), 5.0)
    # Sample past the source's own length: still the clip's colour, not black.
    assert covered.get_frame(4.0).mean() > 50


def test_footage_longer_than_its_beat_is_trimmed_to_it(clip):
    covered = compose._cover_span(clip(9.0), 4.0)
    assert covered.duration == pytest.approx(4.0, abs=0.05)


def test_footage_that_already_fits_is_left_alone(clip):
    covered = compose._cover_span(clip(5.0), 5.0)
    assert covered.duration == pytest.approx(5.0, abs=0.05)


def test_a_hair_short_is_not_looped(clip):
    """Floating-point noise must not trigger a loop for a few milliseconds."""
    covered = compose._cover_span(clip(4.995), 5.0)
    assert covered.duration == pytest.approx(5.0, abs=0.05)


# ── beat spans ──────────────────────────────────────────────────────────────


class _Beat:
    def __init__(self, est_seconds: float) -> None:
        self.est_seconds = est_seconds


def test_spans_are_contiguous_and_cover_the_whole_narration():
    """Any gap between spans is a gap in the video."""
    spans = compose._beat_spans([_Beat(3), _Beat(5), _Beat(2)], total=20.0)

    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(20.0, abs=0.01)
    for (_, end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert end == pytest.approx(next_start, abs=1e-9), "a gap would render as black"


def test_spans_are_proportional_to_the_estimates():
    spans = compose._beat_spans([_Beat(1), _Beat(3)], total=8.0)
    assert spans[0][1] - spans[0][0] == pytest.approx(2.0, abs=0.01)
    assert spans[1][1] - spans[1][0] == pytest.approx(6.0, abs=0.01)


def test_no_beats_still_yields_a_usable_span():
    """An empty beat list must not produce a zero-length or crashing timeline."""
    spans = compose._beat_spans([], total=12.0)
    assert spans and spans[-1][1] == pytest.approx(12.0, abs=0.01)
