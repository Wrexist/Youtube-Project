"""Tests for `engine.genre.gaps` — demand ÷ supply over the watched corpus.

The coverage threshold is the whole scoring model: too low and every bridge
video counts against "Baltimore bridge collapse", too high and a reworded
competitor escapes the count. These tests pin where that line sits.
"""

from __future__ import annotations

from engine import repository
from engine.genre import gaps

# ── supply_count ──────────────────────────────────────────────────────────────


def test_a_reworded_title_still_counts():
    videos = [{"title": "The Collapse of Baltimore's Bridge, Explained"}]
    assert gaps.supply_count("baltimore bridge collapse", videos) == 1


def test_a_loosely_related_title_does_not():
    """One shared word out of three is 0.33 < 0.6 — bridges alone are not this topic."""
    videos = [{"title": "Every Bridge Disaster of the Decade"}]
    assert gaps.supply_count("baltimore bridge collapse", videos) == 0


def test_an_untokenizable_topic_supplies_zero_rather_than_raising():
    assert gaps.supply_count("", []) == 0
    assert gaps.supply_count("the a of", []) == 0  # stopwords only


def test_competitor_counts_is_per_candidate():
    videos = [
        {"title": "Baltimore Bridge Collapse Timeline"},
        {"title": "Why Dams Fail"},
    ]
    counts = gaps.competitor_counts(
        ["baltimore bridge collapse", "dam failures", "medieval sieges"], videos
    )
    assert counts["baltimore bridge collapse"] >= 1
    assert counts["medieval sieges"] == 0


# ── score_gaps ────────────────────────────────────────────────────────────────


async def test_open_topics_rank_above_saturated_ones(monkeypatch):
    corpus = [
        {"title": "Baltimore Bridge Collapse: What We Know"},
        {"title": "Baltimore Bridge Collapse — One Year Later"},
    ]

    async def fake_corpus():
        return corpus

    monkeypatch.setattr(gaps, "_corpus", fake_corpus)

    scored = await gaps.score_gaps(
        ["baltimore bridge collapse", "medieval siege engines"],
        # One demand signal per topic — equal demand, unequal supply.
        suggestions=["baltimore bridge collapse", "medieval siege tactics"],
    )
    by_topic = {r["topic"]: r for r in scored}
    # Equal demand (one autocomplete match each), unequal supply: the open
    # topic outranks the covered one.
    open_row = by_topic["medieval siege engines"]
    saturated = by_topic["baltimore bridge collapse"]
    assert open_row["autocomplete_matches"] == 1
    assert open_row["watched_videos_on_topic"] == 0
    assert saturated["watched_videos_on_topic"] >= 2
    assert scored[0]["topic"] == "medieval siege engines"
    assert open_row["gap"] > saturated["gap"]


async def test_the_report_shows_its_components(monkeypatch):
    async def fake_corpus():
        return []

    monkeypatch.setattr(gaps, "_corpus", fake_corpus)
    [row] = await gaps.score_gaps(["bridge collapse"], suggestions=["bridge collapse why"])
    assert row["autocomplete_matches"] == 1
    assert row["watched_videos_on_topic"] == 0
    assert row["gap"] == 1.0


# ── repository round trip ────────────────────────────────────────────────────


async def test_competitor_counts_for_reads_the_real_corpus(database):
    from engine.genre.sync import sync_channel

    await repository.add_watched_channel("UC1")
    client = FakeGapClient()
    await sync_channel(client, "UC1")

    counts = await gaps.competitor_counts_for(["why dams fail"])
    assert counts["why dams fail"] == 1


class FakeGapClient:
    """One video on one channel — enough to prove the query joins on active."""

    def __init__(self):
        self.item = {
            "id": "v-dam",
            "snippet": {
                "title": "Why Dams Fail",
                "publishedAt": "2026-08-01T00:00:00Z",
            },
            "statistics": {"viewCount": "1000"},
            "contentDetails": {"duration": "PT10M"},
        }

    async def channel_details(self, channel_ids):
        return [
            {
                "id": channel_ids[0],
                "contentDetails": {"relatedPlaylists": {"uploads": "UU"}},
            }
        ]

    async def playlist_items(self, playlist_id, limit=50):
        return [{"contentDetails": {"videoId": self.item["id"]}}]

    async def video_details(self, video_ids):
        return [self.item]
