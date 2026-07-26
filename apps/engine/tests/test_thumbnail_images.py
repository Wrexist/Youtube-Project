"""Thumbnail backgrounds.

The composition, safe zones and type treatment were built first and the background
was a flat panel — `Image.new("RGB", (1280, 720), (18, 18, 21))`, with the
`image_prompt` each concept produced going nowhere. This covers wiring a real
provider behind it *without* making an API key a requirement: the whole point of the
"auto" default is that a clone with no keys still produces a thumbnail.
"""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from engine.providers import images
from engine.render import compose
from engine.settings import get_settings

CONCEPT = {
    "image_prompt": "a lone hiker on a ridge at dawn",
    "overlay_text": "It Gets Worse",
    "focal_point": "the hiker",
    "rationale": "stakes, not summary",
}


def _png(colour=(200, 40, 40), size=(1536, 1024)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── provider selection ──────────────────────────────────────────────────────


def test_no_keys_means_no_provider(monkeypatch):
    """A fresh clone has neither key. That must not be an error."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert images.selected() is None


def test_auto_picks_whichever_key_exists(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    spec = images.selected()
    assert spec is not None and spec.provider == "openai"


def test_gpt_image_wins_a_tie(monkeypatch):
    """The better thumbnail model wins, even though it is the dearer one.

    The thumbnail is the asset that decides whether the video gets clicked, so this
    is the one place in the pipeline where quality outranks cost per call.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    spec = images.selected()
    assert spec is not None and spec.provider == "openai"


def test_gemini_is_still_reachable_when_it_is_the_only_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    spec = images.selected()
    assert spec is not None and spec.provider == "gemini"


def test_none_disables_generation_even_with_a_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "none")
    assert images.selected() is None


def test_a_named_provider_without_its_key_degrades_rather_than_crashing(monkeypatch):
    """Worth a warning, not a dead render — but it must not silently use the other key."""
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    assert images.selected() is None


def test_an_unknown_provider_is_refused_at_startup(monkeypatch):
    from engine.settings import Settings

    with pytest.raises(Exception, match="image_provider"):
        Settings(image_provider="midjourney")


# ── transports ──────────────────────────────────────────────────────────────


async def test_openai_decodes_base64(monkeypatch):
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = {"data": [{"b64_json": base64.b64encode(b"fake-bytes").decode()}]}
    _stub_post(monkeypatch, httpx.Response(200, json=payload))

    result = await images.generate("a ridge at dawn")
    assert result is not None
    assert result.data == b"fake-bytes"
    assert result.model == "openai:gpt-image-1"
    assert result.prompt == "a ridge at dawn"
    assert result.cost_usd > 0


async def test_gemini_decodes_its_own_envelope(monkeypatch):
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    payload = {"predictions": [{"bytesBase64Encoded": base64.b64encode(b"fake-bytes").decode()}]}
    _stub_post(monkeypatch, httpx.Response(200, json=payload))

    result = await images.generate("a ridge at dawn")
    assert result is not None and result.data == b"fake-bytes"


async def test_a_provider_error_raises_rather_than_silently_degrading(monkeypatch):
    """A broken key must not hide behind a thumbnail that merely looks unstyled."""
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    _stub_post(monkeypatch, httpx.Response(401, text="invalid key"))

    with pytest.raises(images.ImageUnavailable):
        await images.generate("a ridge at dawn")


async def test_the_api_key_never_appears_in_the_error(monkeypatch):
    """Imagen takes its key in the query string, so the URL must stay out of logs."""
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key")
    _stub_post(monkeypatch, httpx.Response(500, text="upstream boom"))

    with pytest.raises(images.ImageUnavailable) as caught:
        await images.generate("a ridge at dawn")
    assert "super-secret-key" not in str(caught.value)


# ── composition ─────────────────────────────────────────────────────────────


