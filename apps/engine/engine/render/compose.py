"""Video composition.

MoviePy is CPU-bound and blocking, so every call here runs in a thread and reports
progress back to the event stream. A long-form render takes minutes; the Create
screen's pipeline view depends on that progress actually arriving.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

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
    from engine.workflows.media import download_all

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
    from moviepy.editor import (
        AudioFileClip,
        CompositeVideoClip,
        TextClip,
        VideoFileClip,
        concatenate_videoclips,
    )

    width, height = RESOLUTIONS[aspect]
    narration = AudioFileClip(str(audio_path))
    total = narration.duration

    # Lay clips out along the timeline in beat order, giving each beat a share of the
    # runtime proportional to its estimated length. This is what keeps footage
    # aligned with what is being said — the single biggest visual-quality lever.
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
            take = min(per_clip, source.duration)
            segments.append(_fit(source.subclip(0, take), width, height))
        report(0.25 + 0.45 * (index + 1) / max(len(beat_spans), 1), f"beat {index + 1}")

    if not segments:
        raise RuntimeError("no usable clips after download")

    video = concatenate_videoclips(segments, method="compose").set_duration(total)
    video = video.set_audio(narration)

    report(0.75, "burning subtitles")
    overlays = [
        TextClip(
            cue["text"],
            fontsize=int(height * 0.045),
            color="white",
            stroke_color="black",
            stroke_width=2,
            method="caption",
            size=(int(width * 0.86), None),
        )
        .set_start(cue["start"])
        .set_duration(max(cue["end"] - cue["start"], 0.4))
        .set_position(("center", int(height * 0.72)))
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
    resized = clip.resize(scale)
    return resized.crop(x_center=resized.w / 2, y_center=resized.h / 2, width=width, height=height)


async def transcribe(audio_path: Path) -> list[dict]:
    """Whisper fallback for TTS backends that return no word boundaries."""

    def _run() -> list[dict]:
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
        return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]

    return await asyncio.to_thread(_run)


async def make_thumbnail(concept: dict, *, job_id: str, index: int) -> str:
    """Generate the image, then compose the type ourselves.

    Overlay text is drawn here rather than requested from the image model: generated
    typography is unreliable, and a separate text layer is what lets Phase 8 swap
    variants without regenerating anything.
    """
    from PIL import Image, ImageDraw, ImageFont

    def _run() -> bytes:
        # Placeholder background until an image provider is wired in. The composition,
        # safe zones, and type treatment are the parts worth getting right first.
        canvas = Image.new("RGB", (1280, 720), (18, 18, 21))
        draw = ImageDraw.Draw(canvas)

        words = concept["overlay_text"].upper().split()[:5]
        try:
            font = ImageFont.truetype("arialbd.ttf", 150)
        except OSError:
            font = ImageFont.load_default(size=150)

        # Left-weighted: the bottom-right ~15% is covered by the duration badge.
        y = 180
        for word in words:
            draw.text((70, y), word, font=font, fill="white", stroke_width=8, stroke_fill="black")
            y += 160

        import io

        buffer = io.BytesIO()
        canvas.save(buffer, "JPEG", quality=88, optimize=True)
        return buffer.getvalue()

    data = await asyncio.to_thread(_run)
    path = await store.put_bytes(data, f"thumbnails/{job_id}-{index}.jpg")
    return f"thumbnails/{job_id}-{index}.jpg" if path else ""
