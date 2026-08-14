"""Caption re-cutting and placement.

Most short-form viewing is muted, so captions are the primary text channel rather
than an accessibility nicety. The placement test is the one that matters most: the
default 0.72 sits a caption behind the platform's own UI on a vertical frame, so
it is unreadable on the app the clip was made for.
"""

from __future__ import annotations

import pytest

from engine.repurpose.captions import (
    MAX_CHARS,
    MAX_WORDS,
    MIN_SECONDS,
    SAFE_Y,
    regroup,
    safe_y,
)


def _cue(text: str, start: float = 0.0, end: float = 4.0) -> dict:
    return {"start": start, "end": end, "text": text}


# ── placement ───────────────────────────────────────────────────────────────


def test_vertical_captions_sit_above_the_platform_ui():
    """The bottom sixth of a 9:16 frame is the handle and the button rail. A
    caption at the 16:9 default is behind them."""
    assert safe_y("9:16") < safe_y("16:9")


def test_the_wide_default_is_unchanged():
    """0.72 is right for a player with no overlay, and every existing render
    depends on it."""
    assert safe_y("16:9") == 0.72


def test_an_unknown_aspect_falls_back_to_the_safest():
    """Vertical is the tightest constraint, so guessing it is the safe error."""
    assert safe_y("21:9") == SAFE_Y["9:16"]


# ── re-cutting ──────────────────────────────────────────────────────────────


def test_a_long_cue_is_split_to_the_word_budget():
    cues = regroup([_cue("one two three four five six seven eight nine ten eleven")])

    assert len(cues) > 1
    assert all(len(c["text"].split()) <= MAX_WORDS for c in cues)


def test_a_cue_already_short_enough_is_left_alone():
    cues = regroup([_cue("four short words here")])

    assert len(cues) == 1
    assert cues[0]["text"] == "four short words here"


def test_the_character_ceiling_applies_as_well_as_the_word_count():
    """Word count alone lets "internationally recognised standardisation"
    through as two words."""
    cues = regroup([_cue("internationally recognised standardisation frameworks")])

    assert all(len(c["text"]) <= MAX_CHARS + 8 for c in cues)


def test_split_timings_stay_inside_the_parent_span():
    cues = regroup([_cue("one two three four five six seven eight nine ten", 10.0, 14.0)])

    assert cues[0]["start"] == pytest.approx(10.0)
    assert cues[-1]["end"] == pytest.approx(14.0)
    assert all(c["end"] > c["start"] for c in cues)


def test_split_timings_do_not_leave_a_gap_or_an_overhang():
    """The last chunk is pinned to the parent's end rather than accumulated, so
    rounding cannot drift."""
    cues = regroup([_cue("one two three four five six seven eight", 0.0, 8.0)])

    for earlier, later in zip(cues, cues[1:], strict=False):
        assert earlier["end"] == pytest.approx(later["start"])


def test_longer_chunks_get_more_time():
    """Characters rather than words, because "a" and "extraordinarily" do not
    take the same time to say."""
    cues = regroup([_cue("a b c extraordinarily complicated pronouncement", 0.0, 10.0)])

    by_length = sorted(cues, key=lambda c: len(c["text"]))
    shortest = by_length[0]["end"] - by_length[0]["start"]
    longest = by_length[-1]["end"] - by_length[-1]["start"]
    assert longest >= shortest


def test_a_trailing_orphan_is_pulled_back():
    """ "and" alone on screen for half a second reads as a rendering fault, and it
    is the commonest artefact of a greedy split."""
    cues = regroup([_cue("one two three four five six and")])

    assert len(cues[-1]["text"].split()) >= 2


def test_cues_are_never_merged_across_a_boundary():
    """Two short adjacent cues came from a sentence end or a pause. Joining them
    puts the end of one thought and the start of the next on screen together."""
    cues = regroup([_cue("first thought", 0.0, 2.0), _cue("second thought", 2.0, 4.0)])

    assert len(cues) == 2
    assert cues[0]["text"] == "first thought"


def test_a_flash_is_widened_to_a_readable_minimum():
    """The eye does not finish a line in a quarter of a second."""
    cues = regroup([_cue("something", 0.0, 0.05)])

    assert cues[0]["end"] - cues[0]["start"] >= MIN_SECONDS


# ── junk in ─────────────────────────────────────────────────────────────────


def test_an_empty_cue_is_dropped():
    assert regroup([_cue("   ", 0.0, 2.0)]) == []


def test_a_zero_length_cue_is_dropped():
    """A caption that appears for no time is a flicker."""
    assert regroup([_cue("something", 3.0, 3.0)]) == []


def test_a_reversed_span_is_dropped():
    assert regroup([_cue("something", 5.0, 2.0)]) == []


def test_no_cues_produces_no_captions():
    assert regroup([]) == []
