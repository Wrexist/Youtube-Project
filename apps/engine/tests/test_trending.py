"""Tests for FIX-TASKS E3: `ideas.build_backlog`'s `trending_terms` argument has
always existed and nothing ever supplied it, so `freshness` was zero for every
idea ever scored. `engine.trending` is the source; these tests are about its two
signals degrading honestly (never raising into a backlog build) and about the
rising-autocomplete diff actually being a diff, not just "today's terms".
"""

from __future__ import annotations

from engine import trending

# ── youtube_trending_terms ──────────────────────────────────────────────────


async def test_no_client_means_no_terms_not_an_error():
    assert await trending.youtube_trending_terms(None) == []


async def test_the_clients_titles_pass_through():
    class FakeClient:
        async def trending(self, *, region_code, category_id, limit):
            assert region_code == "US"
            return ["Why the Baltimore bridge collapsed", "Salt roads of the Sahara"]

    got = await trending.youtube_trending_terms(FakeClient())
    assert got == ["Why the Baltimore bridge collapsed", "Salt roads of the Sahara"]


async def test_a_failing_client_degrades_to_empty_rather_than_raising():
    """A trend source failing must not fail the backlog build it feeds."""

    class BrokenClient:
        async def trending(self, *, region_code, category_id, limit):
            raise RuntimeError("quota exceeded")

    assert await trending.youtube_trending_terms(BrokenClient()) == []


# ── rising_autocomplete_terms ────────────────────────────────────────────────


async def test_no_seed_means_no_terms():
    assert await trending.rising_autocomplete_terms("") == []


async def test_the_first_poll_for_a_seed_treats_every_term_as_rising(database, monkeypatch):
    """Nothing to diff against yet — every term counts, rather than the freshness
    signal staying silently zero for a channel just getting started."""

    async def fake_suggest(seed, *, expand=False):
        return ["bridge collapse baltimore", "bridge collapse investigation"]

    monkeypatch.setattr("engine.trending.kw.suggest", fake_suggest)

    got = await trending.rising_autocomplete_terms("bridge collapse")
    assert got == ["bridge collapse baltimore", "bridge collapse investigation"]


async def test_the_second_poll_reports_only_what_is_new(database, monkeypatch):
    from engine import repository

    await repository.save_keyword_snapshot("bridge collapse", ["bridge collapse baltimore"])

    async def fake_suggest(seed, *, expand=False):
        return ["bridge collapse baltimore", "bridge collapse investigation update"]

    monkeypatch.setattr("engine.trending.kw.suggest", fake_suggest)

    got = await trending.rising_autocomplete_terms("bridge collapse")
    assert got == ["bridge collapse investigation update"]


async def test_a_poll_updates_the_snapshot_for_next_time(database, monkeypatch):
    async def fake_suggest(seed, *, expand=False):
        return ["term a", "term b"]

    monkeypatch.setattr("engine.trending.kw.suggest", fake_suggest)
    await trending.rising_autocomplete_terms("seed")

    from engine import repository

    assert await repository.get_keyword_snapshot("seed") == ["term a", "term b"]


async def test_no_autocomplete_data_means_no_terms_and_no_snapshot_write(database, monkeypatch):
    async def empty(seed, *, expand=False):
        return []

    monkeypatch.setattr("engine.trending.kw.suggest", empty)
    assert await trending.rising_autocomplete_terms("seed") == []

    from engine import repository

    # An empty poll is not a signal that "nothing is rising any more" — it is
    # more likely a network hiccup, and overwriting a real snapshot with []
    # would make the *next* poll report everything as rising by mistake.
    assert await repository.get_keyword_snapshot("seed") == []


# ── gather_trending_terms ────────────────────────────────────────────────────


async def test_gathering_with_nothing_configured_is_empty_not_an_error(database):
    assert await trending.gather_trending_terms() == []


async def test_gathering_combines_both_signals(database, monkeypatch):
    class FakeClient:
        async def trending(self, *, region_code, category_id, limit):
            return ["trending video title"]

    async def fake_suggest(seed, *, expand=False):
        return ["rising autocomplete query"]

    monkeypatch.setattr("engine.trending.kw.suggest", fake_suggest)

    got = await trending.gather_trending_terms(youtube_client=FakeClient(), seed="a seed")
    assert got == ["trending video title", "rising autocomplete query"]


# ── providers.youtube.YouTube.trending ──────────────────────────────────────


async def test_trending_asks_for_the_mostpopular_chart(monkeypatch):
    """`videos.list?chart=mostPopular` is the whole point — a typo here silently
    turns this into an unfiltered, meaningless request."""
    from engine.providers.youtube import YouTube

    captured: dict = {}

    class FakeResponse:
        def json(self):
            return {"items": [{"snippet": {"title": "A trending video"}}]}

    async def fake_call(self, method, url, operation, **kwargs):
        captured.update(kwargs)
        captured["operation"] = operation
        return FakeResponse()

    monkeypatch.setattr(YouTube, "_call", fake_call)
    client = YouTube.__new__(YouTube)  # no OAuth creds needed — _call is stubbed

    got = await client.trending(region_code="GB", limit=10)
    assert got == ["A trending video"]
    assert captured["operation"] == "videos.list"
    assert captured["params"]["chart"] == "mostPopular"
    assert captured["params"]["regionCode"] == "GB"


async def test_trending_drops_items_with_no_title_rather_than_crashing(monkeypatch):
    from engine.providers.youtube import YouTube

    class FakeResponse:
        def json(self):
            return {"items": [{"snippet": {}}, {"snippet": {"title": "Has a title"}}]}

    async def fake_call(self, method, url, operation, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(YouTube, "_call", fake_call)
    client = YouTube.__new__(YouTube)

    assert await client.trending() == ["Has a title"]
