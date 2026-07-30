"""Tests for stock footage search.

The two things worth pinning down are the response parsers (they are the shape
contract with two APIs nobody here can call in CI) and the orientation filter —
upstream compares width only, which is what lets a landscape clip through on a
portrait render and crops the subject out of frame.
"""

from __future__ import annotations

import httpx
import pytest

from engine.services import stock
from engine.services.stock import (
    _matches_orientation,
    _parse_pexels,
    _parse_pixabay,
    _target_orientation,
)


class _FakeClient:
    """Just enough of `httpx.AsyncClient` to drive one provider request.

    The response carries a real `httpx.Request` built from the same params the code
    sent, because that is exactly how the live client builds it — and the Pixabay
    key is one of those params, which is the whole point of the leak test below.
    """

    def __init__(self, *, status: int = 200, content_type: str = "application/json", body: bytes):
        self._status = status
        self._content_type = content_type
        self._body = body
        self.params: dict = {}

    async def get(self, url: str, *, params: dict, headers: dict | None = None) -> httpx.Response:
        self.params = dict(params)
        return httpx.Response(
            self._status,
            content=self._body,
            headers={"content-type": self._content_type},
            request=httpx.Request("GET", url, params=params),
        )


def _pexels(video_id: int, files: list[tuple[int, int]], duration: float = 10.0) -> dict:
    return {
        "id": video_id,
        "duration": duration,
        "video_files": [
            {"width": w, "height": h, "link": f"https://example.test/{video_id}-{w}.mp4"}
            for w, h in files
        ],
    }


def _pixabay(hit_id: int, sizes: dict[str, tuple[int, int]], duration: float = 10.0) -> dict:
    return {
        "id": hit_id,
        "duration": duration,
        "videos": {
            name: {
                "url": f"https://example.test/{hit_id}-{name}.mp4",
                "width": w,
                "height": h,
            }
            for name, (w, h) in sizes.items()
        },
    }


# ── orientation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "width,height,aspect,expected",
    [
        (1080, 1920, "9:16", True),  # portrait into portrait
        (1920, 1080, "9:16", False),  # landscape into portrait — the upstream bug
        (1920, 1080, "16:9", True),
        (1080, 1920, "16:9", False),
        (1080, 1080, "9:16", True),  # square crops cleanly either way
        (1080, 1080, "16:9", True),
        (1080, 1080, "1:1", True),
        (1920, 1080, "1:1", False),
        (0, 1080, "9:16", False),  # missing dimensions are unusable, not "square"
        (1080, 0, "16:9", False),
    ],
)
def test_orientation_filter(width, height, aspect, expected):
    assert _matches_orientation(width, height, aspect) is expected


# The filter above is only half of it. Pexels *does* take an orientation parameter,
# so for that provider what gets asked for decides what there is to filter — and the
# two halves were written apart and drifted. `1:1` asked for "landscape" while the
# filter rejects anything wider than 1.45, so every clip a square render fetched from
# Pexels was thrown away on arrival and the beat came back empty.

_SAMPLE_OF = {"portrait": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}


@pytest.mark.parametrize(
    ("aspect", "orientation"),
    [("9:16", "portrait"), ("1:1", "square"), ("16:9", "landscape")],
)
def test_each_aspect_asks_for_its_own_orientation(aspect, orientation):
    assert _target_orientation(aspect) == orientation


@pytest.mark.parametrize("aspect", ["9:16", "1:1", "16:9"])
def test_what_is_requested_is_what_the_filter_keeps(aspect):
    """The invariant, so the request and the filter cannot drift apart again.

    Asserted through the sample rather than by comparing strings: the two sides
    speak different languages — one an API parameter, one a width/height ratio — and
    the only thing that matters is that a clip matching the request survives.
    """
    width, height = _SAMPLE_OF[_target_orientation(aspect)]
    assert _matches_orientation(width, height, aspect) is True


@pytest.mark.parametrize(
    ("aspect", "orientation"),
    [("9:16", "portrait"), ("1:1", "square"), ("16:9", "landscape")],
)
async def test_the_orientation_reaches_the_pexels_request(aspect, orientation):
    """And that the mapping is actually wired into the query, not just defined."""
    client = _FakeClient(body=b'{"videos": []}')
    await stock._search_pexels(client, "bridges", aspect, 5)
    assert client.params["orientation"] == orientation


# ── pexels ──────────────────────────────────────────────────────────────────


def test_pexels_picks_the_highest_usable_resolution():
    payload = {"videos": [_pexels(1, [(720, 1280), (1080, 1920), (540, 960)])]}
    clips = _parse_pexels(payload, "bridges", "9:16")
    assert len(clips) == 1
    assert clips[0]["url"].endswith("1-1080.mp4")


def test_pexels_falls_past_a_wrongly_oriented_top_rendition():
    """A 4K landscape master must not win over a smaller portrait rendition."""
    payload = {"videos": [_pexels(2, [(3840, 2160), (1080, 1920)])]}
    clips = _parse_pexels(payload, "bridges", "9:16")
    assert clips[0]["url"].endswith("2-1080.mp4")


