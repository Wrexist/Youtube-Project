"""Acquisition — the only place video files enter the system.

Most of these are about the refusal. The download check and the persistence check
are deliberately separate, and the tests treat them that way: without the first,
bytes are already on disk by the time the second objects, and a file nobody was
allowed to fetch is not redeemed by declining to write a row about it.

The watermark tests build synthetic clips rather than mocking the detector,
because a detector that only passes against its own mock is worth nothing — it is
a hard block, and a false negative lets a clip past it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from engine import repository
from engine.repurpose import acquire as acquisition
from engine.repurpose.acquire import NotCleared
from engine.repurpose.rights import Grant, Lane, own


async def _clip(database_unused=None) -> str:
    await repository.upsert_clip_sources(
        [{"platform": "tiktok", "external_id": "aaa", "duration_s": 20}], channel_key="main"
    )
    return (await repository.clip_sources(channel_key="main"))[0]["id"]


# ── the refusal ─────────────────────────────────────────────────────────────


async def test_acquiring_without_a_grant_is_refused_before_the_network(database, monkeypatch):
    """Before, not after. Bytes on disk are not undone by a failed insert."""
    called = False

    async def tripwire(*_a, **_k):
        nonlocal called
        called = True

    monkeypatch.setattr(acquisition, "_download", tripwire)
    source_id = await _clip()

    with pytest.raises(NotCleared, match="no recorded grant"):
        await acquisition.acquire(source_id, "https://cdn.example/v.mp4")

    assert not called, "the download must not start"


async def test_acquiring_under_an_expired_grant_is_refused(database, monkeypatch):
    monkeypatch.setattr(acquisition, "_download", _tripwire)
    source_id = await _clip()
    await repository.record_grant(
        source_id,
        Grant(
            lane=Lane.CAMPAIGN,
            grantor="@streamer",
            evidence_kind="campaign_enrolment",
            evidence_ref="https://whop.example/c/1",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
    )

    with pytest.raises(NotCleared, match="expired or revoked"):
        await acquisition.acquire(source_id, "https://cdn.example/v.mp4")


async def test_a_caller_cannot_dodge_the_check_by_omitting_the_grant(database, monkeypatch):
    """The grant is looked up when not supplied, so passing None is not a bypass."""
    monkeypatch.setattr(acquisition, "_download", _tripwire)
    source_id = await _clip()

    with pytest.raises(NotCleared):
        await acquisition.acquire(source_id, "https://cdn.example/v.mp4", grant=None)


async def test_a_cleared_clip_with_no_media_url_says_why(database):
    """The common Lane B case, and the message has to explain it rather than
    looking like a bug."""
    source_id = await _clip()
    await repository.record_grant(source_id, own())

    with pytest.raises(NotCleared, match="only for your own posts"):
        await acquisition.acquire(source_id, "")


async def test_a_grant_revoked_mid_download_still_blocks_the_row(database, monkeypatch):
    """Acquisition and persistence are separated by a long download."""
    source_id = await _clip()
    await repository.record_grant(source_id, own())

    async def slow_download(_url, key):
        # The grant lapses while the bytes are in flight.
        await repository.record_grant(
            source_id,
            Grant(lane=Lane.OWN, revoked_at=datetime.now(UTC) - timedelta(seconds=1)),
        )
        return await _tiny_file(key)

    monkeypatch.setattr(acquisition, "_download", slow_download)
    monkeypatch.setattr(acquisition, "_probe", lambda _p: (10.0, 1080, 1920))
    monkeypatch.setattr(acquisition, "_scan_watermark", lambda _p: (False, []))

    with pytest.raises(PermissionError):
        await acquisition.acquire_and_record(source_id, "https://cdn.example/v.mp4")


# ── the happy path ──────────────────────────────────────────────────────────


async def test_acquisition_measures_and_records(database, monkeypatch):
    source_id = await _clip()
    await repository.record_grant(source_id, own())

    monkeypatch.setattr(acquisition, "_download", lambda _u, key: _tiny_file(key))
    monkeypatch.setattr(acquisition, "_probe", lambda _p: (18.5, 1080, 1920))
    monkeypatch.setattr(acquisition, "_scan_watermark", lambda _p: (False, []))

    result = await acquisition.acquire_and_record(source_id, "https://cdn.example/v.mp4")

    assert result.duration_s == 18.5
    assert result.width == 1080
    assert len(result.sha256) == 64
    clip = (await repository.clip_sources(channel_key="main"))[0]
    assert clip["acquired"] is True


# ── the size guard ──────────────────────────────────────────────────────────


async def test_an_oversized_download_is_stopped_mid_stream(tmp_path, monkeypatch):
    """Enforced while the bytes arrive. Checking afterwards means the file is
    already in memory, which is the thing being guarded against."""
    monkeypatch.setattr(acquisition, "MAX_BYTES", 1024)

    class Response:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self, _size):
            for _ in range(10):
                yield b"x" * 512

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, _method, _url):
            return Stream()

    monkeypatch.setattr(acquisition.httpx, "AsyncClient", lambda **_: Client())

    with pytest.raises(ValueError, match="refusing a clip over"):
        await acquisition._download("https://cdn.example/huge.mp4", "clips/huge.mp4")


# ── the watermark scan ──────────────────────────────────────────────────────
#
# Built as real frame stacks rather than mocks. A detector that only passes
# against its own mock is worth nothing, and this one gates a hard block.


def _moving_scene(n=12, h=200, w=120):
    """A clip whose content genuinely changes between samples."""
    rng = np.random.default_rng(0)
    return [rng.integers(0, 255, size=(h, w, 3)).astype(float) for _ in range(n)]


def test_a_composited_logo_is_detected():
    frames = _moving_scene()
    # A bright block in the same place every frame, over changing content.
    for frame in frames:
        frame[5:20, 5:40] = 250.0

    assert acquisition._persistent_overlay(frames)


def test_a_clip_with_no_overlay_is_not_flagged():
    assert not acquisition._persistent_overlay(_moving_scene())


def test_a_bright_but_moving_region_is_not_an_overlay():
    """A sunlit sky changes with the shot. A logo does not."""
    frames = []
    rng = np.random.default_rng(1)
    for i in range(12):
        frame = rng.integers(0, 60, size=(200, 120, 3)).astype(float)
        # Bright, but drifting — genuine scene content.
        frame[5:20, 5 + i * 4 : 40 + i * 4] = 250.0
        frames.append(frame)

    assert not acquisition._persistent_overlay(frames)


def test_a_static_region_alone_is_not_an_overlay():
    """A letterbox bar or a locked-off shot is not evidence of a watermark."""
    frames = [np.full((200, 120, 3), 200.0) for _ in range(12)]
    assert not acquisition._persistent_overlay(frames)


def test_too_few_samples_reports_nothing():
    assert not acquisition._persistent_overlay(_moving_scene(n=2))


def test_an_unreadable_clip_does_not_crash_the_scan(tmp_path):
    """Reported as no-detection, alongside a zero duration from the probe — a
    caller seeing that already knows not to trust the rest."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    assert acquisition._scan_watermark(broken) == (False, [])


async def _tripwire(*_a, **_k):
    raise AssertionError("the download must not start")


async def _tiny_file(key: str):
    from engine.storage import store

    path = await store.local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x01\x02fake mp4 bytes")
    return path
