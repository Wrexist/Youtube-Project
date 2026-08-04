"""Tests for retention-derived Short selection.

The test that matters most is `test_a_decaying_curve_does_not_always_pick_the_opening`.
Every naive version of this feature passes every other test here and still fails
that one, because raw `audienceWatchRatio` is highest at the start of every video
ever made.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from engine.shorts import (
    MAX_SECONDS,
    MIN_SECONDS,
    Candidate,
    detrend,
    find_candidates,
)


@dataclass
class FakeBeat:
    purpose: str
    est_seconds: float


def beats(count: int, seconds: float = 30.0) -> list[FakeBeat]:
    return [FakeBeat(purpose=f"beat {i}", est_seconds=seconds) for i in range(count)]


def decaying(n: int = 100, start: float = 100.0, end: float = 20.0) -> list[float]:
    """A plain linear decay — the shape every video's retention roughly follows."""
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def with_bump(curve: list[float], at: float, width: float, height: float) -> list[float]:
    """Add a local rise to a curve, centred at fraction `at`."""
    n = len(curve)
    centre = at * (n - 1)
    half = max(1.0, width * n / 2)
    out = list(curve)
    for i in range(n):
        distance = abs(i - centre)
        if distance < half:
            out[i] += height * (1 - distance / half)
    return out


def steep_then_flat(n: int = 100) -> list[float]:
    """Steep decay for the first half, flat for the second, with a matching bump in
    each half. The two bumps score alike on lift, so only `hold` separates them."""
    curve = [100 - 60 * (i / (n / 2)) if i < n / 2 else 40.0 for i in range(n)]
    return with_bump(with_bump(curve, 0.25, 0.08, 12.0), 0.65, 0.08, 12.0)


class TestDetrend:
    def test_a_pure_decay_detrends_to_nothing_including_at_the_edges(self):
        """Asserted over the whole array, edges included, on purpose.

        An earlier version checked only the interior, and a one-sided baseline
        window at the head of the curve produced a residual there that a decaying
        video has not earned. That is the pick-the-intro bug getting back in
        through the baseline, and an interior-only assertion cannot see it.
        """
        residuals = detrend(decaying())
        assert max(abs(r) for r in residuals) < 1e-9

    def test_a_bump_survives_detrending(self):
        residuals = detrend(with_bump(decaying(), at=0.6, width=0.1, height=15.0))
        peak = max(range(len(residuals)), key=lambda i: residuals[i])
        assert 0.55 < peak / (len(residuals) - 1) < 0.65
        assert residuals[peak] > 5.0

    def test_a_curve_too_short_to_have_a_trend_returns_zeros(self):
        assert detrend([50.0, 40.0]) == [0.0, 0.0]


class TestSelection:
    def test_a_decaying_curve_does_not_always_pick_the_opening(self):
        """The whole point. Retention is highest at 0% in every video; a bump at
        60% is where the interesting moment is, and that is what must be returned."""
        curve = with_bump(decaying(), at=0.6, width=0.12, height=20.0)
        picks = find_candidates(curve, beats(10, 30.0), duration_s=300.0)

        assert picks, "a curve with a clear rewatch spike must yield a candidate"
        best = picks[0]
        middle = best.start_s + best.duration_s / 2
        assert 0.5 < middle / 300.0 < 0.75, (
            f"best cut sits at {middle / 300.0:.0%} of the video; "
            "picking the opening means the curve was not detrended"
        )

    def test_a_flat_curve_yields_nothing(self):
        assert find_candidates([50.0] * 100, beats(10, 30.0), duration_s=300.0) == []

    def test_a_featureless_decay_yields_nothing(self):
        """No moment stands out, so there is no best moment to report."""
        assert find_candidates(decaying(), beats(10, 30.0), duration_s=300.0) == []

    def test_the_outro_is_never_offered(self):
        curve = with_bump(decaying(), at=0.97, width=0.06, height=40.0)
        for pick in find_candidates(curve, beats(20, 15.0), duration_s=300.0):
            assert pick.end_s <= 300.0 * 0.9 + 1e-6

    def test_picks_are_ranked_best_first(self):
        curve = with_bump(
            with_bump(decaying(), at=0.3, width=0.1, height=25.0),
            at=0.6,
            width=0.1,
            height=10.0,
        )
        picks = find_candidates(curve, beats(20, 15.0), duration_s=300.0)
        assert len(picks) >= 2
        assert picks == sorted(picks, key=lambda c: c.score, reverse=True)

    def test_picks_do_not_all_cover_the_same_moment(self):
        """One bump, three asked for. Every window touching that bump scores well,
        so without deduplication the answer is three near-identical cuts of the same
        twenty seconds — the three best windows, and a useless set of choices."""
        curve = with_bump(decaying(), at=0.5, width=0.15, height=25.0)
        picks = find_candidates(curve, beats(30, 10.0), duration_s=300.0, count=3)
        assert len(picks) == 1, (
            "one standout moment is one offer; three slices of it is three ways to "
            "say the same thing"
        )
        for i, a in enumerate(picks):
            for b in picks[i + 1 :]:
                overlap = min(a.end_s, b.end_s) - max(a.start_s, b.start_s)
                assert overlap <= min(a.duration_s, b.duration_s) / 3, (
                    f"{a.start_s:.0f}-{a.end_s:.0f} and {b.start_s:.0f}-{b.end_s:.0f} "
                    "are the same moment offered twice"
                )

    def test_a_window_that_bleeds_viewers_loses_to_a_weaker_one_that_holds(self):
        """Lift alone is not enough. A window can sit higher above the trend than
        any other and still be the wrong cut, because viewers are leaving all the
        way through it — a clip cannot carry a drop-off with it."""
        picks = find_candidates(steep_then_flat(), beats(20, 15.0), duration_s=300.0, count=3)
        best = picks[0]
        stronger = [p for p in picks if p.lift > best.lift]
        assert stronger, "expected a higher-lift window to also be returned"
        for other in stronger:
            assert other.hold < best.hold, (
                "a window with more lift outranked the top pick despite holding "
                "fewer viewers — hold is not affecting the ranking"
            )

    def test_retention_rising_inside_a_window_cannot_inflate_its_score(self):
        """`hold` above 1.0 means the original video was rewatched partway through
        that stretch. That is a property of the long-form timeline, not of the clip,
        and it must not lift a window above what its own rise above trend earned."""
        picks = find_candidates(steep_then_flat(), beats(20, 15.0), duration_s=300.0, count=3)
        assert any(p.hold > 1.0 for p in picks), "curve should offer a rising window"
        for pick in picks:
            assert pick.score <= pick.lift + 1e-9

    def test_count_is_respected(self):
        curve = decaying()
        for at in (0.2, 0.4, 0.6, 0.75):
            curve = with_bump(curve, at=at, width=0.05, height=20.0)
        assert len(find_candidates(curve, beats(30, 10.0), duration_s=300.0, count=2)) == 2


