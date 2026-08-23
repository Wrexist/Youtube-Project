"""Tests for `engine.genre.sync` — the watchlist sweep.

The sweep's contract is that it cannot fail loudly: one dead channel degrades
to a report with an error on it, and the other channels still arrive. The
tests use a fake client because the real one needs credentials nobody has —
the same status `providers.tiktok` records for itself.
"""

from __future__ import annotations

import pytest

from engine import repository
from engine.genre import sync


def yt_item(
    video_id: str,
    title: str,
    *,
    views: int,
    published: str,
    duration: str = "PT4M13S",
    likes: int | None = 7,
) -> dict:
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    return {
        "id": video_id,
        "snippet": {"title": title, "publishedAt": published},
        "statistics": stats,
        "contentDetails": {"duration": duration},
    }


class FakeYT:
    """The Data API as sync.py is allowed to know it: three reads, canned."""

    def __init__(
        self,
        *,
        videos: list[dict] | None = None,
        found: bool = True,
        uploads_playlist: str = "UU-uploads",
    ):
        self.videos = videos or []
        self.found = found
        self.uploads_playlist = uploads_playlist
        self.requested_channels: list[str] = []

    async def channel_details(self, channel_ids):
        self.requested_channels.extend(channel_ids)
        if not self.found:
            return []
        return [
            {
                "id": channel_ids[0],
                "contentDetails": {"relatedPlaylists": {"uploads": self.uploads_playlist}},
            }
        ]

    async def playlist_items(self, playlist_id, limit=50):
        return [{"contentDetails": {"videoId": v["id"]}} for v in self.videos]

    async def video_details(self, video_ids):
        return [v for v in self.videos if v["id"] in set(video_ids)]


# ── row parsing ──────────────────────────────────────────────────────────────


def test_hidden_counters_are_absent_keys_not_zeros():
    row = sync._row_from_item(
        yt_item("v1", "Why Dams Fail", views=1234, published="2026-08-01T00:00:00Z", likes=None),
        channel_id="UC1",
    )
    assert row["likes"] == 0
    assert row["views"] == 1234


def test_a_malformed_timestamp_loses_one_video_date_not_the_sweep():
    row = sync._row_from_item(yt_item("v1", "t", views=1, published="not-a-date"), channel_id="UC1")
    assert row["published_at"] is None


def test_iso8601_duration_becomes_seconds():
    row = sync._row_from_item(
        yt_item("v1", "t", views=1, published="2026-08-01T00:00:00Z"),
        channel_id="UC1",
    )
    assert row["duration_s"] == 253.0


# ── sync_channel / sync_all ──────────────────────────────────────────────────


async def test_a_sweep_upserts_and_reports_new_videos(database):
    await repository.add_watched_channel("UC1", label="Competitor")
    client = FakeYT(
        videos=[
            yt_item("v1", "Why Bridges Fail", views=1000, published="2026-08-01T00:00:00Z"),
            yt_item("v2", "7 Dam Disasters", views=2000, published="2026-07-01T00:00:00Z"),
        ]
    )

    report = await sync.sync_channel(client, "UC1")
    assert report["ok"] is True and report["new_videos"] == 2

    # Second sweep sees the same videos: counters refresh, nothing counts as new.
    client.videos[0]["statistics"]["viewCount"] = "5000"
    again = await sync.sync_channel(client, "UC1")
    assert again["new_videos"] == 0

    rows = {r["video_id"]: r for r in await repository.watched_videos_for_mining()}
    assert rows["v1"]["views"] == 5000
    assert rows["v2"]["title"] == "7 Dam Disasters"


async def test_first_seen_views_survive_resweeps(database):
    """Velocity is a subtraction from first sighting; an upsert must not reset it."""
    await repository.add_watched_channel("UC1")
    client = FakeYT(
        videos=[yt_item("v1", "old video", views=100, published="2026-01-01T00:00:00Z")]
    )
    await sync.sync_channel(client, "UC1")

    client.videos[0]["statistics"]["viewCount"] = "900"
    await sync.sync_channel(client, "UC1")

    rows = await repository.watched_videos_for_mining()
    assert rows[0]["first_seen_views"] == 100
    assert rows[0]["views"] == 900


async def test_a_vanished_channel_is_reported_not_swallowed(database):
    await repository.add_watched_channel("UC-gone")
    report = await sync.sync_channel(FakeYT(found=False), "UC-gone")
    assert report["ok"] is False
    assert "not found" in report["error"]

    channels = await repository.list_watched_channels()
    assert "not found" in channels[0]["last_error"]


async def test_a_raising_client_degrades_to_an_error_report(database):
    class Broken:
        async def channel_details(self, channel_ids):
            raise RuntimeError("network gone")

    report = await sync.sync_channel(Broken(), "UC1")
    assert report["ok"] is False and "network gone" in report["error"]


async def test_no_connected_client_means_an_empty_sweep():
    assert await sync.sync_all(None) == []


async def test_inactive_channels_are_skipped(database):
    await repository.add_watched_channel("UC-active")
    await repository.add_watched_channel("UC-paused")
    await repository.set_watched_channel_active("UC-paused", False)

    client = FakeYT()
    reports = await sync.sync_all(client)
    assert [r["channel_id"] for r in reports] == ["UC-active"]
    assert client.requested_channels == ["UC-active"]


@pytest.mark.usefixtures("database")
def test_sync_never_touches_media():
    """Structural guard: the genre package mines metadata only. If someone adds
    a download call to the sweep, this fails before it ships."""
    import inspect

    source = inspect.getsource(sync)
    assert "download" not in source.lower()
