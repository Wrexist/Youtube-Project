"""Video composition.

MoviePy is CPU-bound and blocking, so every call here runs in a thread and reports
progress back to the event stream. A long-form render takes minutes; the Create
screen's pipeline view depends on that progress actually arriving.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from engine.providers import images
from engine.render import templates
from engine.services import bgm, effects, fonts
from engine.settings import get_settings
from engine.storage import store

ProgressFn = Callable[[float, str], Awaitable[None]]

RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


async def compose_video(
    *,
    clips: list[dict],
    beats: list,
    audio_path: Path,
    cues: list[dict],
    aspect: str,
    job_id: str,
    on_progress: ProgressFn,
) -> Path:
    from engine.services.stock import download_all

    await on_progress(0.05, "downloading footage")
    await download_all(clips)

    await on_progress(0.25, "composing")
    output = Path(get_settings().storage_root) / "tmp" / f"{job_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()

    def report(fraction: float, message: str) -> None:
        """Bridge from the render thread back to the async event stream."""
        asyncio.run_coroutine_threadsafe(on_progress(fraction, message), loop)

    await asyncio.to_thread(_render_sync, clips, beats, audio_path, cues, aspect, output, report)
    await on_progress(1.0, "done")
    return output


def _render_sync(
    clips: list[dict],
    beats: list,
    audio_path: Path,
    cues: list[dict],
    aspect: str,
    output: Path,
    report: Callable[[float, str], None],
) -> None:
    # MoviePy 2.x. The 1.x `moviepy.editor` namespace is gone, and the mutator
    # methods were renamed (`subclip`→`subclipped`, `set_*`→`with_*`, and the
    # geometry helpers gained past-tense names). Written against 2.x deliberately;
    # 1.x is EOL.
    from moviepy import (
        AudioFileClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )

    settings = get_settings()
    width, height = RESOLUTIONS[aspect]
    narration = AudioFileClip(str(audio_path))
    total = narration.duration

    # Lay clips out along the timeline in beat order, giving each beat a share of the
    # runtime proportional to its estimated length. This is what keeps footage
    # aligned with what is being said — the single biggest visual-quality lever.
    fade_s = settings.transition_fade_s
    segments = []
    beat_spans = _beat_spans(beats, total)
    for index, (start, end) in enumerate(beat_spans):
        beat_clips = [c for c in clips if c["beat_index"] == index and c.get("path")]
        if not beat_clips:
            continue
        span = end - start
        per_clip = span / len(beat_clips)
        for clip in beat_clips:
            try:
                source = VideoFileClip(clip["path"])
            except Exception as exc:  # noqa: BLE001 — a bad download must not kill the render
                logger.warning("skipping unreadable clip {}: {}", clip["path"], exc)
                continue
            # Dissolves overlap the timeline, so each clip has to carry the extra
            # `fade_s` that the overlap eats. Without it the video finishes short
            # of the narration and freezes on the last frame.
            take = min(per_clip + fade_s, source.duration)
            segments.append(_fit(source.subclipped(0, take), width, height))
        report(0.25 + 0.45 * (index + 1) / max(len(beat_spans), 1), f"beat {index + 1}")

    if not segments:
        raise RuntimeError("no usable clips after download")

    # Motion and dissolves go on after the whole timeline is known — the first
    # segment is treated differently, and that cannot be decided mid-loop.
    report(0.72, "applying motion")
    segments = [
        effects.style_segment(
            segment,
            index=index,
            count=len(segments),
            ken_burns=settings.ken_burns,
            fade_s=fade_s,
        )
        for index, segment in enumerate(segments)
    ]

    video = concatenate_videoclips(
        segments,
        method="compose",
        padding=effects.concat_padding(len(segments), fade_s),
    ).with_duration(total)

    track = bgm.resolve() if bgm.should_mix(settings.bgm_volume) else None
    video = video.with_audio(
        bgm.mix(narration, duration=total, track=track, volume=settings.bgm_volume)
    )

    report(0.75, "burning subtitles")
    # MoviePy 2 requires an explicit font path for TextClip — it no longer falls back
    # to an ImageMagick-resolved family name, and omitting it raises at construction.
    font = fonts.cached_resolve(settings.subtitle_font)
    overlays = [
        TextClip(
            text=cue["text"],
            font=font,
            font_size=int(height * 0.045),
            color="white",
            stroke_color="black",
            stroke_width=2,
            method="caption",
            size=(int(width * 0.86), None),
        )
        .with_start(cue["start"])
        .with_duration(max(cue["end"] - cue["start"], 0.4))
        .with_position(("center", int(height * 0.72)))
        for cue in cues
    ]

    final = CompositeVideoClip([video, *overlays], size=(width, height))

    report(0.85, "encoding")
    final.write_videofile(
        str(output),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        logger=None,
    )
    for clip in (*segments, narration, final):
        clip.close()


def _beat_spans(beats: list, total: float) -> list[tuple[float, float]]:
    """Map beats onto the real audio duration.

    The script's `est_seconds` are estimates and always drift from the synthesised
    audio, so they are used as *proportions* and rescaled to the actual runtime.
    """
    weights = [max(getattr(b, "est_seconds", 1.0), 0.5) for b in beats] or [1.0]
    scale = total / sum(weights)
    spans, cursor = [], 0.0
    for weight in weights:
        end = cursor + weight * scale
        spans.append((cursor, end))
        cursor = end
    return spans


def _fit(clip, width: int, height: int):
    """Scale-and-crop to the target frame. Never letterbox — black bars read as cheap."""
    scale = max(width / clip.w, height / clip.h)
    resized = clip.resized(scale)
    return resized.cropped(
        x_center=resized.w / 2, y_center=resized.h / 2, width=width, height=height
    )


async def transcribe(audio_path: Path) -> list[dict]:
    """Whisper fallback for TTS backends that return no word boundaries."""

    def _run() -> list[dict]:
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
        return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]

    return await asyncio.to_thread(_run)


THUMB_W, THUMB_H = 1280, 720

# The type column for the left-weighted layouts. Bottom-right ~15% is left clear
# throughout: that is where YouTube stamps the duration badge.
TEXT_LEFT, TEXT_RIGHT = 70, 760
TEXT_TOP, TEXT_BOTTOM = 80, 640
MAX_TYPE_PX = 150
BADGE_SAFE_Y = 640


@dataclass
class Thumbnail:
    """A composed thumbnail and how its background came to exist.

    The image model and its prompt travel with the result rather than being looked up
    later: CLAUDE.md's provenance rule covers every generated artifact, and the
    background is a separate generation from the LLM call that designed the concept.
    """

    key: str
    template: str = templates.FALLBACK
    image_model: str = ""
    image_prompt: str = ""
    cost_usd: float = 0.0

    @property
    def generated(self) -> bool:
        return bool(self.image_model)


async def make_thumbnail(concept: dict, *, job_id: str, index: int) -> Thumbnail:
    """Generate the background, then compose the type ourselves.

    Overlay text is drawn here rather than requested from the image model: generated
    typography is unreliable, and a separate text layer is what lets Phase 8 swap
    variants without regenerating anything.

    The concept's `template` chooses the layout — see `render/templates.py`. Three
    variants in three different templates is the whole point of generating three.

    With no image provider configured the background is a flat panel. That is a
    deliberate downgrade, not a failure — a first run with no API keys still produces
    a correctly composed thumbnail.
    """
    template = templates.get(concept.get("template"))
    prompt = f"{concept['image_prompt'].strip()} {template.image_direction}".strip()

    background = await images.generate(prompt)
    data = await asyncio.to_thread(
        _compose_thumbnail, concept, background.data if background else None, template
    )

    key = f"thumbnails/{job_id}-{index}.jpg"
    await store.put_bytes(data, key)
    if background is None:
        return Thumbnail(key=key, template=template.key)
    return Thumbnail(
        key=key,
        template=template.key,
        image_model=background.model,
        image_prompt=background.prompt,
        cost_usd=background.cost_usd,
    )


def _compose_thumbnail(concept: dict, background: bytes | None, template) -> bytes:
    """Background, then the template's own layout. Pillow work belongs in a thread."""
    canvas = _background_layer(background)
    accent = templates.accent_rgb(concept.get("accent") or template.accent)
    words = concept["overlay_text"].upper().split()[: template.max_words]

    layouts = {
        "left_column": _layout_left_column,
        "numeral": _layout_numeral,
        "versus": _layout_versus,
        "banner": _layout_banner,
        "centre_stage": _layout_centre_stage,
    }
    if not words:
        # A bad generation, not a reason to lose the image. The layout still runs so
        # the template's furniture — dividers, bars, scrims — is drawn either way;
        # each one simply has no words to set.
        logger.warning("thumbnail concept had no overlay text; composing the image alone")
    layouts.get(template.layout, _layout_left_column)(canvas, words, accent)

    buffer = io.BytesIO()
    # JPEG cannot hold the alpha the scrims needed, and YouTube's 2MB thumbnail
    # ceiling makes PNG the wrong output for a photographic background.
    canvas.convert("RGB").save(buffer, "JPEG", quality=88, optimize=True)
    return buffer.getvalue()


