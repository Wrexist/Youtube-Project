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

# The type column. Left-weighted, because the bottom-right ~15% is covered by the
# duration badge and the concept's focal point is meant to sit on the right.
TEXT_LEFT, TEXT_RIGHT = 70, 760
TEXT_TOP, TEXT_BOTTOM = 80, 640
MAX_TYPE_PX = 150


@dataclass
class Thumbnail:
    """A composed thumbnail and how its background came to exist.

    The image model and its prompt travel with the result rather than being looked up
    later: CLAUDE.md's provenance rule covers every generated artifact, and the
    background is a separate generation from the LLM call that designed the concept.
    """

    key: str
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

    With no image provider configured the background is a flat panel. That is a
    deliberate downgrade, not a failure — a first run with no API keys still produces
    a correctly composed thumbnail.
    """
    background = await images.generate(concept["image_prompt"])
    data = await asyncio.to_thread(
        _compose_thumbnail, concept, background.data if background else None
    )

    key = f"thumbnails/{job_id}-{index}.jpg"
    await store.put_bytes(data, key)
    if background is None:
        return Thumbnail(key=key)
    return Thumbnail(
        key=key,
        image_model=background.model,
        image_prompt=background.prompt,
        cost_usd=background.cost_usd,
    )


def _compose_thumbnail(concept: dict, background: bytes | None) -> bytes:
    """Background, scrim, then type. Synchronous — Pillow work belongs in a thread."""
    from PIL import ImageDraw

    canvas = _background_layer(background)
    # A scrim under the text. Without it, legibility depends on whatever the image
    # model happened to put on the left of the frame, which is not something to leave
    # to chance on the one asset that decides whether the video gets clicked.
    canvas.alpha_composite(_scrim())
    draw = ImageDraw.Draw(canvas)

    words = concept["overlay_text"].upper().split()[:5]
    font, line_h = _fit_type(draw, words)

    # Vertically centred in the band, so a two-word overlay and a five-word one are
    # both anchored the same way instead of both starting at a fixed y.
    block_h = line_h * len(words)
    y = TEXT_TOP + max(0, (TEXT_BOTTOM - TEXT_TOP - block_h) // 2)
    stroke = max(2, round(font.size * 0.055)) if hasattr(font, "size") else 8
    for word in words:
        draw.text(
            (TEXT_LEFT, y), word, font=font, fill="white", stroke_width=stroke, stroke_fill="black"
        )
        y += line_h

    buffer = io.BytesIO()
    # JPEG cannot hold the alpha the scrim needed, and YouTube's 2MB thumbnail
    # ceiling makes PNG the wrong output for a photographic background.
    canvas.convert("RGB").save(buffer, "JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def _fit_type(draw, words: list[str]):
    """The largest size at which every word fits the column and the block fits the band.

    Previously this drew at a fixed 150px from a fixed y=180 in fixed 160px steps,
    which silently ran off the canvas: the prompt asks for 3-5 words, and at four the
    last line is already half cut off, at five it is drawn entirely below the frame.
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

    for size in range(MAX_TYPE_PX, 39, -4):
        font = _at(size)
        line_h = round(size * 1.06)
        if line_h * len(words) > TEXT_BOTTOM - TEXT_TOP:
            continue
        if any(draw.textlength(word, font=font) > TEXT_RIGHT - TEXT_LEFT for word in words):
            continue
        return font, line_h

    # Five very long words genuinely cannot be set large. Smallest is still better
    # than overflowing, and the concept prompt caps this at 3-5 short words anyway.
    return _at(40), round(40 * 1.06)


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


def _scrim():
    """A left-to-right dark gradient, opaque under the type and clear by mid-frame."""
    from PIL import Image

    gradient = Image.new("L", (THUMB_W, 1))
    # Fully dark for the first 8%, then easing out to nothing at 65% — past the text
    # column but well short of the focal point the concept describes.
    fade_start, fade_end = int(THUMB_W * 0.08), int(THUMB_W * 0.65)
    for x in range(THUMB_W):
        if x <= fade_start:
            alpha = 205
        elif x >= fade_end:
            alpha = 0
        else:
            alpha = round(205 * (1 - (x - fade_start) / (fade_end - fade_start)))
        gradient.putpixel((x, 0), alpha)

    layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 255))
    layer.putalpha(gradient.resize((THUMB_W, THUMB_H)))
    return layer
