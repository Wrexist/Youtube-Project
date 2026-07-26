"""Tests for subtitle font resolution.

A wrong font here does not fail fast — MoviePy raises inside `TextClip`, which
is constructed after the footage is downloaded and the timeline is built. So the
resolution order and the failure message are worth pinning down.
"""

from __future__ import annotations

import pytest

from engine.services import fonts
from engine.settings import get_settings


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """An empty storage root, with the system font probe disabled.

    The probe has to go: CI images have DejaVu and developer laptops do not, and
    a test whose result depends on that is a test that reports the machine.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_SUBTITLE_FONT", raising=False)
    monkeypatch.setattr(fonts, "_SYSTEM_CANDIDATES", ())
    fonts.cached_resolve.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
    fonts.cached_resolve.cache_clear()


def _font(directory, name: str = "Studio-Bold.ttf"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"stub")
    return path


def test_a_drop_in_font_is_found_by_bare_name(storage):
    _font(storage / "fonts")
    assert fonts.resolve("Studio-Bold.ttf").name == "Studio-Bold.ttf"


def test_an_absolute_path_is_used_as_given(storage, tmp_path):
    elsewhere = _font(tmp_path / "somewhere", "Other.otf")
    assert fonts.resolve(str(elsewhere)) == elsewhere


def test_the_setting_supplies_the_default(storage, monkeypatch):
    _font(storage / "fonts")
    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_SUBTITLE_FONT", "Studio-Bold.ttf")
    try:
        assert fonts.resolve().name == "Studio-Bold.ttf"
    finally:
        get_settings.cache_clear()


def test_an_unknown_font_falls_back_rather_than_failing(storage):
    """A typo in a series config should not cost a render."""
    _font(storage / "fonts", "Fallback.ttf")
    assert fonts.resolve("NotInstalled.ttf").name == "Fallback.ttf"


def test_a_traversal_name_does_not_escape_the_drop_in_directory(storage, tmp_path):
    _font(storage / "fonts", "Fallback.ttf")
    outside = _font(tmp_path / "outside", "Escaped.ttf")
    assert fonts.resolve(f"../outside/{outside.name}").name == "Fallback.ttf"


def test_non_font_files_in_the_directory_are_ignored(storage):
    (storage / "fonts").mkdir(parents=True)
    (storage / "fonts" / "README.md").write_text("not a font")
    assert fonts.available_fonts() == []


def test_drop_ins_take_precedence_over_system_fonts(storage, tmp_path, monkeypatch):
    system = _font(tmp_path / "system", "System.ttf")
    monkeypatch.setattr(fonts, "_SYSTEM_CANDIDATES", (str(system),))
    dropped = _font(storage / "fonts")
    assert fonts.resolve() == dropped


def test_no_font_anywhere_names_every_place_it_looked(storage):
    with pytest.raises(RuntimeError, match="STUDIO_SUBTITLE_FONT"):
        fonts.resolve()


def test_the_cache_returns_a_string_for_moviepy(storage):
    _font(storage / "fonts")
    assert fonts.cached_resolve("Studio-Bold.ttf") == str(fonts.resolve("Studio-Bold.ttf"))