class TestWindows:
    def test_every_cut_is_a_usable_short_length(self):
        curve = with_bump(decaying(), at=0.5, width=0.15, height=25.0)
        picks = find_candidates(curve, beats(30, 10.0), duration_s=300.0, count=3)
        assert picks
        for pick in picks:
            assert MIN_SECONDS - 1e-6 <= pick.duration_s <= MAX_SECONDS + 1e-6

    def test_beats_longer_than_a_short_are_still_drawn_from(self):
        """Three ninety-second beats admit no run of whole beats under 60s. A video
        written that way must still produce cuts rather than silently nothing."""
        curve = with_bump(decaying(), at=0.5, width=0.15, height=25.0)
        picks = find_candidates(curve, beats(3, 90.0), duration_s=270.0)
        assert picks
        for pick in picks:
            assert pick.duration_s <= MAX_SECONDS + 1e-6

    def test_beat_estimates_are_rescaled_onto_the_real_runtime(self):
        """`est_seconds` are the script's guesses. A video whose beats sum to 100s
        but which actually runs 300s must not have every cut land in its first
        third."""
        curve = with_bump(decaying(), at=0.8, width=0.1, height=25.0)
        picks = find_candidates(curve, beats(10, 10.0), duration_s=300.0)
        assert picks
        assert max(p.end_s for p in picks) > 100.0

    def test_cuts_land_on_beat_boundaries(self):
        curve = with_bump(decaying(), at=0.5, width=0.15, height=25.0)
        picks = find_candidates(curve, beats(10, 30.0), duration_s=300.0)
        assert picks
        boundaries = {round(i * 30.0, 6) for i in range(11)}
        for pick in picks:
            assert round(pick.start_s, 6) in boundaries
            assert round(pick.end_s, 6) in boundaries

    def test_a_cut_carries_the_purpose_of_the_beat_it_starts_on(self):
        curve = with_bump(decaying(), at=0.5, width=0.15, height=25.0)
        picks = find_candidates(curve, beats(10, 30.0), duration_s=300.0)
        assert picks
        assert picks[0].label.startswith("beat ")


class TestDegenerateInput:
    @pytest.mark.parametrize(
        ("curve", "beat_list", "duration"),
        [
            ([], [FakeBeat("a", 30.0)], 300.0),
            (decaying(), [], 300.0),
            (decaying(), [FakeBeat("a", 30.0)], 0.0),
            (decaying(), [FakeBeat("a", 30.0)], -10.0),
        ],
    )
    def test_missing_input_returns_no_candidates(self, curve, beat_list, duration):
        assert find_candidates(curve, beat_list, duration) == []

    def test_beats_with_no_duration_do_not_divide_by_zero(self):
        assert find_candidates(decaying(), [FakeBeat("a", 0.0)], 300.0) == []

    def test_a_video_shorter_than_a_short_yields_nothing(self):
        curve = with_bump(decaying(), at=0.5, width=0.2, height=25.0)
        assert find_candidates(curve, beats(3, 3.0), duration_s=9.0) == []


class TestReporting:
    def test_a_candidate_serialises_the_numbers_that_chose_it(self):
        curve = with_bump(decaying(), at=0.6, width=0.12, height=20.0)
        pick = find_candidates(curve, beats(10, 30.0), duration_s=300.0)[0]
        payload = pick.as_dict()
        assert set(payload) == {
            "start_s",
            "end_s",
            "duration_s",
            "label",
            "lift",
            "hold",
            "score",
            "reason",
        }
        assert payload["duration_s"] == pytest.approx(
            payload["end_s"] - payload["start_s"], abs=0.02
        )

    def test_the_reason_names_where_in_the_video_the_cut_sits(self):
        curve = with_bump(decaying(), at=0.6, width=0.12, height=20.0)
        pick = find_candidates(curve, beats(10, 30.0), duration_s=300.0)[0]
        assert "%" in pick.reason
        assert pick.reason.endswith(".")

    def test_a_window_that_bleeds_viewers_says_so(self):
        candidate = Candidate(
            start_s=0.0,
            end_s=30.0,
            label="x",
            lift=0.2,
            hold=0.5,
            score=0.3,
            reason="",
        )
        assert candidate.duration_s == 30.0
