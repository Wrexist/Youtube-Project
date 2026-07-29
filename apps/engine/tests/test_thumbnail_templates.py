"""Thumbnail archetypes.

Three "variants" used to be three pictures in one identical frame — words down the
left, image on the right, every time. That is the same thumbnail three times, and it
makes the variant picker decorative.

These cover the two things that make templates real rather than cosmetic: the three
variants genuinely differ, and each layout puts its type somewhere legal.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from engine.render import compose, templates
from engine.settings import get_settings

CONCEPT = {
    "image_prompt": "a collapsing bridge at dusk",
    "overlay_text": "It Gets Worse",
    "focal_point": "the span",
    "rationale": "stakes",
}


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch, tmp_path):
    """Compose against the flat panel — this is about layout, not image generation."""
    from engine.storage import ObjectStore

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(compose, "store", ObjectStore())
    yield
    get_settings.cache_clear()


async def _render(tmp_path, template_key: str, overlay: str = "It Gets Worse", **extra):
    concept = {**CONCEPT, "overlay_text": overlay, "template": template_key, **extra}
    thumb = await compose.make_thumbnail(concept, job_id="j", index=0)
    with Image.open(tmp_path / "thumbnails" / "j-0.jpg") as handle:
        # Eagerly, and detached from the file: `Image.open` is lazy, so two renders to
        # the same key would otherwise both read back whichever was written last.
        return thumb, handle.copy()


# ── the catalogue ───────────────────────────────────────────────────────────


def test_every_template_has_a_layout_that_exists():
    """A template naming a layout `compose` does not implement silently falls back."""
    implemented = {"left_column", "numeral", "versus", "banner", "centre_stage"}
    for template in templates.TEMPLATES.values():
        assert template.layout in implemented, f"{template.key} → unknown layout"


def test_every_template_tells_the_model_when_to_use_it():
    """A bare list of names gets picked from at random."""
    for template in templates.TEMPLATES.values():
        assert len(template.when) > 40, f"{template.key} has no usable guidance"
        assert template.image_direction, f"{template.key} gives the image model nothing"


def test_the_catalogue_prompt_names_every_template():
    catalogue = templates.catalogue_for_prompt()
    for key in templates.TEMPLATES:
        assert key in catalogue


def test_an_invented_template_falls_back_rather_than_failing():
    """A hallucinated name must not lose an otherwise usable concept."""
    assert templates.get("mrbeast_face").key == templates.FALLBACK
    assert templates.get(None).key == templates.FALLBACK
    assert templates.get("").key == templates.FALLBACK


def test_no_template_asks_for_more_words_than_read_at_168px():
    for template in templates.TEMPLATES.values():
        assert template.max_words <= 4, f"{template.key} allows an unreadable overlay"


# ── three variants must actually differ ─────────────────────────────────────


def test_repeated_choices_are_spread_across_templates():
    """Asked for variety, models still return one archetype three times."""
    assert templates.distinct(["stakes", "stakes", "stakes"]) == ["stakes", "numeral", "versus"]


def test_a_genuine_spread_is_left_alone():
    picked = ["versus", "numeral", "revelation"]
    assert templates.distinct(picked) == picked


def test_partial_duplication_keeps_what_was_chosen_first():
    assert templates.distinct(["revelation", "revelation", "versus"])[0] == "revelation"
    assert "versus" in templates.distinct(["revelation", "revelation", "versus"])


def test_junk_choices_still_yield_three_distinct_templates():
    spread = templates.distinct([None, "nonsense", ""])
    assert len(spread) == 3 and len(set(spread)) == 3


# ── each layout renders somewhere legal ─────────────────────────────────────


@pytest.mark.parametrize("key", sorted(templates.TEMPLATES))
async def test_every_template_renders_a_valid_thumbnail(tmp_path, key):
    thumb, img = await _render(tmp_path, key)
    assert img.size == (1280, 720)
    assert thumb.template == key
    size_kb = (tmp_path / "thumbnails" / "j-0.jpg").stat().st_size / 1024
    assert size_kb < 2048, "over YouTube's 2MB thumbnail ceiling"


async def _type_only(tmp_path, key, overlay):
    """Just the glyphs, isolated from whatever the layout paints behind them.

    Diffing against the same layout with no overlay is what makes this work for every
    template: `banner` sets near-black type on a bright accent bar, so anything
    keying off "bright pixels" measures the bar and never sees the type at all.
    """
    from PIL import ImageChops

    _, blank = await _render(tmp_path, key, overlay="")
    _, filled = await _render(tmp_path, key, overlay=overlay)
    diff = ImageChops.difference(blank.convert("L"), filled.convert("L"))
    return diff.point(lambda p: 255 if p > 40 else 0)


@pytest.mark.parametrize("key", sorted(templates.TEMPLATES))
async def test_no_template_draws_outside_the_canvas(tmp_path, key):
    """The bug the flat background hid: type set past the bottom edge.

    A glyph running off the frame is clipped rather than moved, so it leaves ink
    hard against the boundary — which is what this catches.
    """
    ink = await _type_only(tmp_path, key, "Absolutely Everything Changed Now")
    box = ink.getbbox()
    assert box is not None, f"{key}: nothing was drawn"
    left, top, right, bottom = box
    assert bottom <= 716, f"{key}: type reaches the bottom edge ({bottom})"
    assert right <= 1276, f"{key}: type reaches the right edge ({right})"
    assert left >= 4 and top >= 4, f"{key}: type reaches the top/left edge"


@pytest.mark.parametrize("key", sorted(templates.TEMPLATES))
async def test_the_duration_badge_corner_stays_clear(tmp_path, key):
    """YouTube stamps the runtime bottom-right; type under it is type lost."""
    ink = await _type_only(tmp_path, key, "Absolutely Everything Changed Now")
    corner = ink.crop((1130, 660, 1280, 720))
    covered = sum(1 for p in corner.getdata() if p) / (corner.width * corner.height)
    assert covered < 0.02, f"{key}: type under the duration badge ({covered:.0%})"


async def test_the_layouts_are_genuinely_different(tmp_path):
    """The whole point. If two templates render alike, the picker is decorative."""
    rendered = {}
    for key in templates.TEMPLATES:
        _, img = await _render(tmp_path, key)
        rendered[key] = list(img.convert("L").resize((32, 18)).getdata())

    keys = sorted(rendered)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            difference = sum(abs(x - y) for x, y in zip(rendered[a], rendered[b], strict=True))
            assert difference > 2000, f"{a} and {b} render nearly identically ({difference})"


async def test_the_numeral_layout_sets_the_number_biggest(tmp_path):
    """The quantity is the hook; the supporting words get out of its way.

    Measured by ink: the numeral is drawn in the accent and everything after it in
    white, so counting each is a direct read on which one dominates the frame.
    """
    _, img = await _render(tmp_path, "numeral", overlay="7 Days Alone", accent="lime")
    rgb = img.convert("RGB")
    lime = templates.ACCENTS["lime"]

    def _mask(test):
        out = Image.new("L", rgb.size, 0)
        out.putdata([255 if test(p) else 0 for p in rgb.getdata()])
        return out.getbbox()

    numeral = _mask(lambda p: all(abs(a - b) < 45 for a, b in zip(p, lime, strict=True)))
    words = _mask(lambda p: all(c > 215 for c in p))
    assert numeral and words

    # Cap height, not ink area: "7" is a thin glyph and "DAYS ALONE" is nine
    # characters, so by area they are nearly equal while the numeral is far taller.
    numeral_h = numeral[3] - numeral[1]
    per_line = (words[3] - words[1]) / 2
    assert numeral_h > per_line * 2, f"numeral {numeral_h}px vs each word ~{per_line:.0f}px"


async def test_an_empty_overlay_does_not_lose_the_thumbnail(tmp_path):
    """`_layout_numeral` indexes words[0]; a model returning "" must not crash it."""
    thumb, img = await _render(tmp_path, "numeral", overlay="   ")
    assert img.size == (1280, 720)
    assert thumb.template == "numeral"


async def test_the_banner_layout_paints_its_accent_bar(tmp_path):
    """Flat colour behind flat type is what survives compression on any background."""
    _, img = await _render(tmp_path, "transformation", accent="red")
    # The bar sits in the lower third, inset from the right for the badge.
    assert img.convert("RGB").getpixel((40, 600))[0] > 150


async def test_the_accent_is_honoured_when_the_concept_names_one(tmp_path):
    _, red = await _render(tmp_path, "transformation", accent="red")
    _, cyan = await _render(tmp_path, "transformation", accent="cyan")
    assert red.convert("RGB").getpixel((40, 600)) != cyan.convert("RGB").getpixel((40, 600))


async def test_an_unknown_accent_falls_back_to_a_real_colour(tmp_path):
    _, img = await _render(tmp_path, "transformation", accent="chartreuse-ish")
    painted = img.convert("RGB").getpixel((40, 600))
    expected = templates.ACCENTS[templates.DEFAULT_ACCENT]
    # Not exact: the bar is composited at alpha 242 and then JPEG-compressed.
    assert all(abs(a - b) < 20 for a, b in zip(painted, expected, strict=True)), painted


# ── the image prompt carries the template's direction ───────────────────────


async def test_the_template_steers_the_image_prompt(tmp_path, monkeypatch):
    """A layout that reserves space on the left needs the image to leave it empty."""
    from engine.providers import images

    seen = {}

    async def fake(prompt):
        seen["prompt"] = prompt
        buffer = io.BytesIO()
        Image.new("RGB", (1536, 1024), (90, 90, 90)).save(buffer, "PNG")
        return images.GeneratedImage(buffer.getvalue(), "openai:gpt-image-1", prompt, 0.19)

    monkeypatch.setattr(images, "generate", fake)
    await _render(tmp_path, "versus")

    assert CONCEPT["image_prompt"] in seen["prompt"]
    assert templates.TEMPLATES["versus"].image_direction in seen["prompt"]
