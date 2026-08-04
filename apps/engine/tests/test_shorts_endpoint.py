"""Tests for `GET /v1/analytics/shorts/{video_id}` and the runtime lookup behind it.

The endpoint's job is mostly to *not* guess: at four separate points it has less
information than it needs, and each one has a right answer that is not "return
something plausible". Those four are what is asserted here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import insights as insights_api
from engine.main import app
from engine.providers.youtube import _parse_iso8601_duration


class FakeBeat:
    def __init__(self, purpose: str, est_seconds: float) -> None:
        self.purpose = purpose
        self.est_seconds = est_seconds


class Record:
    """Stands in for a `VideoRecord` that has beats attached."""

    def __init__(self, beats: list | None) -> None:
        self.video_id = "vid1"
        self.beats = beats if beats is not None else []
        self.avd_seconds = 42.0


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def clean_records():
    insights_api.RECORDS.clear()
    yield
    insights_api.RECORDS.clear()


@pytest.fixture(autouse=True)
def no_channel(monkeypatch):
    monkeypatch.setattr(insights_api, "CHANNELS", {}, raising=False)


def connect(monkeypatch, *, curve, duration):
    """Wire a fake channel whose retention curve and runtime are ours to choose."""
    monkeypatch.setattr(insights_api, "CHANNELS", {"default": object()}, raising=False)

    class FakeYouTube:
        def __init__(self, creds):
            pass

        async def duration_seconds(self, video_id):
            return duration

    class FakeAnalytics:
        def __init__(self, creds):
            pass

        async def retention(self, video_id):
            return curve

    monkeypatch.setattr(insights_api.youtube, "YouTube", FakeYouTube)
    monkeypatch.setattr(insights_api, "Analytics", FakeAnalytics)


def decaying(n=100, start=100.0, end=20.0):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def with_bump(curve, at, width, height):
    n = len(curve)
    centre, half = at * (n - 1), max(1.0, width * n / 2)
    return [
        v + (height * (1 - abs(i - centre) / half) if abs(i - centre) < half else 0.0)
        for i, v in enumerate(curve)
    ]


class TestRefusals:
    def test_an_unknown_video_is_a_404(self, client):
        assert client.get("/v1/analytics/shorts/nope").status_code == 404

    def test_a_video_with_no_beats_says_so_rather_than_cutting_blind(self, client):
        insights_api.RECORDS["vid1"] = Record(beats=[])
        resp = client.get("/v1/analytics/shorts/vid1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["candidates"] == []
        assert "beats" in body["note"]

    def test_no_connected_channel_is_a_409(self, client):
        insights_api.RECORDS["vid1"] = Record([FakeBeat("a", 30.0)] * 10)
        assert client.get("/v1/analytics/shorts/vid1").status_code == 409

    def test_an_unreadable_duration_fails_rather_than_being_guessed(self, client, monkeypatch):
        """There is a tempting wrong answer here — fall back to `avd_seconds`, which
        is right there on the record. It is *average view duration*, a different
        quantity, and every timestamp derived from it lands in the wrong place."""
        insights_api.RECORDS["vid1"] = Record([FakeBeat("a", 30.0)] * 10)
        connect(monkeypatch, curve=decaying(), duration=None)
        assert client.get("/v1/analytics/shorts/vid1").status_code == 502


class TestResults:
    def test_a_standout_moment_is_returned_with_its_reason(self, client, monkeypatch):
        insights_api.RECORDS["vid1"] = Record([FakeBeat(f"b{i}", 30.0) for i in range(10)])
        connect(
            monkeypatch,
            curve=with_bump(decaying(), at=0.6, width=0.12, height=20.0),
            duration=300.0,
        )
        body = client.get("/v1/analytics/shorts/vid1").json()

        assert body["duration_s"] == 300.0
        assert body["note"] is None
        assert body["candidates"]
        best = body["candidates"][0]
        assert 15.0 <= best["duration_s"] <= 60.0
        assert best["reason"]

    def test_a_featureless_video_returns_an_empty_list_with_an_explanation(
        self, client, monkeypatch
    ):
        insights_api.RECORDS["vid1"] = Record([FakeBeat(f"b{i}", 30.0) for i in range(10)])
        connect(monkeypatch, curve=decaying(), duration=300.0)
        body = client.get("/v1/analytics/shorts/vid1").json()
        assert body["candidates"] == []
        assert body["note"]

    def test_count_is_honoured(self, client, monkeypatch):
        curve = decaying()
        for at in (0.2, 0.4, 0.6, 0.75):
            curve = with_bump(curve, at=at, width=0.05, height=20.0)
        insights_api.RECORDS["vid1"] = Record([FakeBeat(f"b{i}", 10.0) for i in range(30)])
        connect(monkeypatch, curve=curve, duration=300.0)
        body = client.get("/v1/analytics/shorts/vid1?count=2").json()
        assert len(body["candidates"]) == 2


class TestDurationParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("PT1M30S", 90.0),
            ("PT45S", 45.0),
            ("PT2H", 7200.0),
            ("PT1H2M3S", 3723.0),
            ("P1DT1H", 90000.0),
            ("PT1M30.5S", 90.5),
        ],
    )
    def test_the_forms_youtube_actually_emits(self, value, expected):
        assert _parse_iso8601_duration(value) == expected

    @pytest.mark.parametrize("value", ["", "nonsense", "P1D", "1M30S", "PT"])
    def test_anything_unparseable_is_none_rather_than_zero(self, value):
        """None routes to the 502 above. A zero would sail on into the selector and
        divide by it."""
        assert _parse_iso8601_duration(value) is None

    def test_a_live_stream_of_zero_length_is_none(self):
        assert _parse_iso8601_duration("PT0S") is None
