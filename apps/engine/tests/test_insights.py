"""Statistics and attribution tests.

The p-values are checked against published t-distribution critical values, because a
hand-rolled incomplete beta function that is subtly wrong would silently promote
noise to "confirmed" and the loop would train on it.
"""

from __future__ import annotations

import pytest

from engine.insights import (
    MIN_PER_GROUP,
    Verdict,
    VideoRecord,
    analyze,
    map_retention_to_beats,
)
from engine.stats import summarize, two_tailed_p, welch_t_test

# ── the t-distribution itself ───────────────────────────────────────────────


def test_t_of_zero_is_certainly_not_significant():
    assert two_tailed_p(0.0, 10) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize(
    "t,df,expected",
    [
        (2.306, 8, 0.05),  # published critical value, df=8
        (2.228, 10, 0.05),  # df=10
        (2.042, 30, 0.05),  # df=30
        (1.960, 100_000, 0.05),  # converges on the normal
        (1.000, 8, 0.3466),  # mid-range check
        (3.355, 8, 0.01),  # df=8 at p=0.01
    ],
)
def test_p_values_match_published_critical_values(t, df, expected):
    assert two_tailed_p(t, df) == pytest.approx(expected, abs=0.002)


def test_welch_handles_unequal_variance_and_size():
    """Student's t assumes equal variance. A strategy used 3 times and one used 40
    times will not have comparable spread, which is why Welch is the right test."""
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 4, 5, 6]
    result = welch_t_test(a, b)
    assert result.t == pytest.approx(-1.0, abs=1e-9)
    assert result.df == pytest.approx(8.0, abs=1e-9)
    assert result.p_value == pytest.approx(0.3466, abs=0.002)


def test_tiny_samples_return_no_significance_rather_than_dividing_by_zero():
    assert welch_t_test([5.0], [3.0]).p_value == 1.0
    assert welch_t_test([], []).p_value == 1.0


def test_identical_groups_are_never_significant():
    assert welch_t_test([4.0] * 10, [4.0] * 10).p_value == 1.0


def test_confidence_interval_spans_zero_when_undecided():
    low, high = welch_t_test([1, 2, 3, 4, 5], [2, 3, 4, 5, 6]).ci95
    assert low < 0 < high


