"""Endpoint tests for `/v1/genre/*`.

The watchlist is state a human curates, so the tests are about the edges of
that curation: adding by id without a connected channel, removing what is not
there, pausing instead of deleting, and an empty corpus rendering as a normal
screen rather than an error.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ── watchlist CRUD ───────────────────────────────────────────────────────────


async def test_add_and_list_a_channel_by_id(database, client):
    response = client.post("/v1/genre/watchlist", json={"channel_id": "UCabc"})
    assert response.status_code == 200
    assert response.json()["channel"]["youtube_channel_id"] == "UCabc"

    listed = client.get("/v1/genre/watchlist").json()["channels"]
    assert [c["youtube_channel_id"] for c in listed] == ["UCabc"]
    assert listed[0]["video_count"] == 0


def test_add_requires_an_id_or_handle(client):
    assert client.post("/v1/genre/watchlist", json={}).status_code == 400


async def test_adding_by_handle_needs_a_connected_channel(client):
    """Without OAuth there is nothing to resolve a handle with — say so rather
    than pretending the handle was watched."""
    response = client.post("/v1/genre/watchlist", json={"handle": "somecreator"})
    assert response.status_code == 400
    assert "connect" in response.json()["detail"].lower()


async def test_re_adding_updates_rather_than_duplicates(database, client):
    client.post("/v1/genre/watchlist", json={"channel_id": "UCabc", "label": "old"})
    client.post("/v1/genre/watchlist", json={"channel_id": "UCabc", "label": "new"})
    channels = client.get("/v1/genre/watchlist").json()["channels"]
    assert len(channels) == 1
    assert channels[0]["label"] == "new"


async def test_remove_then_remove_again_404s(database, client):
    client.post("/v1/genre/watchlist", json={"channel_id": "UCabc"})
    assert client.delete("/v1/genre/watchlist/UCabc").status_code == 200
    assert client.delete("/v1/genre/watchlist/UCabc").status_code == 404


async def test_pause_keeps_history_visible_but_inactive(database, client):
    client.post("/v1/genre/watchlist", json={"channel_id": "UCabc"})

    paused = client.patch("/v1/genre/watchlist/UCabc", json={"active": False})
    assert paused.status_code == 200
    channels = client.get("/v1/genre/watchlist").json()["channels"]
    assert channels[0]["active"] is False

    # Pausing must not be deletion: unpausing brings it back with its row intact.
    client.patch("/v1/genre/watchlist/UCabc", json={"active": True})
    channels = client.get("/v1/genre/watchlist").json()["channels"]
    assert channels[0]["active"] is True


async def test_toggling_an_unknown_channel_404s(client):
    assert client.patch("/v1/genre/watchlist/UCnope", json={"active": True}).status_code == 404


# ── sweeps, patterns, gaps ───────────────────────────────────────────────────


async def test_sync_with_no_connected_channel_is_empty_not_broken(database, client):
    response = client.post("/v1/genre/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["channels_synced"] == 0


async def test_patterns_on_an_empty_watchlist_render_zeroed(database, client):
    body = client.get("/v1/genre/patterns").json()
    assert body["video_count"] == 0
    assert body["hook_patterns"] == []


async def test_gaps_reports_components_for_each_topic(database, client, monkeypatch):
    async def fake_suggest(topic, *, expand=False):
        return ["why bridges collapse investigation"]

    monkeypatch.setattr("engine.research.keywords.suggest", fake_suggest)
    rows = client.post("/v1/genre/gaps", json={"topics": ["bridge collapses"]}).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["topic"] == "bridge collapses"
    assert row["autocomplete_matches"] >= 0
    assert "gap" in row and "watched_videos_on_topic" in row
