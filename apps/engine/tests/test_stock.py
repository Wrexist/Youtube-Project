"""Tests for stock footage search.

The two things worth pinning down are the response parsers (they are the shape
contract with two APIs nobody here can call in CI) and the orientation filter —
upstream compares width only, which is what lets a landscape clip through on a
portrait render and crops the subject out of frame.
"""

from __future__ import annotations

import pytest

from engine.services import stock
from engine.services.stock import (
    _matches_orientation,
    _parse_pexels,
    _parse_pixabay,
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