def test_summarize_uses_sample_variance():
    s = summarize([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert s.mean == pytest.approx(5.0)
    assert s.variance == pytest.approx(4.571, abs=0.01)  # n-1, not n


# ── attribution ─────────────────────────────────────────────────────────────


def videos(strategy: str, ctrs: list[float]) -> list[VideoRecord]:
    return [
        VideoRecord(
            video_id=f"{strategy}{i}",
            title=f"{strategy} {i}",
            published_at="2026-07-01",
            ctr=ctr,
            title_strategy=strategy,
        )
        for i, ctr in enumerate(ctrs)
    ]


def test_a_clear_large_difference_is_confirmed():
    records = videos("curiosity_gap", [6.0, 6.4, 6.1, 6.3, 6.2, 6.5, 6.0, 6.6]) + videos(
        "number_list", [4.0, 4.2, 3.9, 4.1, 4.0, 4.3, 3.8, 4.1]
    )
    finding = next(f for f in analyze(records).findings if f.metric == "ctr")
    assert finding.verdict is Verdict.CONFIRMED
    assert finding.winner == "curiosity_gap"
    assert finding.lift > 40


def test_small_samples_are_never_confirmed_however_large_the_gap():
    """Three videos versus three is noise, no matter how big the difference looks.
    This is the gate that stops the loop training on nothing."""
    records = videos("a", [9.0, 9.1, 9.2]) + videos("b", [2.0, 2.1, 2.2])
    report = analyze(records)
    assert report.findings == []
    assert any("needs 2 groups" in s for s in report.skipped)


def test_a_real_but_trivial_difference_is_not_confirmed():
    """Statistically detectable and worth acting on are different questions."""
    records = videos("a", [5.00, 5.02, 5.01, 5.03, 5.02, 5.01, 5.02, 5.00]) + videos(
        "b", [4.90, 4.92, 4.91, 4.93, 4.92, 4.91, 4.92, 4.90]
    )
    finding = next(f for f in analyze(records).findings if f.metric == "ctr")
    assert finding.comparison.p_value < 0.05  # real
    assert abs(finding.lift) < 8  # but tiny
    assert finding.verdict is Verdict.SUGGESTIVE


def test_noise_between_large_groups_stays_suggestive():
    """A gap big enough to look interesting, drowned in variance."""
    a = videos("a", [5.0, 6.0, 4.0, 7.0, 3.0, 5.5, 4.5, 6.5, 5.2, 4.8])  # mean 5.15
    b = videos("b", [4.6, 5.6, 3.6, 6.6, 2.6, 5.1, 4.1, 6.1, 4.8, 4.4])  # mean 4.75
    finding = next(f for f in analyze(a + b).findings if f.metric == "ctr")
    assert finding.lift > 8  # looks meaningful
    assert finding.comparison.p_value > 0.05  # but is not distinguishable from noise
    assert finding.verdict is Verdict.SUGGESTIVE


def test_exactly_tied_groups_produce_no_finding():
    a = videos("a", [5.0, 6.0, 4.0, 7.0, 3.0, 5.5, 4.5, 6.5])
    b = videos("b", [6.0, 5.0, 7.0, 4.0, 5.5, 3.0, 6.5, 4.5])
    assert [f for f in analyze(a + b).findings if f.metric == "ctr"] == []


def test_only_best_versus_worst_is_tested_per_dimension():
    """Testing every pair across 4 dimensions and 3 metrics yields dozens of
    comparisons and a couple of false positives per run at p<0.05."""
    records = (
        videos("a", [6.0] * MIN_PER_GROUP)
        + videos("b", [5.0] * MIN_PER_GROUP)
        + videos("c", [4.0] * MIN_PER_GROUP)
    )
    ctr_findings = [f for f in analyze(records).findings if f.metric == "ctr"]
    assert len(ctr_findings) == 1
    assert {ctr_findings[0].winner, ctr_findings[0].loser} == {"a", "c"}


def test_sentence_hedges_according_to_verdict():
    records = videos("a", [5.0, 5.2, 5.1, 5.3, 5.2, 5.1, 5.2, 5.0]) + videos(
        "b", [5.0, 5.2, 5.1, 5.3, 5.2, 5.1, 5.2, 5.1]
    )
    finding = next(f for f in analyze(records).findings if f.metric == "ctr")
    assert "not yet conclusive" in finding.sentence()
    assert "8 and 8 videos" in finding.sentence()


def test_skipped_dimensions_explain_themselves():
    report = analyze(videos("only_one", [5.0] * 20))
    assert report.findings == []
    assert any("title_strategy" in s for s in report.skipped)


# ── retention mapping ───────────────────────────────────────────────────────


class Beat:
    def __init__(self, purpose: str, est_seconds: float):
        self.purpose, self.est_seconds = purpose, est_seconds


def test_retention_drop_is_attributed_to_the_beat_that_caused_it():
    # Retention falls off a cliff during the middle beat.
    curve = [100, 98, 96, 94, 60, 55, 52, 50, 48, 46]
    beats = [Beat("hook", 10), Beat("first data point", 10), Beat("payoff", 10)]
    mapped = map_retention_to_beats(curve, beats, duration_s=30)

    assert len(mapped) == 3
    worst = next(b for b in mapped if b.get("worst"))
    assert worst["label"] == "first data point"


def test_drop_rate_normalises_for_beat_length():
    """A long beat should not be flagged merely for being long."""
    curve = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]  # perfectly linear
    beats = [Beat("short", 5), Beat("very long", 50)]
    mapped = map_retention_to_beats(curve, beats, duration_s=55)
    assert mapped[0]["drop_rate"] == pytest.approx(mapped[1]["drop_rate"], rel=0.3)


def test_empty_inputs_do_not_explode():
    assert map_retention_to_beats([], [], 0) == []
    assert map_retention_to_beats([100, 50], [], 10) == []


def test_completed_publish_job_seeds_feedback_record():
    """Publishing must create the provenance row that later analytics can measure."""
    from types import SimpleNamespace

    from engine.main import _published_record
    from engine.workflows.base import Provenance, StageOutput, StageState, StageStatus
    from engine.workflows.seo import TitleVariant

    def done(name: str, value, model: str | None = None) -> StageState:
        state = StageState(name=name, status=StageStatus.DONE)
        state.output = StageOutput(value=value, provenance=Provenance(model=model))
        return state

    job = {
        "workflow": SimpleNamespace(name="publish"),
        "status": "completed",
        "inputs": {"format": "long", "chosen_title_index": 1, "chosen_thumbnail_index": 0},
        "states": {
            "upload": done("upload", "yt-123"),
            "titles": done(
                "titles",
                [
                    TitleVariant(text="Backup", strategy="question"),
                    TitleVariant(text="The Real Bridge Failure", strategy="curiosity_gap"),
                ],
            ),
            "hook": done(
                "hook",
                {"chosen": 0, "variants": [{"text": "x", "device": "contradiction"}]},
            ),
            "thumbnail": done("thumbnail", [{"template": "split_reveal"}]),
            "revision": done("revision", "script", model="anthropic:claude-sonnet-5"),
        },
    }

    record = _published_record(job)

    assert record is not None
    assert record.video_id == "yt-123"
    assert record.title == "The Real Bridge Failure"
    assert record.title_strategy == "curiosity_gap"
    assert record.hook_device == "contradiction"
    assert record.thumbnail_concept == "split_reveal"
    assert record.script_model == "anthropic:claude-sonnet-5"
    assert record.format == "long"