# ── layouts ─────────────────────────────────────────────────────────────────
#
# Each takes the canvas, the words, and an accent, and draws in place. They differ in
# where the type sits and how the background is darkened to carry it — a layout that
# only changed colour would not be a different thumbnail.


def _layout_left_column(canvas, words: list[str], accent) -> None:
    """Type stacked down the left, focal point right. The workhorse."""
    from PIL import ImageDraw

    canvas.alpha_composite(_side_scrim())
    draw = ImageDraw.Draw(canvas)
    font, line_h = _fit_type(draw, words, TEXT_RIGHT - TEXT_LEFT, TEXT_BOTTOM - TEXT_TOP)

    block_h = line_h * len(words)
    y = TEXT_TOP + max(0, (TEXT_BOTTOM - TEXT_TOP - block_h) // 2)
    for i, word in enumerate(words):
        # First word in the accent: it is the one read first at 168px, and an
        # all-white block reads as a caption rather than a hook.
        _draw_word(draw, (TEXT_LEFT, y), word, font, accent if i == 0 else (255, 255, 255))
        y += line_h


def _layout_numeral(canvas, words: list[str], accent) -> None:
    """First token set enormous, the rest small beneath it.

    Quantities are the hook in this archetype, so the number gets the frame and the
    supporting words get out of its way.
    """
    from PIL import ImageDraw

    canvas.alpha_composite(_side_scrim(fade_end=0.72, alpha=190))
    if not words:
        return  # this is the one layout that indexes rather than iterates
    draw = ImageDraw.Draw(canvas)

    numeral, rest = words[0], words[1:]
    big, _ = _fit_type(draw, [numeral], TEXT_RIGHT - TEXT_LEFT, 380, max_px=340)
    small, small_h = _fit_type(draw, rest or [""], TEXT_RIGHT - TEXT_LEFT, 200, max_px=110)

    # Measured, not derived from the point size. `draw.text` anchors to the ascender
    # while a numeral's ink starts well below it, so computing the next line from the
    # font size overlaps the two — "7" sitting on top of "DAYS".
    ink = draw.textbbox((0, 0), numeral, font=big, stroke_width=_stroke_for(big))
    numeral_h = ink[3] - ink[1]
    gap = 20

    block_h = numeral_h + gap + small_h * len(rest)
    top = max(TEXT_TOP, (THUMB_H - block_h) // 2)

    # Offset by the ink's own top so the block is placed by what is visible rather
    # than by the invisible ascender box around it.
    _draw_word(draw, (TEXT_LEFT, top - ink[1]), numeral, big, accent)

    y = top + numeral_h + gap
    for word in rest:
        _draw_word(draw, (TEXT_LEFT, y), word, small, (255, 255, 255))
        y += small_h


def _layout_versus(canvas, words: list[str], accent) -> None:
    """A divider down the middle, type centred across the top."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(canvas)

    # Accent divider, full height — this is what reads as "two things" at 168px.
    bar = 14
    draw.rectangle(
        [THUMB_W // 2 - bar // 2, 0, THUMB_W // 2 + bar // 2, THUMB_H], fill=(*accent, 255)
    )

    band_h = 250
    band = Image.new("RGBA", (THUMB_W, band_h), (0, 0, 0, 190))
    canvas.alpha_composite(band, (0, 0))

    font, lines, line_h = _fit_wrapped(draw, words, THUMB_W - 120, band_h - 40, max_px=130)
    y = (band_h - line_h * len(lines)) // 2
    for line in lines:
        width = draw.textlength(line, font=font)
        _draw_word(draw, ((THUMB_W - width) // 2, y), line, font, (255, 255, 255))
        y += line_h


def _layout_banner(canvas, words: list[str], accent) -> None:
    """A solid accent bar across the lower third, type on it.

    The highest-contrast option available: flat colour behind flat type survives any
    background and any amount of compression.
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(canvas)
    bar_h = 205
    # Stops short of the bottom-right badge zone rather than running full width.
    bar_w = THUMB_W - 180
    # Fully opaque. At 242 the background showed through, which is exactly the thing
    # this layout exists to prevent — flat colour behind flat type is what survives
    # a busy image and YouTube's compression.
    bar = Image.new("RGBA", (bar_w, bar_h), (*accent, 255))
    canvas.alpha_composite(bar, (0, THUMB_H - bar_h - 40))

    font, lines, line_h = _fit_wrapped(draw, words, bar_w - 90, bar_h - 44, max_px=120)
    y = THUMB_H - bar_h - 40 + (bar_h - line_h * len(lines)) // 2
    for line in lines:
        # Near-black on a saturated bar: the one place white would lose contrast.
        _draw_word(draw, (60, y), line, font, (14, 14, 16), stroke_fill=None)
        y += line_h


def _layout_centre_stage(canvas, words: list[str], accent) -> None:
    """Type centred over a vignette, subject behind it. For the reveal."""
    from PIL import ImageDraw

    canvas.alpha_composite(_vignette())
    draw = ImageDraw.Draw(canvas)

    font, line_h = _fit_type(draw, words, THUMB_W - 200, BADGE_SAFE_Y - 120, max_px=140)
    block_h = line_h * len(words)
    y = (THUMB_H - block_h) // 2
    for i, word in enumerate(words):
        width = draw.textlength(word, font=font)
        colour = accent if i == len(words) - 1 else (255, 255, 255)
        _draw_word(draw, ((THUMB_W - width) // 2, y), word, font, colour)
        y += line_h


# ── type ────────────────────────────────────────────────────────────────────


def _stroke_for(font) -> int:
    return max(2, round(getattr(font, "size", 100) * 0.055))


def _draw_word(draw, xy, word: str, font, fill, stroke_fill=(0, 0, 0)) -> None:
    stroke = _stroke_for(font) if stroke_fill else 0
    draw.text(xy, word, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _fit_type(draw, words: list[str], max_w: int, max_h: int, *, max_px: int = MAX_TYPE_PX):
    """The largest size at which every word fits `max_w` and the block fits `max_h`.

    The original drew at a fixed 150px from a fixed y=180 in fixed 160px steps, which
    silently ran off the canvas: the concept prompt asks for 3-5 words, and at four
    the last line was half cut off, at five it was drawn entirely below the frame.
    Nobody noticed while the background was a flat panel nobody looked at.
    """
    from PIL import ImageFont

    try:
        path = fonts.cached_resolve()
    except (OSError, RuntimeError):
        path = None

    def _at(size: int):
        if path is None:
            # A thumbnail in the default bitmap font is ugly but recoverable; the
            # render it belongs to has already succeeded by this point.
            return ImageFont.load_default(size=size)
        return ImageFont.truetype(path, size)

    words = words or [""]
    for size in range(max_px, 23, -4):
        font = _at(size)
        line_h = round(size * 1.06)
        if line_h * len(words) > max_h:
            continue
        if any(draw.textlength(word, font=font) > max_w for word in words):
            continue
        return font, line_h

    # Very long words genuinely cannot be set large. Smallest is still better than
    # overflowing, and the concept prompt caps this at a few short words anyway.
    return _at(24), round(24 * 1.06)


def _fit_wrapped(draw, words: list[str], max_w: int, max_h: int, *, max_px: int = MAX_TYPE_PX):
    """Like `_fit_type`, but packs words onto shared lines.

    One word per line is right for a tall left-hand column and wrong for a wide
    horizontal bar: four words stacked in a 230px band shrink to about 45px and are
    illegible at feed scale, while the same four words on two lines set at 100px.
    Returns the lines already wrapped, since the caller cannot re-derive them.
    """
    from PIL import ImageFont

    try:
        path = fonts.cached_resolve()
    except (OSError, RuntimeError):
        path = None

    def _at(size: int):
        if path is None:
            return ImageFont.load_default(size=size)
        return ImageFont.truetype(path, size)

    words = [w for w in words if w] or [""]
    for size in range(max_px, 23, -4):
        font = _at(size)
        line_h = round(size * 1.08)

        lines: list[str] = []
        current = ""
        overflowed = False
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_w:
                current = candidate
                continue
            if current:
                lines.append(current)
            # A single word wider than the box at this size: no wrap can save it.
            if draw.textlength(word, font=font) > max_w:
                overflowed = True
                break
            current = word
        if overflowed:
            continue
        if current:
            lines.append(current)

        if lines and line_h * len(lines) <= max_h:
            return font, lines, line_h

    return _at(24), [" ".join(words)], round(24 * 1.08)


def _background_layer(background: bytes | None):
    """The generated image cover-fitted to 16:9, or a flat panel when there is none."""
    from PIL import Image

    if background is None:
        return Image.new("RGBA", (THUMB_W, THUMB_H), (18, 18, 21, 255))

    try:
        source = Image.open(io.BytesIO(background)).convert("RGBA")
    except OSError:
        # Unreadable bytes from a provider must not lose a thumbnail that is
        # otherwise fine; the composed type is the part that carries the meaning.
        logger.warning("generated thumbnail background could not be decoded; using a flat panel")
        return Image.new("RGBA", (THUMB_W, THUMB_H), (18, 18, 21, 255))

    # Cover, not fit: neither provider offers exactly 1280x720, and letterboxing a
    # thumbnail wastes the little area it has.
    scale = max(THUMB_W / source.width, THUMB_H / source.height)
    resized = source.resize(
        (max(THUMB_W, round(source.width * scale)), max(THUMB_H, round(source.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - THUMB_W) // 2
    top = (resized.height - THUMB_H) // 2
    return resized.crop((left, top, left + THUMB_W, top + THUMB_H))


def _side_scrim(*, fade_end: float = 0.65, alpha: int = 205):
    """A left-to-right dark gradient, opaque under the type and clear by mid-frame.

    Without it, legibility depends on whatever the image model happened to put on the
    left of the frame — not something to leave to chance on the one asset that
    decides whether the video gets clicked.
    """
    from PIL import Image

    gradient = Image.new("L", (THUMB_W, 1))
    # Fully dark for the first 8%, then easing out — past the text column but well
    # short of the focal point the concept describes.
    start, end = int(THUMB_W * 0.08), int(THUMB_W * fade_end)
    for x in range(THUMB_W):
        if x <= start:
            value = alpha
        elif x >= end:
            value = 0
        else:
            value = round(alpha * (1 - (x - start) / (end - start)))
        gradient.putpixel((x, 0), value)

    layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 255))
    layer.putalpha(gradient.resize((THUMB_W, THUMB_H)))
    return layer


def _vignette():
    """Dark at the edges, clear at the centre — for type set over the subject.

    Radial rather than linear because centre-stage type has background on all four
    sides of it, so a one-directional gradient leaves half the letters unprotected.
    """
    from PIL import Image, ImageDraw, ImageFilter

    mask = Image.new("L", (THUMB_W, THUMB_H), 225)
    draw = ImageDraw.Draw(mask)
    # A soft clear ellipse over the middle. Blurred hard so no edge is visible —
    # a discernible oval reads as a mistake rather than as lighting.
    draw.ellipse([-140, -60, THUMB_W + 140, THUMB_H + 60], fill=95)
    mask = mask.filter(ImageFilter.GaussianBlur(90))

    layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 255))
    layer.putalpha(mask)
    return layer
