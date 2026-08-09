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


def test_the_better_thumbnail_model_wins_a_tie(monkeypatch):
    """The thumbnail is the asset that decides whether the video gets clicked, so
    this is the one place in the pipeline where quality outranks cost per call.

    It no longer costs anything to say so. This test asserted `openai` while GPT
    Image 1 was the better model and the dearer one; Gemini 3 Pro Image is now
    both stronger and cheaper ($0.134 against $0.19), and returns 16:9 natively
    where GPT Image returns 3:2 and has to be cropped. The rule is unchanged —
    only which model satisfies it.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    spec = images.selected()
    assert spec is not None and spec.provider == "gemini"
    assert spec.model == "gemini-3-pro-image"


def test_the_preference_order_is_the_catalogue_order(monkeypatch):
    """These were two separate literals, so reordering the catalogue to put the
    better model first changed nothing: "auto" kept choosing whichever provider
    the other dict happened to list first."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    assert images.selected().provider == next(iter(images.CATALOGUE))


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
    """The `generateContent` shape, which is what the Gemini image models answer on.

    Not interchangeable with Imagen's `:predict`: different endpoint, different
    request body, and the image comes back as an inline part of a candidate
    rather than as a prediction. Asking one on the other's URL is a 404.
    """
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    encoded = base64.b64encode(b"fake-bytes").decode()
    payload = {"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]}
    _stub_post(monkeypatch, httpx.Response(200, json=payload))

    result = await images.generate("a ridge at dawn")
    assert result is not None and result.data == b"fake-bytes"


async def test_an_imagen_model_still_decodes_the_predict_envelope(monkeypatch):
    """`endpoint` is per model, not per provider — the Imagen family is still
    served by :predict and anyone who has pinned one keeps working."""
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setitem(
        images.CATALOGUE,
        "gemini",
        images.ImageSpec("gemini", "imagen-4.0-generate-001", "Imagen 4", 0.04, "16:9"),
    )
    payload = {"predictions": [{"bytesBase64Encoded": base64.b64encode(b"fake-bytes").decode()}]}
    _stub_post(monkeypatch, httpx.Response(200, json=payload))

    result = await images.generate("a ridge at dawn")
    assert result is not None and result.data == b"fake-bytes"


async def test_a_text_only_answer_is_reported_rather_than_returned(monkeypatch):
    """generateContent answers a refusal or a safety block with a text part and a
    200, so "no inline data" is a normal response to handle, not a malformed one."""
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    payload = {"candidates": [{"content": {"parts": [{"text": "I cannot draw that."}]}}]}
    _stub_post(monkeypatch, httpx.Response(200, json=payload))

    with pytest.raises(images.ImageUnavailable, match="no image"):
        await images.generate("a ridge at dawn")


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
    monkeypatch.setattr(images, "_BACKOFF", 0.0)  # 500 is retried; do not sleep for it
    _stub_post(monkeypatch, httpx.Response(500, text="upstream boom"))

    with pytest.raises(images.ImageUnavailable) as caught:
        await images.generate("a ridge at dawn")
    assert "super-secret-key" not in str(caught.value)


# ── transport retry ─────────────────────────────────────────────────────────
#
# Two of the three callers of `generate` are stages, and the workflow runner gives
# a stage three attempts. The third is `POST /v1/jobs/{id}/thumbnails`, which
# composes inside the request with nothing behind it — so a blip there is a 502 on
# an action the panel has already told the operator costs money.


async def test_a_transient_status_is_retried_until_it_succeeds(monkeypatch):
    calls = _stub_sequence(
        monkeypatch,
        httpx.Response(503, text="overloaded"),
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json=_GEMINI_OK),
    )

    result = await images.generate("a ridge at dawn")
    assert result is not None and result.data == b"fake-bytes"
    assert len(calls) == 3


async def test_a_rejected_prompt_is_not_retried(monkeypatch):
    """400 is the model's verdict on the prompt. It says the same thing three times."""
    calls = _stub_sequence(monkeypatch, httpx.Response(400, text="content policy"))

    with pytest.raises(images.ImageUnavailable, match="content policy"):
        await images.generate("a ridge at dawn")
    assert len(calls) == 1


async def test_giving_up_still_reports_the_provider_s_own_words(monkeypatch):
    """The last response is returned, not swallowed — its body is the diagnosis."""
    calls = _stub_sequence(monkeypatch, *[httpx.Response(503, text="overloaded")] * 3)

    with pytest.raises(images.ImageUnavailable, match="overloaded"):
        await images.generate("a ridge at dawn")
    assert len(calls) == 3


async def test_a_connect_failure_is_retried(monkeypatch):
    calls = _stub_sequence(
        monkeypatch,
        httpx.ConnectError("no route to host"),
        httpx.Response(200, json=_GEMINI_OK),
    )

    result = await images.generate("a ridge at dawn")
    assert result is not None and result.data == b"fake-bytes"
    assert len(calls) == 2


