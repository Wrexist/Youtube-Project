"""Discovery: Lane A sweeps, and the boundary this module must not cross.

The first test is the important one. TikTok's APIs return the authenticated user's
own content and nothing else, so a function here that appears to fetch arbitrary
creators' video would be either broken or a lie about what the product does — and
either way it would be the seam where the whole rights model stops meaning
anything.
"""

from __future__ import annotations

from engine.providers import tiktok
from engine.repurpose import discover as discovery


def _raw(video_id: str, *, caption: str = "compound interest explained", views: int = 1000):
    return {
        "id": video_id,
        "video_description": caption,
        "duration": 30,
        "share_url": f"https://tiktok.example/v/{video_id}",
        "view_count": views,
        "like_count": 10,
    }


# ── the boundary ────────────────────────────────────────────────────────────


def test_the_provider_exposes_no_way_to_fetch_other_peoples_video():
    """A guard, not a formality.

    TikTok issues no credential that returns arbitrary creators' media. If a
    function appears here that claims to, it is either broken or misrepresenting
    what the product does — and it is exactly the seam where the rights model
    would stop meaning anything.
    """
    surface = {name for name in dir(tiktok) if not name.startswith("_")}
    forbidden = {"search", "search_videos", "download", "fetch_video", "by_hashtag", "user_videos"}

    assert not (surface & forbidden)
    assert "own_videos" in surface, "Lane A is the only media path"


def test_trends_need_no_credentials_and_no_media():
    """Discovery and acquisition are different problems. Knowing a topic is
    moving infringes nothing."""
    import inspect

    source = inspect.getsource(tiktok.trends)
    assert "video" not in source.lower() or "no video" in source.lower()


# ── the provider ────────────────────────────────────────────────────────────


def test_unconfigured_tiktok_reports_it_rather_than_raising(monkeypatch):
    from engine.settings import get_settings

    get_settings.cache_clear()
    assert tiktok.configured() is False


async def test_own_videos_without_a_token_returns_nothing():
    assert await tiktok.own_videos("") == []


async def test_own_videos_survives_an_unreachable_tiktok(monkeypatch):
    """A failed sweep is "no clips today", not a broken screen."""

    class Boom:
        async def __aenter__(self):
            raise ConnectionError("no network")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(tiktok.httpx, "AsyncClient", lambda **_: Boom())

    assert await tiktok.own_videos("token") == []


def test_a_row_missing_every_optional_field_still_parses():
    """TikTok omits fields rather than nulling them. A KeyError here would lose
    the whole sweep over one video with no view count."""
    clip = tiktok._clip({"id": "abc"})

    assert clip.external_id == "abc"
    assert clip.stats["views"] == 0
    assert clip.duration_s == 0.0


def test_hashtags_come_out_of_the_caption():
    clip = tiktok._clip(_raw("x", caption="why this works #investing #money"))
    assert clip.hashtags == ["#investing", "#money"]


def test_a_clip_serialises_without_its_media_url():
    """`as_dict` feeds `clip_sources`, which is metadata only — the media URL
    belongs to the acquire step and must not leak into the discovery row."""
    clip = tiktok._clip(_raw("x"))
    clip.media_url = "https://cdn.example/v.mp4"

    assert "media_url" not in clip.as_dict()


# ── the sweep ───────────────────────────────────────────────────────────────


async def test_a_sweep_scores_and_stores(database, monkeypatch):
    async def fake_videos(_token, *, limit=20):
        return [
            tiktok._clip(_raw("low", caption="unrelated cooking thing", views=10)),
            tiktok._clip(_raw("high", caption="compound interest explained", views=900_000)),
        ]

    monkeypatch.setattr(tiktok, "own_videos", fake_videos)
    monkeypatch.setattr(discovery.keywords, "suggest", _no_suggestions)

    scored = await discovery.discover_own(
        "token",
        channel_key="main",
        channel_topics=["how compound interest actually works"],
    )

    assert [c["external_id"] for c in scored] == ["high", "low"]

    from engine import repository

    stored = await repository.clip_sources(channel_key="main")
    assert {c["external_id"] for c in stored} == {"high", "low"}


async def test_re_running_a_sweep_does_not_resurrect_a_dismissal(database, monkeypatch):
    """Discovery is expected to run repeatedly over the same data."""
    from engine import repository

    async def fake_videos(_token, *, limit=20):
        return [tiktok._clip(_raw("aaa"))]

    monkeypatch.setattr(tiktok, "own_videos", fake_videos)
    monkeypatch.setattr(discovery.keywords, "suggest", _no_suggestions)

    await discovery.discover_own("token", channel_key="main")
    clip_id = (await repository.clip_sources(channel_key="main"))[0]["id"]
    await repository.set_clip_status(clip_id, "dismissed")

    await discovery.discover_own("token", channel_key="main")

    assert await repository.clip_sources(channel_key="main") == []


async def test_an_empty_sweep_stores_nothing(database, monkeypatch):
    monkeypatch.setattr(tiktok, "own_videos", lambda *_a, **_k: _empty())

    assert await discovery.discover_own("token", channel_key="main") == []


async def test_a_failing_autocomplete_sweep_does_not_lose_the_clips(database, monkeypatch):
    """Demand at zero is a worse ranking, not a broken discovery pass."""

    async def fake_videos(_token, *, limit=20):
        return [tiktok._clip(_raw("aaa", caption="a reasonably long caption about money"))]

    async def boom(*_a, **_k):
        raise TimeoutError("autocomplete is down")

    monkeypatch.setattr(tiktok, "own_videos", fake_videos)
    monkeypatch.setattr(discovery.keywords, "suggest", boom)

    scored = await discovery.discover_own("token", channel_key="main")

    assert len(scored) == 1


async def test_own_footage_is_scored_as_ready(database, monkeypatch):
    """Lane A carries a standing grant, so it must not rank as "no rights"."""

    async def fake_videos(_token, *, limit=20):
        return [tiktok._clip(_raw("aaa"))]

    monkeypatch.setattr(tiktok, "own_videos", fake_videos)
    monkeypatch.setattr(discovery.keywords, "suggest", _no_suggestions)

    scored = await discovery.discover_own("token", channel_key="main")

    assert "no rights recorded yet" not in scored[0]["fit_reasons"]


async def _no_suggestions(*_args, **_kwargs):
    return []


async def _empty():
    return []
