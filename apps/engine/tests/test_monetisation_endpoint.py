"""`GET /v1/analytics/monetisation` — the seam nothing tested.

20 tests covered `progress()`. 7 covered the card. Between them sat
`MonetisationOut`, whose `blocking` field was typed `str | None` while
`Progress.blocking` returns `list[str]` — so the endpoint raised a validation
error for **every channel that was not already monetised**, which is every channel
the feature exists for. Both halves were green the whole time.

So the assertions here are deliberately about the response actually serialising,
not about the arithmetic. The arithmetic has its own file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from engine.api import insights as insights_api
from engine.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def connect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subscribers: int | None,
    watch_minutes: float,
    days: int = 400,
) -> None:
    monkeypatch.setattr(insights_api, "CHANNELS", {"default": object()}, raising=False)
    today = datetime.now(UTC).date()

    class FakeYouTube:
        def __init__(self, creds: object) -> None:
            pass

        async def subscriber_count(self) -> int | None:
            return subscribers

    class FakeAnalytics:
        def __init__(self, creds: object) -> None:
            pass

        async def daily(self, days: int = 28) -> list:
            class Row:
                def __init__(self, day: date) -> None:
                    self.day = day
                    self.watch_minutes = watch_minutes

            return [Row(today - timedelta(days=i)) for i in range(days)]

        async def shorts_views(self) -> dict:
            return {}

    monkeypatch.setattr(insights_api.youtube, "YouTube", FakeYouTube)
    monkeypatch.setattr(insights_api, "Analytics", FakeAnalytics)


class TestItActuallyResponds:
    def test_a_channel_short_of_both_thresholds_gets_a_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that 500'd. Not an edge case — it is every new channel."""
        connect(monkeypatch, subscribers=50, watch_minutes=1.0)
        resp = client.get("/v1/analytics/monetisation")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["eligible"] is False
        assert body["blocking"] == ["subscribers", "watch hours"]

    def test_blocking_is_a_list_even_when_only_one_thing_is_left(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connect(monkeypatch, subscribers=5000, watch_minutes=1.0)
        body = client.get("/v1/analytics/monetisation").json()
        assert body["blocking"] == ["watch hours"]

    def test_an_eligible_channel_reports_nothing_blocking(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connect(monkeypatch, subscribers=5000, watch_minutes=1000.0)
        body = client.get("/v1/analytics/monetisation").json()
        assert body["eligible"] is True
        assert body["blocking"] == []

    def test_a_hidden_subscriber_count_is_flagged_rather_than_failing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connect(monkeypatch, subscribers=None, watch_minutes=1.0)
        body = client.get("/v1/analytics/monetisation").json()
        assert body["subscriber_count_hidden"] is True
        assert body["subscribers"]["current"] == 0

    def test_every_field_the_card_reads_is_present(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connect(monkeypatch, subscribers=50, watch_minutes=1.0)
        body = client.get("/v1/analytics/monetisation").json()

        assert set(body) == {
            "eligible",
            "route",
            "blocking",
            "caveat",
            "subscribers",
            "watch_hours",
            "shorts_views",
            "subscriber_count_hidden",
        }
        for name in ("subscribers", "watch_hours", "shorts_views"):
            assert set(body[name]) >= {"name", "current", "target", "met", "fraction"}


class TestRefusals:
    def test_no_connected_channel_is_a_409(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(insights_api, "CHANNELS", {}, raising=False)
        assert client.get("/v1/analytics/monetisation").status_code == 409
