"""Segment and hook selection.

The first test is the one that matters. Short-form clips are front-loaded by
construction, so any scorer working on raw signal picks the opening of every clip
and produces a feature that has not looked at the clip at all — the same failure
`shorts.py` documents for `audienceWatchRatio`, arrived at by a different route.
"""

from __future__ import annotations

from engine.repurpose.segment import (
    HOOK_S,
    MIN_SEGMENT_S,
    choose_hook,
    choose_segment,
    detrend,
)


def _front_loaded(n=60):
    """The shape of nearly every short-form clip: loud open, decaying after."""
    return [1.0 - 0.9 * (i / n) for i in range(n)]


def _with_bump(base, at, width=8, size=0.35):
    series = list(base)
    for i in range(at, min(at + width, len(series))):
        series[i] += size
    return series


# ── the trap ────────────────────────────────────────────────────────────────


def test_a_decaying_signal_does_not_make_every_segment_the_opening():
    """The whole reason this module detrends.

    A clip that is loudest at the start and has a genuine second peak must pick
    the peak, not the intro.
    """
    energy = _with_bump(_front_loaded(), at=35)

    segment = choose_segment(energy=energy, duration_s=60, target_s=10)

    assert segment is not None
    assert segment.start_s > 20, "picked the intro despite a real peak at 35s"


def test_detrending_does_not_invent_a_residual_at_the_edges():
    """A one-sided window on a falling series pulls the baseline below the curve
    and hands the opening a lift it did not earn."""
    residual = detrend(_front_loaded())

    # The pure decay has no local structure, so nothing should stand far out.
    assert max(abs(v) for v in residual) < 0.05


def test_a_flat_clip_gets_no_confident_pick():
    """Inventing a best moment is what this module exists to avoid."""
    assert choose_segment(energy=[0.5] * 60, duration_s=60) is None


def test_pure_noise_does_not_produce_a_confident_segment():
    """A rise has to beat the clip's own wobble, not merely exist."""
    import random

    rng = random.Random(0)
    energy = [0.5 + rng.uniform(-0.02, 0.02) for _ in range(60)]

    segment = choose_segment(energy=energy, duration_s=60, target_s=10)

    assert segment is None or segment.lift >= 0.4


# ── segment selection ───────────────────────────────────────────────────────


def test_speech_outweighs_loudness():
    """An edit built from the loudest windows is a music video, not a clip with
    a point."""
    flat = [0.5] * 60
    loud_sting = _with_bump(flat, at=10, size=0.5)
    someone_talking = _with_bump(flat, at=40, size=0.5)

    segment = choose_segment(
        energy=loud_sting,
        speech=someone_talking,
        duration_s=60,
        target_s=10,
    )

    assert segment is not None
    assert segment.start_s > 25, "followed the music sting rather than the speech"


def test_a_clip_shorter_than_the_target_is_used_whole():
    segment = choose_segment(energy=[0.5, 0.6, 0.7], duration_s=3, window_s=1, target_s=20)

    assert segment is not None
    assert (segment.start_s, segment.end_s) == (0.0, 3.0)
    assert "whole" in segment.reason


def test_a_segment_never_runs_past_the_clip():
    segment = choose_segment(
        energy=_with_bump(_front_loaded(30), at=24), duration_s=30, target_s=20
    )

    if segment is not None:
        assert segment.end_s <= 30
        assert segment.duration_s >= MIN_SEGMENT_S


def test_an_empty_clip_is_declined():
    assert choose_segment(energy=[], duration_s=0) is None
    assert choose_segment(energy=[0.5] * 10, duration_s=0) is None


def test_the_reason_names_the_evidence():
    segment = choose_segment(energy=_with_bump(_front_loaded(), at=35), duration_s=60, target_s=10)
    assert segment is not None
    assert "×" in segment.reason


# ── hook selection ──────────────────────────────────────────────────────────


def test_a_late_peak_is_teased_at_the_front():
    """Viewers settle in 1.5–3s. A payoff twenty seconds in has to be shown
    first, or it is never reached."""
    hook = choose_hook(energy=_with_bump(_front_loaded(), at=40), duration_s=60)

    assert hook is not None
    assert hook.teased
    assert hook.at_s > HOOK_S
    assert "cut back" in hook.reason


def test_a_clip_already_opening_on_its_best_moment_is_not_teased():
    hook = choose_hook(energy=_with_bump([0.3] * 60, at=0), duration_s=60)

    assert hook is not None
    assert not hook.teased
    assert "already opens" in hook.reason


def test_the_hook_never_runs_past_the_clip():
    hook = choose_hook(energy=_with_bump([0.3] * 10, at=9), duration_s=10)

    assert hook is not None
    assert hook.at_s + hook.duration_s <= 10


def test_hook_and_segment_are_decided_independently():
    """Two different questions. The strongest instant and the best sustained
    stretch are not the same thing, and collapsing them loses the tease."""
    energy = _with_bump(_with_bump([0.3] * 60, at=45, width=2, size=0.9), at=15, width=12, size=0.3)

    segment = choose_segment(energy=energy, duration_s=60, target_s=12)
    hook = choose_hook(energy=energy, duration_s=60)

    assert segment is not None and hook is not None
    # The instantaneous spike at 45s is the hook; the sustained stretch at 15s is
    # the segment. If these were one decision, one of them would be wrong.
    assert hook.at_s > segment.end_s or hook.at_s < segment.start_s


def test_the_tease_decision_needs_both_series():
    """Pins the split that took three attempts to get right.

    Locating the event needs the *detrended* series; finding where that event
    begins needs the *raw* one. Either alone gets one of these two cases wrong:

      * detrended-only cannot find the onset of a *leading* strong stretch — its
        baseline sits inside the plateau, so residual there is ~0 and the clip is
        told to tease its own opening;
      * raw-only reintroduces the front-loading bias, because raw level peaks in
        the first seconds of nearly every short-form clip, so a real event at 40s
        is never teased.
    """
    opens_strong = choose_hook(energy=_with_bump([0.3] * 60, at=0), duration_s=60)
    event_late = choose_hook(energy=_with_bump(_front_loaded(), at=40), duration_s=60)

    assert opens_strong is not None and event_late is not None
    assert not opens_strong.teased, "detrended-only fails here"
    assert event_late.teased, "raw-only fails here"


def test_an_empty_clip_has_no_hook():
    assert choose_hook(energy=[], duration_s=10) is None
