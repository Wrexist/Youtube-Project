"""Clip performance, attributed.

Ten clips a day is 300 data points a month. Without somewhere to put them it is
300 videos and no knowledge, which is the difference between volume that compounds
and volume that trips the templating checks — see docs/REPURPOSE-RESEARCH.md §4.

The gate on a finding is unchanged and deliberately high: 8 videos per group,
p<0.05, and ≥8% lift before it is allowed to change a prompt. These tests are
about whether the *dimensions* reach the analyser at all, not about loosening it.
"""

from __future__ import annotations

from engine.insights import VideoRecord, analyze


def _video(video_id: str, *, ctr: float, **provenance) -> VideoRecord:
    return VideoRecord(
        video_id=video_id,
        title=f"video {video_id}",
        published_at="2026-08-01",
        ctr=ctr,
        avd_percent=50.0,
        **provenance,
    )


def _group(prefix: str, count: int, *, ctr: float, **provenance) -> list[VideoRecord]:
    return [_video(f"{prefix}{i}", ctr=ctr + i * 0.01, **provenance) for i in range(count)]


# ── the dimensions reach the analyser ───────────────────────────────────────


def test_clip_source_is_compared():
    """The most actionable dimension in campaign clipping: whose clips perform is
    a decision about where to spend tomorrow."""
    videos = [
        *_group("good", 10, ctr=8.0, clip_source="@streamer"),
        *_group("poor", 10, ctr=4.0, clip_source="@other"),
    ]

    report = analyze(videos)

    assert any(f.dimension == "clip_source" for f in report.findings)


def test_clip_lane_is_compared():
    videos = [
        *_group("own", 10, ctr=8.0, clip_lane="own"),
        *_group("camp", 10, ctr=4.0, clip_lane="campaign"),
    ]

    report = analyze(videos)

    assert any(f.dimension == "clip_lane" for f in report.findings)


def test_whether_the_hook_was_teased_is_compared():
    """Directly testable, and the retention research says it should matter."""
    videos = [
        *_group("teased", 10, ctr=8.0, hook_teased="teased"),
        *_group("plain", 10, ctr=4.0, hook_teased="in-order"),
    ]

    report = analyze(videos)

    assert any(f.dimension == "hook_teased" for f in report.findings)


# ── originals stay out of it ────────────────────────────────────────────────


def test_from_scratch_videos_do_not_form_a_group():
    """Every original video shares the value "", so a dimension including them
    would pit "no clips" against every clip strategy at once — a comparison about
    nothing. `analyze` drops under-sized groups, which handles it without a
    special case, and this pins that it stays handled."""
    videos = [
        *_group("original", 20, ctr=6.0),  # no repurpose provenance at all
    ]

    report = analyze(videos)

    assert not [f for f in report.findings if f.dimension.startswith("clip_")]


def test_a_mixed_channel_compares_only_the_repurposed_videos():
    videos = [
        *_group("original", 10, ctr=6.0),
        *_group("good", 10, ctr=9.0, clip_source="@streamer"),
        *_group("poor", 10, ctr=4.0, clip_source="@other"),
    ]

    report = analyze(videos)

    finding = next(f for f in report.findings if f.dimension == "clip_source")
    assert "@" in finding.winner
    assert "@" in finding.loser


# ── the gate is unchanged ───────────────────────────────────────────────────


def test_too_few_videos_produces_no_finding():
    """The 8-per-group floor is the whole reason this loop can be trusted."""
    videos = [
        *_group("good", 3, ctr=9.0, clip_source="@streamer"),
        *_group("poor", 3, ctr=4.0, clip_source="@other"),
    ]

    report = analyze(videos)

    assert not [f for f in report.findings if f.dimension == "clip_source"]


def test_a_difference_too_small_to_act_on_is_not_confirmed():
    videos = [
        *_group("a", 10, ctr=6.00, clip_source="@one"),
        *_group("b", 10, ctr=6.05, clip_source="@two"),
    ]

    report = analyze(videos)

    confirmed = [
        f
        for f in report.findings
        if f.dimension == "clip_source" and getattr(f, "verdict", "") == "confirmed"
    ]
    assert not confirmed


# ── a compilation is not attributable to one creator ────────────────────────


def test_several_creators_in_one_episode_are_recorded_together():
    """Silently crediting the first would make the strongest signal in the table a
    lie about which clips did the work."""
    from engine.main import _repurpose_provenance
    from engine.repurpose.rights import own
    from engine.workflows.base import Provenance, StageOutput, StageState, StageStatus
    from engine.workflows.repurpose import ClearedClips, Cuts

    states = {
        "rights": StageState(
            name="rights",
            status=StageStatus.DONE,
            output=StageOutput(
                value=ClearedClips(
                    source_ids=["a", "b"],
                    grants={"a": own(), "b": own()},
                    handles={"a": "@one", "b": "@two"},
                ),
                provenance=Provenance(),
            ),
        ),
        "segment": StageState(
            name="segment",
            status=StageStatus.DONE,
            output=StageOutput(
                value=Cuts(segments=[], hook={"teased": True}), provenance=Provenance()
            ),
        ),
    }

    provenance = _repurpose_provenance({"states": states, "inputs": {}})

    assert provenance["clip_source"] == "@one+@two"
    assert provenance["clip_lane"] == "own"
    assert provenance["hook_teased"] == "teased"


def test_a_from_scratch_job_carries_no_repurpose_provenance():
    from engine.main import _repurpose_provenance

    assert _repurpose_provenance({"states": {}, "inputs": {}}) == {}
