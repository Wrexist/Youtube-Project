"""Tests for background music selection and path safety.

`resolve()` takes a caller-supplied name and turns it into a path handed to
FFmpeg. That is the whole reason these tests exist: the allow-listing has to
hold against traversal and symlink escapes, and a missing track has to degrade
to "no music" rather than a failed render.

The mix itself needs MoviePy and a real audio file, so it is not covered here —
`KNOWN-ISSUES.md` §2 records what was verified by an actual render.
"""

from __future__ import annotations

import os

import pytest

from engine.services import bgm
from engine.settings import get_settings


@pytest.fixture
def music(tmp_path, monkeypatch):
    """A populated BGM directory with settings pointed at it."""
    directory = tmp_path / "bgm"
    directory.mkdir()
    for name in ("a-track.mp3", "b-track.wav", "notes.txt", "clip.mp4"):
        (directory / name).write_bytes(b"stub")

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_BGM_DIR", str(directory))
    monkeypatch.setenv("STUDIO_BGM_ENABLED", "true")
    yield directory
    get_settings.cache_clear()


# ── listing ─────────────────────────────────────────────────────────────────


def test_listing_filters_by_extension(music):
    """A stray .mp4 would otherwise be mixed in as a second video's audio."""
    assert [p.name for p in bgm.list_tracks()] == ["a-track.mp3", "b-track.wav"]


def test_a_missing_directory_lists_nothing(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_BGM_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("STUDIO_BGM_ENABLED", "true")
    try:
        assert bgm.list_tracks() == []
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_a_symlink_out_of_the_directory_is_skipped(music, tmp_path):
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"stub")
    (music / "escape.mp3").symlink_to(outside)
    assert "escape.mp3" not in [p.name for p in bgm.list_tracks()]


# ── resolution ──────────────────────────────────────────────────────────────


def test_a_named_track_resolves(music):
    resolved = bgm.resolve("a-track.mp3")
    assert resolved is not None and resolved.name == "a-track.mp3"


def test_random_picks_something_from_the_directory(music):
    resolved = bgm.resolve("random")
    assert resolved is not None
    assert resolved.name in {"a-track.mp3", "b-track.wav"}


def test_an_empty_name_behaves_like_random(music):
    assert bgm.resolve("") is not None


def test_a_traversal_path_does_not_escape(music, tmp_path):
    (tmp_path / "outside.mp3").write_bytes(b"stub")
    assert bgm.resolve("../outside.mp3") is None


def test_an_absolute_path_is_not_honoured(music, tmp_path):
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"stub")
    assert bgm.resolve(str(outside)) is None


def test_an_unknown_track_is_not_an_error(music):
    assert bgm.resolve("missing.mp3") is None


def test_an_unsupported_extension_is_refused(music):
    assert bgm.resolve("notes.txt") is None


def test_no_music_available_returns_none(tmp_path, monkeypatch):
    """A video with no music bed is a video, not a failure."""
    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_BGM_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("STUDIO_BGM_ENABLED", "true")
    try:
        assert bgm.resolve("random") is None
    finally:
        get_settings.cache_clear()


# ── the mix gate ────────────────────────────────────────────────────────────


def test_music_is_off_unless_explicitly_enabled(monkeypatch):
    """Nothing ships with licensed music, so the default must be silence."""
    get_settings.cache_clear()
    monkeypatch.delenv("STUDIO_BGM_ENABLED", raising=False)
    try:
        assert get_settings().bgm_enabled is False
        assert bgm.should_mix(0.5) is False
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("volume", [0.0, -0.1, 1.5, "loud"])
def test_a_nonsense_volume_skips_the_mix(music, volume):
    assert bgm.should_mix(volume) is False


def test_none_means_use_the_configured_volume(music, monkeypatch):
    assert bgm.should_mix(None) is True

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_BGM_VOLUME", "0.4")
    try:
        assert get_settings().bgm_volume == pytest.approx(0.4)
        assert bgm.should_mix(None) is True
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("volume", [0.01, 0.12, 1.0])
def test_a_sane_volume_enables_the_mix(music, volume):
    assert bgm.should_mix(volume) is True
