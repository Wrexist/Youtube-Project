"""Clip fit scoring.

Fit is a property of a clip *and a channel*, so most of these are about the same
clip scoring differently against different histories. The rest guard the two ways
this could quietly become a virality predictor: letting reach dominate, and
inventing a number where there is no evidence.
"""

from __future__ import annotations

from engine.repurpose.fit import (
    MAX_USABLE_S,
    REACH_SATURATION,
    score_clip,
)

FINANCE = ["how compound interest actually works", "why index funds beat stock picking"]
COOKING = ["the only knife skills video you need", "why restaurant pasta tastes better"]


def test_the_same_clip_scores_differently_per_channel():
    kwargs = dict(caption="compound interest explained in 30 seconds", duration_s=30, cleared=True)

    on_topic = score_clip(**kwargs, channel_topics=FINANCE)
    off_topic = score_clip(**kwargs, channel_topics=COOKING)

    assert on_topic.adjacency > off_topic.adjacency
    assert on_topic.score > off_topic.score


def test_a_channel_with_no_history_says_so_rather_than_scoring_zero():
    """Zero adjacency for everything ranks every clip identically and silently."""
    fit = score_clip(caption="anything at all", duration_s=30, channel_topics=[])
    assert fit.adjacency == 0.0
    assert any("no published history" in r for r in fit.reasons)


def test_a_near_duplicate_of_a_published_video_is_pushed_down():
    """Ranked down here rather than blocked after a render.

    `gate.py` would refuse the finished video for corpus repetition anyway, so
    surfacing it highly only spends a render to reach the same answer.
    """
    duplicate = score_clip(
        caption="how compound interest actually works",
        duration_s=30,
        cleared=True,
        channel_topics=FINANCE,
    )
    fresh = score_clip(
        caption="why bond yields move when rates do",
        duration_s=30,
        cleared=True,
        channel_topics=FINANCE,
    )

    assert duplicate.saturation >= 0.85
    assert duplicate.score < fresh.score
    assert "near-duplicate" in duplicate.reasons[0]


def test_reach_alone_cannot_carry_a_clip():
    """The guard against this becoming a virality predictor.

    A clip with 40M views and nothing else going for it must not outrank a
    well-fitting, in-demand one — TikTok reach says little about YouTube outcome.
    """
    viral_but_irrelevant = score_clip(
        caption="cat falls off a shelf",
        duration_s=30,
        views=40_000_000,
        cleared=True,
        channel_topics=FINANCE,
    )
    relevant = score_clip(
        caption="compound interest explained simply",
        duration_s=30,
        views=1_000,
        cleared=True,
        channel_topics=FINANCE,
        suggestions=["compound interest explained", "compound interest formula"],
    )

    assert relevant.score > viral_but_irrelevant.score


def test_reach_saturates():
    a = score_clip(caption="x", duration_s=30, views=REACH_SATURATION)
    b = score_clip(caption="x", duration_s=30, views=REACH_SATURATION * 20)
    assert a.reach == b.reach == 1.0


def test_demand_counts_real_autocomplete_matches():
    fit = score_clip(
        caption="index funds explained",
        duration_s=30,
        suggestions=["index funds explained"] * 12,
    )
    assert fit.demand >= 0.5
    assert any("12 YouTube autocomplete queries match" in r for r in fit.reasons)


def test_middling_demand_is_reported_as_a_finding():
    """Saying nothing reads on the card as "demand was not measured" — the one
    thing it definitely was."""
    fit = score_clip(
        caption="index funds explained",
        duration_s=30,
        suggestions=["index funds explained", "index funds for beginners"],
    )
    assert 0 < fit.demand < 0.5
    assert any(r.startswith("only 2 YouTube autocomplete") for r in fit.reasons)


def test_no_demand_evidence_scores_zero_rather_than_guessing():
    fit = score_clip(caption="index funds explained", duration_s=30, suggestions=[])
    assert fit.demand == 0.0
    # And says nothing about demand, because nothing was measured.
    assert not any("autocomplete" in r for r in fit.reasons)


def test_nobody_searching_is_reported():
    fit = score_clip(
        caption="zzzz qqqq wwww",
        duration_s=30,
        suggestions=["something else entirely", "unrelated phrase"],
    )
    assert any("nobody searches" in r for r in fit.reasons)


def test_hashtags_count_as_subject_matter():
    """They are how TikTok's own topic model works — dropping them loses signal."""
    without = score_clip(caption="watch this", duration_s=30, channel_topics=FINANCE)
    with_tags = score_clip(
        caption="watch this",
        hashtags=["#compound", "#interest", "#investing"],
        duration_s=30,
        channel_topics=FINANCE,
    )
    assert with_tags.adjacency > without.adjacency


class TestUsability:
    def test_too_short_to_build_around(self):
        fit = score_clip(caption="x", duration_s=3, cleared=True)
        assert fit.usability == 0.0
        assert any("too short" in r for r in fit.reasons)

    def test_a_whole_video_is_not_a_clip(self):
        fit = score_clip(caption="x", duration_s=MAX_USABLE_S + 60, cleared=True)
        assert fit.usability <= 0.2
        assert any("video in its own right" in r for r in fit.reasons)

    def test_the_cuttable_middle_scores_best(self):
        middle = score_clip(caption="x", duration_s=40, cleared=True)
        edge = score_clip(caption="x", duration_s=8, cleared=True)
        assert middle.usability > edge.usability

    def test_an_uncleared_clip_ranks_below_a_ready_one(self):
        """Ranking only — nothing here decides whether a clip may be used."""
        ready = score_clip(caption="x", duration_s=40, cleared=True)
        pending = score_clip(caption="x", duration_s=40, cleared=False)

        assert pending.usability < ready.usability
        assert pending.usability > 0, "still worth surfacing — a grant is one click"
        assert "no rights recorded yet" in pending.reasons

    def test_unknown_duration_does_not_zero_the_clip(self):
        fit = score_clip(caption="x", duration_s=0, cleared=True)
        assert fit.usability > 0


def test_score_is_a_weighted_combination_not_a_max():
    """Every component pulls. A clip strong on one axis and dead on the rest
    should sit mid-table, not top."""
    one_axis = score_clip(caption="x", duration_s=40, cleared=True)
    all_axes = score_clip(
        caption="compound interest explained simply",
        duration_s=40,
        views=500_000,
        cleared=True,
        channel_topics=FINANCE,
        suggestions=["compound interest explained"] * 12,
    )
    assert all_axes.score > one_axis.score
    assert 0.0 <= one_axis.score <= 1.0
    assert 0.0 <= all_axes.score <= 1.0


def test_as_dict_carries_the_reasons_the_card_shows():
    fit = score_clip(caption="compound interest", duration_s=40, channel_topics=FINANCE)
    payload = fit.as_dict()
    assert set(payload) >= {"score", "adjacency", "demand", "reach", "usability", "reasons"}
    assert isinstance(payload["reasons"], list)
