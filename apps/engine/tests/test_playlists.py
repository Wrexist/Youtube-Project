"""Listing the channel's playlists.

`PlaylistStage` has been able to add a video to a playlist since it was written and
skipped on every publish this project has ever done, because `playlist_id` was never
set — there was no way to learn an id short of reading it out of a YouTube URL. The
tests here are about the two ways that could stay true: an endpoint that errors
instead of degrading, and one that costs more quota than it says.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.api import channels
from engine.quota import COSTS


@pytest.fixture
def client():
    from engine.main import app

    with TestClient(app) as c:
        yield c


def test_no_connected_channel_is_an_empty_list_not_an_error(client, monkeypatch):
    """The publish screen asks for this before it knows whether it needs it. A 4xx
    would surface as a failure on a screen where nothing has failed."""

    async def none(_key="default"):
        return None

    monkeypatch.setattr(channels, "credentials_for", none)

    response = client.get("/v1/channels/playlists")

    assert response.status_code == 200
    assert response.json() == []


def test_a_broken_call_degrades_rather_than_failing_the_screen(client, monkeypatch):
    async def creds(_key="default"):
        return object()

    class Exploding:
        def __init__(self, _creds):
            pass

        async def playlists(self, limit=50):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(channels, "credentials_for", creds)
    monkeypatch.setattr(channels, "YouTube", Exploding)

    response = client.get("/v1/channels/playlists")

    assert response.status_code == 200
    assert response.json() == []


def test_it_returns_what_the_picker_needs(client, monkeypatch):
    async def creds(_key="default"):
        return object()

    class Fake:
        def __init__(self, _creds):
            pass

        async def playlists(self, limit=50):
            return [
                {"id": "PL1", "title": "Bridge failures", "count": 12},
                {"id": "PL2", "title": "Shorts", "count": 0},
            ]

    monkeypatch.setattr(channels, "credentials_for", creds)
    monkeypatch.setattr(channels, "YouTube", Fake)

    body = client.get("/v1/channels/playlists").json()

    # The count is what tells two similarly-named playlists apart in a dropdown.
    assert body == [
        {"id": "PL1", "title": "Bridge failures", "count": 12},
        {"id": "PL2", "title": "Shorts", "count": 0},
    ]


def test_listing_playlists_is_a_read_and_priced_as_one():
    """A picker that quietly costs 100 units would eat 6% of a day's budget every
    time the publish screen opened. `playlists.list` is documented at 1."""
    assert COSTS["playlists.list"] == 1