async def test_a_read_timeout_is_not_retried(monkeypatch):
    """The request landed and we gave up waiting. The provider may be drawing the
    picture it will bill us for, so asking again risks paying twice for one image."""
    calls = _stub_sequence(monkeypatch, httpx.ReadTimeout("too slow"))

    with pytest.raises(httpx.ReadTimeout):
        await images.generate("a ridge at dawn")
    assert len(calls) == 1


async def test_the_wait_grows_between_attempts(monkeypatch):
    """Backing off flat would hammer a provider that asked for room."""
    slept: list[float] = []

    async def _record(seconds):
        slept.append(seconds)

    # After `_stub_sequence`, which installs a no-op sleep of its own.
    _stub_sequence(monkeypatch, *[httpx.Response(503, text="overloaded")] * 3)
    monkeypatch.setattr(images.asyncio, "sleep", _record)

    with pytest.raises(images.ImageUnavailable):
        await images.generate("a ridge at dawn")
    # Two waits for three attempts. Nothing is slept after the last one.
    assert slept == [2.0, 4.0]


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
        assert CONCEPT["image_prompt"] in prompt, "the concept's image_prompt must be used"
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


def test_more_words_are_set_smaller():
    """Fitting means shrinking, not clipping.

    Asked of `_fit_type` directly, because the rendered image cannot answer it.
    Two earlier versions of this test tried and both measured something else:

    - The first compared a two-word render against a five-word one and divided the
      long block by five, when the template's three-word cap meant only three had
      been drawn.
    - The second stayed inside the cap, at two words against three — but at the
      current constants those both set at the 150px maximum (3 x 159px of line
      still fits the 560px band), so there is no shrink between them to find. It
      "passed" only by miscounting: `_type_bbox` thresholds at grey > 200 and
      `_layout_left_column` draws the first word in the accent, which for the
      fallback template is amber at luminance 186. The first word was therefore
      invisible to the measurement while still counted in the divisor, and the
      arithmetic came out differently depending on which font the machine
      happened to resolve. It failed on Windows for that reason and nothing else.

    Word counts that genuinely exceed the band are what exercise the behaviour, and
    those are past the cap - so this asks the fitter, where the cap does not apply.
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (compose.THUMB_W, compose.THUMB_H)))
    max_w = compose.TEXT_RIGHT - compose.TEXT_LEFT
    max_h = compose.TEXT_BOTTOM - compose.TEXT_TOP

    # Short words, so the height constraint is what decides the size on every
    # machine. A long one would let font width shrink the two-word case too and
    # make the comparison depend on which face got resolved.
    def size_for(count: int) -> int:
        font, line_h = compose._fit_type(draw, ["WORD"] * count, max_w, max_h)
        assert line_h * count <= max_h, f"{count} words do not fit the band"
        return font.size

    sizes = [size_for(n) for n in (2, 3, 4, 5, 6)]
    assert sizes == sorted(sizes, reverse=True), f"not monotonic: {sizes}"
    assert sizes[-1] < sizes[0], f"six words set no smaller than two: {sizes}"


async def test_words_past_the_cap_are_dropped_rather_than_shrunk_into_illegibility(
    monkeypatch, tmp_path
):
    """168px wide on a phone is the real test; six words cannot survive it.

    Asserting the type stayed on the canvas proved nothing — a seven-word overlay
    only ever reaches the layout as its first three words, so the assertion held
    whatever the fitting code did. The claim worth testing is that the cap *drops*
    the surplus: three words and seven words must produce the identical render.
    """
    three = await _type_bbox(monkeypatch, tmp_path, "One Two Three")
    seven = await _type_bbox(monkeypatch, tmp_path, "One Two Three Four Five Six Seven")
    assert three == seven, "words past the cap changed the render instead of being dropped"
    assert seven is not None and seven[3] <= compose.THUMB_H


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


#: One decoded Gemini answer, for tests whose subject is not the envelope.
_GEMINI_OK = {
    "candidates": [
        {"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"fake-bytes").decode()}}]}}
    ]
}


def _stub_sequence(monkeypatch, *outcomes) -> list[str]:
    """Answer each POST with the next outcome, raising it if it is an exception.

    Returns the list of URLs called, which is how the retry tests count attempts —
    and a call past the end of the sequence fails the test rather than repeating
    the last answer, so an over-eager retry cannot pass quietly.

    Sleeping is stubbed out here as well. The delays are real seconds and only one
    test below cares what they are.
    """
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(images.asyncio, "sleep", _no_sleep)

    calls: list[str] = []
    remaining = list(outcomes)

    async def post(self, url, *_a, **_kw):
        calls.append(str(url))
        if not remaining:
            raise AssertionError(f"attempt {len(calls)} was not expected: {url}")
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return calls


def _use_storage(monkeypatch, root) -> None:
    """Point the module-level store at a tmp dir — it resolved its root at import."""
    from engine.storage import ObjectStore

    get_settings.cache_clear()
    monkeypatch.setattr(compose, "store", ObjectStore())