async def test_a_thumbnail_is_produced_with_no_provider_at_all(monkeypatch, tmp_path):
    """The zero-config path. This is the one that must never regress."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    _use_storage(monkeypatch, tmp_path)

    thumb = await compose.make_thumbnail(CONCEPT, job_id="job-1", index=0)

    assert thumb.key == "thumbnails/job-1-0.jpg"
    assert thumb.generated is False
    assert thumb.cost_usd == 0.0
    written = tmp_path / "thumbnails" / "job-1-0.jpg"
    assert Image.open(written).size == (1280, 720)


async def test_a_generated_background_is_cover_fitted_to_16_9(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    _use_storage(monkeypatch, tmp_path)

    async def fake_generate(prompt):
        assert prompt == CONCEPT["image_prompt"], "the concept's image_prompt must be used"
        return images.GeneratedImage(
            data=_png(size=(1536, 1024)),
            model="gemini:imagen-4.0-generate-001",
            prompt=prompt,
            cost_usd=0.04,
        )

    monkeypatch.setattr(images, "generate", fake_generate)
    thumb = await compose.make_thumbnail(CONCEPT, job_id="job-1", index=2)

    assert thumb.generated is True
    assert thumb.image_model == "gemini:imagen-4.0-generate-001"
    assert thumb.cost_usd == 0.04
    # 1536x1024 is 3:2. Cover-fitting to 16:9 must crop, never letterbox.
    written = Image.open(tmp_path / "thumbnails" / "job-1-2.jpg")
    assert written.size == (1280, 720)


async def test_the_scrim_darkens_the_text_column_but_not_the_focal_point(monkeypatch, tmp_path):
    """White type over an arbitrary photo is only legible because of the scrim."""
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    _use_storage(monkeypatch, tmp_path)

    bright = _png(colour=(255, 255, 255), size=(1280, 720))

    async def fake_generate(_prompt):
        return images.GeneratedImage(bright, "gemini:imagen-4.0-generate-001", "p", 0.04)

    monkeypatch.setattr(images, "generate", fake_generate)
    await compose.make_thumbnail({**CONCEPT, "overlay_text": ""}, job_id="job-1", index=0)

    written = Image.open(tmp_path / "thumbnails" / "job-1-0.jpg").convert("L")
    left = written.getpixel((40, 600))  # under where the type sits
    right = written.getpixel((1200, 600))  # where the focal point belongs
    assert left < 120, f"text column not darkened enough (luma {left})"
    assert right > 230, f"scrim bled into the focal point (luma {right})"


async def test_undecodable_bytes_still_yield_a_thumbnail(monkeypatch, tmp_path):
    """A bad response loses the background, not the asset."""
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    _use_storage(monkeypatch, tmp_path)

    async def fake_generate(_prompt):
        return images.GeneratedImage(b"not an image", "openai:gpt-image-1", "p", 0.19)

    monkeypatch.setattr(images, "generate", fake_generate)
    thumb = await compose.make_thumbnail(CONCEPT, job_id="job-1", index=0)
    assert Image.open(tmp_path / "thumbnails" / "job-1-0.jpg").size == (1280, 720)
    assert thumb.generated is True  # it was charged for; that stays on the record


# ── type fitting ────────────────────────────────────────────────────────────
#
# The original loop drew at a fixed 150px from a fixed y=180 in fixed 160px steps.
# The concept prompt asks for 3-5 words: at four the last line was half cut off, at
# five it was drawn entirely below the canvas. Invisible while the background was a
# flat panel nobody ever looked at.


async def _type_bbox(monkeypatch, tmp_path, overlay: str):
    """Where the white type actually landed."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    _use_storage(monkeypatch, tmp_path)

    await compose.make_thumbnail({**CONCEPT, "overlay_text": overlay}, job_id="j", index=0)
    grey = Image.open(tmp_path / "thumbnails" / "j-0.jpg").convert("L")
    # The flat background is (18,18,21); the type is white with a black stroke.
    return grey.point(lambda p: 255 if p > 200 else 0).getbbox()


@pytest.mark.parametrize(
    "overlay",
    [
        "Two Words",
        "It Gets Worse",
        "Nobody Told You This",
        "This Changes Absolutely Everything Now",
    ],
)
async def test_the_type_never_leaves_the_canvas(monkeypatch, tmp_path, overlay):
    bbox = await _type_bbox(monkeypatch, tmp_path, overlay)
    assert bbox is not None, "no type was drawn at all"
    left, top, right, bottom = bbox
    assert bottom <= compose.THUMB_H, f"{overlay!r}: type runs {bottom - 720}px off the bottom"
    assert right <= compose.THUMB_W, f"{overlay!r}: type runs off the right edge"
    assert left >= 0 and top >= 0


async def test_the_type_stays_out_of_the_focal_column(monkeypatch, tmp_path):
    """The concept puts its subject on the right; type crossing into it wastes both."""
    _, _, right, _ = await _type_bbox(monkeypatch, tmp_path, "Nobody Told You This")
    assert right <= compose.TEXT_RIGHT + 20


async def test_more_words_are_set_smaller(monkeypatch, tmp_path):
    """Fitting means shrinking, not clipping."""
    short = await _type_bbox(monkeypatch, tmp_path, "Two Words")
    long_ = await _type_bbox(monkeypatch, tmp_path, "This Changes Absolutely Everything Now")
    short_line = (short[3] - short[1]) / 2
    long_line = (long_[3] - long_[1]) / 5
    assert long_line < short_line


async def test_a_sixth_word_is_dropped_rather_than_shrunk_into_illegibility(monkeypatch, tmp_path):
    """168px wide on a phone is the real test; six words cannot survive it."""
    bbox = await _type_bbox(monkeypatch, tmp_path, "One Two Three Four Five Six Seven")
    assert bbox is not None and bbox[3] <= compose.THUMB_H


# ── budget ──────────────────────────────────────────────────────────────────


def test_the_cost_estimate_follows_the_configured_provider(monkeypatch):
    """`Workflow.run` refuses a stage whose estimate breaches the budget.

    A flat estimate would either block a run that costs nothing or wave through one
    that costs twice as much, depending on which way it was wrong.
    """
    from engine.workflows.media import ThumbnailStage

    stage = ThumbnailStage()

    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    without = stage.estimated_cost_usd

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    with_images = stage.estimated_cost_usd

    assert without < with_images
    assert with_images == pytest.approx(0.06 + 3 * images.CATALOGUE["openai"].cost_per_image)


# ── helpers ─────────────────────────────────────────────────────────────────


def _stub_post(monkeypatch, response: httpx.Response) -> None:
    async def post(self, *_a, **_kw):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


def _use_storage(monkeypatch, root) -> None:
    """Point the module-level store at a tmp dir — it resolved its root at import."""
    from engine.storage import ObjectStore

    get_settings.cache_clear()
    monkeypatch.setattr(compose, "store", ObjectStore())