def test_pexels_skips_videos_with_no_usable_rendition():
    payload = {"videos": [_pexels(3, [(1920, 1080), (1280, 720)])]}
    assert _parse_pexels(payload, "bridges", "9:16") == []


def test_pexels_skips_clips_too_short_to_cut():
    payload = {"videos": [_pexels(4, [(1080, 1920)], duration=1.0)]}
    assert _parse_pexels(payload, "bridges", "9:16") == []


def test_pexels_ids_are_namespaced_by_provider():
    """Both providers use small integer ids, so a bare id collides across them."""
    payload = {"videos": [_pexels(7, [(1080, 1920)])]}
    assert _parse_pexels(payload, "bridges", "9:16")[0]["id"] == "pexels-7"


def test_pexels_tolerates_an_empty_or_malformed_payload():
    assert _parse_pexels({}, "bridges", "9:16") == []
    assert _parse_pexels({"videos": None}, "bridges", "9:16") == []
    assert _parse_pexels({"videos": [{"id": 9, "duration": 10}]}, "bridges", "9:16") == []


# ── pixabay ─────────────────────────────────────────────────────────────────


def test_pixabay_picks_the_highest_usable_rendition():
    payload = {
        "hits": [
            _pixabay(
                5,
                {
                    "tiny": (360, 640),
                    "large": (1080, 1920),
                    "medium": (720, 1280),
                },
            )
        ]
    }
    clips = _parse_pixabay(payload, "bridges", "9:16")
    assert len(clips) == 1
    assert clips[0]["url"].endswith("5-large.mp4")
    assert clips[0]["id"] == "pixabay-5"
    assert clips[0]["provider"] == "pixabay"


def test_pixabay_filters_orientation_it_cannot_request():
    """Pixabay's API has no orientation parameter, so the filter is all we have."""
    payload = {"hits": [_pixabay(6, {"large": (1920, 1080), "medium": (1280, 720)})]}
    assert _parse_pixabay(payload, "bridges", "9:16") == []


def test_pixabay_ignores_renditions_with_missing_fields():
    payload = {
        "hits": [
            {
                "id": 8,
                "duration": 12,
                "videos": {
                    "large": {"url": "", "width": 1080, "height": 1920},
                    "medium": {"url": "https://example.test/8.mp4", "width": 720, "height": 1280},
                },
            }
        ]
    }
    clips = _parse_pixabay(payload, "bridges", "9:16")
    assert [c["url"] for c in clips] == ["https://example.test/8.mp4"]


def test_pixabay_tolerates_an_empty_payload():
    assert _parse_pixabay({}, "bridges", "9:16") == []
    assert _parse_pixabay({"hits": []}, "bridges", "9:16") == []


async def test_a_pixabay_error_does_not_put_the_key_in_the_message(monkeypatch):
    """CLAUDE.md non-negotiable #4: secrets are never logged.

    Pixabay takes its key as a *query parameter*, and `resp.raise_for_status()`
    formats the entire request URL into its message. `search()` catches that
    exception and logs it at WARNING — so the single most likely Pixabay failure, a
    bad or rate-limited key, is also the one that writes the key into the log file,
    where it stays and gets pasted into bug reports.

    The content-type guard above it does not cover this: a 401 from Pixabay is
    `application/json`, so it sails past the Cloudflare check and dies on
    `raise_for_status` instead.
    """
    from engine.settings import get_settings

    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-secret-1234")
    get_settings.cache_clear()

    client = _FakeClient(status=401, body=b'{"error": "invalid key"}')
    with pytest.raises(Exception) as caught:  # noqa: B017 — the type is the fix's choice
        await stock._search_pixabay(client, "bridges", "9:16", 5)

    assert client.params["key"] == "pixabay-secret-1234", "the key really was on the wire"
    assert "pixabay-secret-1234" not in str(caught.value)
    assert "pixabay-secret-1234" not in repr(caught.value)
    # Still diagnosable — a scrubbed message that says nothing is its own bug.
    assert "401" in str(caught.value)


# ── download ────────────────────────────────────────────────────────────────


async def test_a_clip_with_no_url_does_not_take_the_render_down():
    """Regression: the handler used to re-index the key that failed.

    `_download` read `clip["url"]` inside its own `except`, so a clip missing
    that key raised KeyError *from the error path*, escaped `asyncio.gather`,
    and killed the whole render instead of logging one skipped clip. Found by
    an actual render, not by review.
    """
    clips = [{"id": "no-url"}, {"id": "also-none"}]
    await stock.download_all(clips)
    assert all("path" not in c for c in clips)


async def test_a_clip_that_already_has_a_file_is_not_refetched(tmp_path):
    """The resume path: re-downloading is exactly what resuming should avoid."""
    existing = tmp_path / "clip.mp4"
    existing.write_bytes(b"stub")
    clip = {"id": "cached", "path": str(existing), "url": "https://example.test/x.mp4"}

    await stock.download_all([clip])
    assert clip["path"] == str(existing)


async def test_an_empty_clip_list_makes_no_client(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("should not open an HTTP client for zero clips")

    monkeypatch.setattr(stock.httpx, "AsyncClient", explode)
    await stock.download_all([])
