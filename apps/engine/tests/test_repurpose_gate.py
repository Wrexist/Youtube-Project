"""The two gates, and the ways they are supposed to disagree.

The cases that matter most here are the ones where one gate passes and the other
fails — that pairing is the whole reason `rights` and `gate` are separate modules,
and a regression that blends them would look like a passing test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.repurpose.gate import (
    Corpus,
    Severity,
    Timeline,
    TimelineSegment,
    evaluate,
)
from engine.repurpose.rights import Grant, Lane, own

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def seg(start, end, source=None, narrated=False, annotated=False):
    return TimelineSegment(
        start_s=start, end_s=end, source_id=source, narrated=narrated, annotated=annotated
    )


def campaign(**kw):
    defaults = dict(
        lane=Lane.CAMPAIGN,
        grantor="@streamer",
        evidence_kind="campaign_enrolment",
        evidence_ref="https://whop.example/c/1",
        granted_at=NOW - timedelta(days=7),
        expires_at=NOW + timedelta(days=30),
    )
    return Grant(**{**defaults, **kw})


def signal(report, name):
    return next(s for s in report.transformation.signals if s.name == name)


# ── the reaction-video trap ─────────────────────────────────────────────────


def test_full_source_footage_under_narration_passes():
    """100% someone else's pixels, 100% our commentary — the format policy allows.

    A naive "share of runtime that is our own footage" scorer fails this outright,
    which is the single most expensive way this module could be wrong: it rejects
    the one format YouTube documents as monetisable, and an operator who sees that
    stops trusting the gate at all.
    """
    timeline = Timeline(
        segments=tuple(seg(i * 10, i * 10 + 10, source="clip1", narrated=True) for i in range(6)),
        cuts=14,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=10))

    assert report.publishable
    assert signal(report, "authored_share").value == 1.0
    assert signal(report, "bare_source_share").value == 0.0


def test_bare_source_with_a_topping_and_tailing_fails():
    """The actual reupload shape: our intro, their video, our outro."""
    timeline = Timeline(
        segments=(
            seg(0, 5),  # our intro
            seg(5, 65, source="clip1"),  # 60s bare lift
            seg(65, 70),  # our outro
        ),
        cuts=2,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=10))

    assert not report.publishable
    assert report.rights.cleared, "rights are fine; this is purely a transformation failure"
    assert signal(report, "longest_bare_run").severity is Severity.BLOCK
    assert signal(report, "authored_share").severity is Severity.BLOCK


def test_adjacent_bare_segments_merge_into_one_run():
    """Two 10s lifts cut back to back are a 20s lift. The cut changes nothing."""
    timeline = Timeline(
        segments=(seg(0, 10, source="a"), seg(10, 20, source="b"), seg(20, 40, narrated=True)),
        cuts=8,
        audio_bed_replaced=True,
    )
    assert timeline.longest_bare_run_s() == pytest.approx(20.0)


def test_narration_breaks_a_bare_run():
    timeline = Timeline(
        segments=(
            seg(0, 10, source="a"),
            seg(10, 14, source="a", narrated=True),
            seg(14, 24, source="a"),
        ),
    )
    assert timeline.longest_bare_run_s() == pytest.approx(10.0)


# ── the two gates disagreeing ───────────────────────────────────────────────


def test_licensed_but_lazy_is_blocked():
    """Permission does not buy transformation. The correction the research forced.

    An earlier draft gave permissioned lanes looser thresholds. YouTube's rules
    apply "regardless of whether you have permission from the original creator",
    so a licence has to buy exactly nothing here.
    """
    timeline = Timeline(
        segments=(seg(0, 60, source="clip1"),),
        cuts=0,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    grant = Grant(
        lane=Lane.LICENSED,
        grantor="@creator",
        evidence_kind="email",
        evidence_ref="storage://grants/1.eml",
        granted_at=NOW - timedelta(days=1),
    )
    report = evaluate(timeline, {"clip1": grant}, corpus=Corpus(compared_against=10))

    assert report.rights.cleared
    assert not report.transformation.passed
    assert "Blocked on originality" in report.headline()


def test_transformative_but_unlicensed_is_blocked():
    """The converse: a genuinely good edit with no right to the footage."""
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {}, corpus=Corpus(compared_against=10))

    assert report.transformation.passed
    assert not report.rights.cleared
    assert report.rights.ungranted == ("clip1",)
    assert "Blocked on rights" in report.headline()


def test_both_failing_says_so():
    timeline = Timeline(segments=(seg(0, 60, source="clip1"),), cuts=0, audio_bed_replaced=True)
    report = evaluate(timeline, {}, corpus=Corpus(compared_against=10))
    assert "rights are not cleared and the edit is not original enough" in report.headline()


# ── hard blocks ─────────────────────────────────────────────────────────────


def test_watermark_blocks_even_when_everything_else_is_perfect():
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        watermarked_sources=("clip1",),
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=10))

    assert not report.publishable
    assert signal(report, "watermark").severity is Severity.BLOCK


def test_unreplaced_audio_bed_blocks():
    """TikTok music licences cover TikTok. Video rights do not help here."""
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=False,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=10))
    assert signal(report, "audio_bed").severity is Severity.BLOCK


def test_original_video_with_no_source_does_not_need_an_audio_bed_replaced():
    """Nothing was lifted, so there is no foreign bed to replace."""
    timeline = Timeline(segments=(seg(0, 90, narrated=True),), cuts=20)
    report = evaluate(timeline, {}, corpus=Corpus(compared_against=10))
    assert signal(report, "audio_bed").severity is Severity.OK
    assert report.publishable


def test_missing_attribution_blocks_for_campaign_lane():
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        attribution_on_screen=False,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=10))
    assert signal(report, "attribution").severity is Severity.BLOCK
    assert "on screen" in signal(report, "attribution").message


def test_own_lane_needs_no_attribution():
    timeline = Timeline(
        segments=(seg(0, 30, source="mine", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
    )
    report = evaluate(timeline, {"mine": own()}, corpus=Corpus(compared_against=10))
    assert report.publishable
    assert not [s for s in report.transformation.signals if s.name == "attribution"]


# ── corpus-level checks ─────────────────────────────────────────────────────


def test_near_duplicate_of_a_recent_upload_blocks():
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(
        timeline,
        {"clip1": campaign()},
        corpus=Corpus(max_similarity=0.93, compared_against=30),
    )
    assert signal(report, "corpus_similarity").severity is Severity.BLOCK


def test_repeated_narration_template_blocks():
    """Named by policy: "dozens of videos all using the same narration or text"."""
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(
        timeline,
        {"clip1": campaign()},
        corpus=Corpus(template_repeats=4, compared_against=30),
    )
    assert signal(report, "narration_template").severity is Severity.BLOCK
    assert not report.publishable


def test_no_history_warns_rather_than_silently_passing():
    """A check that did not run is not a check that passed."""
    timeline = Timeline(
        segments=(seg(0, 30, source="clip1", narrated=True), seg(30, 90, narrated=True)),
        cuts=20,
        audio_bed_replaced=True,
        attribution_on_screen=True,
        attribution_in_description=True,
    )
    report = evaluate(timeline, {"clip1": campaign()}, corpus=Corpus(compared_against=0))
    assert signal(report, "corpus").severity is Severity.WARN
    assert report.publishable, "a warning must not block"


# ── report shape ────────────────────────────────────────────────────────────


def test_report_records_the_threshold_version():
    """Tuning is impossible if nobody knows which numbers were in force."""
    report = evaluate(Timeline(segments=(seg(0, 10),)), {})
    assert report.as_dict()["thresholds_version"] >= 1


def test_empty_timeline_is_blocked_not_perfect():
    """Zero of zero is 100% by arithmetic and 0% by common sense."""
    report = evaluate(Timeline(), {})
    assert not report.publishable
