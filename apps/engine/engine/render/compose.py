"""Video composition.

MoviePy is CPU-bound and blocking, so every call here runs in a thread and reports
progress back to the event stream. A long-form render takes minutes; the Create
screen's pipeline view depends on that progress actually arriving.
"""

from __future__ import annotations

import asyncio
import io
import math
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from proglog import ProgressBarLogger

from engine.providers import images
from engine.render import templates
from engine.services import bgm, effects, fonts
from engine.settings import get_settings
from engine.storage import store

ProgressFn = Callable[[float, str], Awaitable[None]]

RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


class RenderAborted(Exception):
    """Raised inside the render thread when its abort event is set.

    Cancelling a job cancelled the *coroutine*, which does nothing whatsoever to a
    thread already inside `write_videofile` — MoviePy hands off to ffmpeg and does
    not come back for minutes. So a cancelled render kept a CPU saturated and, worse,
    kept holding its slot in `_render_slots`: `STUDIO_MAX_CONCURRENT_RENDERS` stopped
    bounding anything the moment anyone pressed Cancel. Cooperative because there is
    no other kind — the thread has to be asked, and it checks between beats and
    before each encode.
    """


#: How many source clips may have an open reader at once before beats start being
#: baked to intermediate files.
#:
#: Every `VideoFileClip` is an ffmpeg subprocess with its own buffers — measured at
#: roughly 165MB resident each, and they were *all* held from the first beat until
#: the encode finished, because the final composite still referenced them. A
#: long-form render with forty-odd sources peaked at 6.7GB and was the dominant term
#: in every OOM. Below this many sources the whole thing fits comfortably and the
#: extra encode below is not worth paying for.
MAX_OPEN_SOURCES = 8

#: The frame rate every encode in this module writes at. Named because `bake()`
#: has to snap window boundaries onto it — see there.
FPS = 30

#: Cached rendered cues. Two, not one: a composite asks for the colour frame and the
#: mask frame separately, and a cue on a boundary is asked for either side of it.
_CUE_CACHE = 2


async def compose_video(
    *,
    clips: list[dict],
    beats: list,
    audio_path: Path,
    cues: list[dict],
    aspect: str,
    job_id: str,
    on_progress: ProgressFn,
    abort: threading.Event | None = None,
) -> Path:
    from engine.services.stock import download_all

    abort = abort or threading.Event()

    await on_progress(0.05, "downloading footage")
    await download_all(clips)
    _abort_check(abort)

    await on_progress(0.25, "composing")
    output = Path(get_settings().storage_root) / "tmp" / f"{job_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()

    def report(fraction: float, message: str) -> None:
        """Bridge from the render thread back to the async event stream.

        Dropped once the render is aborting. The thread keeps running until its next
        check point, and the updates it emits in that window are for a job the API
        has already recorded as cancelled — appending them re-woke every SSE stream
        with progress for a stopped render. `run_coroutine_threadsafe` is also
        guarded, because the loop may be shutting down by the time this fires and
        the resulting `RuntimeError` would surface as a render failure.
        """
        if abort.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(on_progress(fraction, message), loop)
        except RuntimeError:  # loop closed or closing
            logger.debug("dropped a render progress update for a closing loop")

    # Held in its own task so the thread can be waited for even when the caller is
    # cancelled: `to_thread` awaited directly would let cancellation unwind past a
    # thread that is still inside ffmpeg, and whatever `finally` the caller uses to
    # release its render slot would run while the encode was still going.
    work = asyncio.create_task(
        asyncio.to_thread(
            _render_sync, clips, beats, audio_path, cues, aspect, output, report, abort
        )
    )
    try:
        await asyncio.shield(work)
    except asyncio.CancelledError:
        abort.set()
        # Not `wait_for`: the point is to *not* return until the thread is gone.
        # It checks between beats and before each encode, so this is bounded by one
        # window encode at worst.
        with suppress(Exception):
            await work
        raise
    except BaseException:
        # A failure inside the thread still leaves nothing else running, but an
        # abort raised elsewhere must not orphan it.
        abort.set()
        raise

    await on_progress(1.0, "done")
    return output


def _abort_check(abort: threading.Event | None) -> None:
    """Raise if this render has been asked to stop. Called from both threads."""
    if abort is not None and abort.is_set():
        raise RenderAborted("render cancelled")


class _AbortLogger(ProgressBarLogger):
    """A proglog logger whose only job is to interrupt an encode in progress.

    Checking the abort flag between beats is not enough on its own: one
    `write_videofile` is most of a render's wall clock, and a cancel that lands
    just after it starts would otherwise wait out the whole encode — measured at
    26 seconds on a short test render, and minutes on a real one. MoviePy calls
    into its logger once per written frame, which is the only interruption point
    ffmpeg gives us from this side, so raising there stops it within a frame.

    MoviePy's writer holds its subprocess in a context manager, so the exception
    closes ffmpeg on the way out rather than orphaning it.
    """

    def __init__(self, abort: threading.Event) -> None:
        super().__init__()
        self._abort = abort

    def bars_callback(self, bar, attr, value, old_value=None) -> None:  # noqa: ANN001
        if self._abort.is_set():
            raise RenderAborted("render cancelled")

    @classmethod
    def for_(cls, abort: threading.Event | None):
        """The logger, or `None` — MoviePy's "no progress output" value.

        `None` rather than an always-false logger so that a render with no abort
        signal (a direct `_render_sync` call, most of the tests) behaves exactly as
        it did before this existed.
        """
        return cls(abort) if abort is not None else None


def _render_sync(
    clips: list[dict],
    beats: list,
    audio_path: Path,
    cues: list[dict],
    aspect: str,
    output: Path,
    report: Callable[[float, str], None],
    abort: threading.Event | None = None,
) -> None:
    # MoviePy 2.x. The 1.x `moviepy.editor` namespace is gone, and the mutator
    # methods were renamed (`subclip`→`subclipped`, `set_*`→`with_*`, and the
    # geometry helpers gained past-tense names). Written against 2.x deliberately;
    # 1.x is EOL.
    from moviepy import (
        AudioFileClip,
        ColorClip,
        CompositeVideoClip,
        ImageClip,
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
    beat_spans = _beat_spans(beats, total)
    groups: list[tuple[float, Any]] = []  # (start, clip covering that beat's span)
    segments: list[Any] = []  # every underlying clip, for close() at the end

    # Beats are baked to intermediate files in windows once there are enough sources
    # to matter — see MAX_OPEN_SOURCES. `baked_upto` is the boundary: everything
    # before it is a single reader onto a file on disk, everything after it still
    # holds its own sources open.
    window = _window_size(sum(1 for c in clips if c.get("path")))
    scratch: list[Path] = []
    baked_upto = 0

    # Everything from here is inside the try, so the cleanup below runs on *any*
    # exit — including `RenderAborted`. It used to guard only the final encode, so a
    # cancel during the beat loop escaped with every source reader still open and
    # every baked window still on disk: a handful of orphaned ffmpeg processes and
    # half a gigabyte in `storage/tmp` per cancelled render.
    final = None
    try:

        def bake() -> None:
            """Flatten the beats built since the last bake into one intermediate file.

            The point is the `close()` at the end: an open `VideoFileClip` is an ffmpeg
            subprocess that stays resident for as long as the final composite might read
            from it, which used to be "until the encode finished". Writing the window out
            and reopening it as one clip trades a second encode of that span for giving
            every one of those subprocesses back.
            """
            nonlocal baked_upto
            fresh = groups[baked_upto:]
            if not fresh or not segments:
                return

            window_start = fresh[0][0]
            window_end = max(start + clip.duration for start, clip in fresh)

            # Snap the window's length up to a whole frame before compositing.
            #
            # A window is written out at 30fps, so ffmpeg can only encode a whole number
            # of frames: a span of 3.0433s is 91.3 frames, becomes 91, and the reopened
            # clip is 10ms short of the beat boundary where the *next* window starts.
            # Nothing covers those 10ms, so the black `ColorClip` base of the final
            # composite shows through — a hard black flash at every window seam, roughly
            # one every seven beats on a long render, which reads as corrupt footage
            # rather than as an arithmetic error.
            #
            # Up rather than to-nearest, so the snapped window always reaches *past* the
            # next window's start and the two overlap by under a frame instead of
            # risking a gap. The next window is later in the final composite's list and
            # therefore painted over the overlap; a gap has nothing to paint at all.
            span = max(math.ceil((window_end - window_start) * FPS), 1) / FPS
            window_end = window_start + span

            composite = CompositeVideoClip(
                [
                    ColorClip((width, height), color=(0, 0, 0), duration=span),
                    *[clip.with_start(start - window_start) for start, clip in fresh],
                ],
                size=(width, height),
            ).with_duration(span)

            path = output.parent / f"{output.stem}-w{len(scratch)}.mp4"
            # Said out loud: a window encode is tens of seconds on a long-form render,
            # and a progress view that sits on "beat 12" through all of it looks stuck.
            report(
                0.25 + 0.45 * len(groups) / max(len(beat_spans), 1),
                f"consolidating beats {baked_upto + 1}-{len(groups)}",
            )
            # The last cheap moment to stop: a window encode is tens of seconds and
            # nothing interrupts ffmpeg once it has started.
            _abort_check(abort)
            # Registered for cleanup *before* it is written, not after: an encode
            # interrupted by a cancel leaves a partial file behind, and one recorded
            # only on success is one the `finally` never hears about — so every
            # cancelled render left a window in `storage/tmp` forever.
            scratch.append(path)
            # Fast and near-lossless: this file is read once by the final pass and then
            # deleted, so encode time matters and a visually invisible generation loss
            # does not. `medium`/default CRF here would roughly double the render.
            composite.write_videofile(
                str(path),
                fps=FPS,
                codec="libx264",
                audio=False,
                preset="ultrafast",
                ffmpeg_params=["-crf", "18"],
                threads=4,
                logger=_AbortLogger.for_(abort),
            )
            composite.close()
            for source in segments:
                source.close()
            segments.clear()

            # Forced back to the intended span rather than trusting the file. Snapping
            # above makes the *request* a whole number of frames; this makes the result
            # one, because an encoder that drops a trailing frame — routine with
            # `ultrafast` and a fractional frame count — reopens as a clip 33ms short,
            # which is the same black seam by another route.
            groups[baked_upto:] = [(window_start, _cover_file_span(VideoFileClip(str(path)), span))]
            baked_upto = len(groups)

        for index, (start, end) in enumerate(beat_spans):
            # Between beats, which is the only granularity available: within one beat
            # the work is a download-free `subclipped` and a couple of effects, and the
            # long waits are the encodes, which have their own check.
            _abort_check(abort)
            span = end - start
            beat_clips = [c for c in clips if c["beat_index"] == index and c.get("path")]

            built = []
            per_clip = span / len(beat_clips) if beat_clips else span
            for clip in beat_clips:
                # Dissolves overlap the timeline, so each clip has to carry the extra
                # `fade_s` that the overlap eats. Without it the video finishes short
                # of the narration and freezes on the last frame.
                want = per_clip + fade_s
                try:
                    if clip.get("kind") == "image":
                        # Generated b-roll, for a beat no stock library could serve.
                        # A still has no duration of its own, so it is simply held
                        # for the whole slot — and Ken Burns further down is what
                        # keeps it from reading as a frozen frame.
                        source = ImageClip(clip["path"]).with_duration(want)
                        take = want
                    else:
                        source = VideoFileClip(clip["path"])
                        take = min(want, source.duration)
                except Exception as exc:  # noqa: BLE001 — a bad download must not kill the render
                    logger.warning("skipping unreadable clip {}: {}", clip["path"], exc)
                    continue
                built.append(_fit(source.subclipped(0, take), width, height))

            if not built:
                # A beat with no usable footage used to be `continue`d, which silently
                # shortened the timeline and dragged every later beat earlier. Beats are
                # positioned absolutely now, so a `continue` no longer shifts anything —
                # but it leaves the black base clip showing through for the whole span,
                # which is what the log line here used to claim it was avoiding.
                #
                # So actually hold the previous shot: stretch the last group to run
                # through this beat's end. `_cover_span` loops, so it reads as b-roll
                # rather than as a freeze.
                if groups:
                    prev_start, prev_group = groups[-1]
                    groups[-1] = (prev_start, _cover_span(prev_group, end - prev_start))
                    logger.warning(
                        "beat {} has no usable footage; holding beat {}'s shot across it",
                        index + 1,
                        index,
                    )
                else:
                    # Nothing to hold — this is the first beat. Black is the only honest
                    # option, and saying so beats claiming a shot that does not exist.
                    logger.warning(
                        "beat {} has no usable footage and nothing precedes it; "
                        "it will render black",
                        index + 1,
                    )
                continue

            segments.extend(built)
            styled = [
                effects.style_segment(
                    segment, index=i, count=len(built), ken_burns=settings.ken_burns, fade_s=fade_s
                )
                for i, segment in enumerate(built)
            ]
            group = (
                styled[0]
                if len(styled) == 1
                else concatenate_videoclips(
                    styled, method="compose", padding=effects.concat_padding(len(styled), fade_s)
                )
            )
            groups.append((start, _cover_span(group, span)))
            report(0.25 + 0.45 * (index + 1) / max(len(beat_spans), 1), f"beat {index + 1}")

            # At a beat boundary, never inside one: a beat's clips are concatenated with
            # negative padding for the dissolve, and splitting that across two files
            # would put a hard cut in the middle of a transition.
            if window and len(segments) >= window:
                bake()

        # Whatever is left over after the last bake stays open, deliberately: it is
        # fewer than `window` sources by construction, so it is already inside the
        # budget, and baking it would pay for an encode that saves nothing.

        if not groups:
            raise RuntimeError("no usable clips after download")

        report(0.72, "placing beats")
        # Each beat is *positioned* at its own start rather than butted onto the end of
        # the previous one. Concatenation assumed every beat's footage exactly filled its
        # span; when a source was shorter than its slot — routine, since stock clips can
        # be as short as 3s while a slot can be 5s or more — the timeline came up short,
        # every later beat played early against the narration, and `.with_duration(total)`
        # padded the difference with transparent frames that render as black. Measured on
        # a 10s narration with 2s sources: 5 seconds of black and beat 2 playing under
        # beat 1's audio.
        video = CompositeVideoClip(
            [ColorClip((width, height), color=(0, 0, 0), duration=total)]
            + [group.with_start(start) for start, group in groups],
            size=(width, height),
        ).with_duration(total)

        # The configured track, not whatever `resolve()` felt like. It has always
        # taken a name and this was always calling it bare, so every render drew a
        # random bed from the directory and a chosen one was unreachable.
        track = bgm.resolve(settings.bgm_track) if bgm.should_mix(settings.bgm_volume) else None
        video = video.with_audio(
            bgm.mix(narration, duration=total, track=track, volume=settings.bgm_volume)
        )

        report(0.75, "burning subtitles")
        # MoviePy 2 requires an explicit font path for TextClip — it no longer falls back
        # to an ImageMagick-resolved family name, and omitting it raises at construction.
        font = fonts.cached_resolve(settings.subtitle_font)
        overlay = _subtitle_overlay(cues, width=width, height=height, font=font, duration=total)

        final = CompositeVideoClip([video, overlay], size=(width, height)) if overlay else video

        report(0.85, "encoding")
        # The final encode is the long one. Checked here so a cancel that arrived
        # during subtitle burn-in is honoured before ffmpeg starts, and interrupted
        # by `_AbortLogger` once it has.
        _abort_check(abort)
        final.write_videofile(
            str(output),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="medium",
            logger=_AbortLogger.for_(abort),
        )
    finally:
        # `overlay` is deliberately absent: `Clip.close()` is a documented no-op for
        # anything that is not backed by a reader, so adding it here would free
        # nothing and only suggest that it did. What it holds — at most two rendered
        # cues — is released with the clip itself.
        #
        # The baked windows are here because they *are* readers, and in a `finally`
        # because their files have to go either way: a failed encode that leaves half
        # a gigabyte of intermediates behind in `storage/tmp` is a slow disk leak
        # nobody would connect back to this.
        #
        # `final` is None when the run stopped before the composite was built — an
        # abort during the beat loop — and the ungrouped tail of `groups` is closed
        # here too, since an abort can leave beats that were never baked.
        for clip in (*segments, *(g for _, g in groups), narration, final):
            if clip is not None:
                with suppress(Exception):
                    clip.close()
        for path in scratch:
            path.unlink(missing_ok=True)


def _window_size(sources: int) -> int:
    """How many source clips to hold open before baking a window, or 0 for never.

    Balanced rather than fixed. Baking leaves one reader per completed window, so
    peak readers is `max(window, sources / window)` and the minimum of that is at
    the square root — 40 sources becomes about seven open readers instead of forty,
    with seven intermediate encodes rather than thirty-nine.
    """
    if sources <= MAX_OPEN_SOURCES:
        return 0
    return max(2, math.ceil(math.sqrt(sources)))


def _wrap_caption(text: str, *, font: str, size: int, max_w: int) -> str:
    """Break a cue onto lines at spaces, never inside a word.

    MoviePy's `method="caption"` does its own wrapping and gets this wrong: a
    rendered frame from a real 9:16 job read

        He finished a war he
        started as a foot soldie
        r

    which is the kind of thing a viewer reads as a broken app rather than as a
    typo. Measuring with the same font the clip will use means the line that fits
    here is the line that fits there.

    A single word wider than the box is left alone on its own line and allowed to
    overflow: there is no break that helps, and hyphenating is not this function's
    decision to make.
    """
    from PIL import ImageFont

    try:
        face = ImageFont.truetype(font, size)
    except OSError:  # pragma: no cover - a font that resolved but will not load
        return text

    def width_of(line: str) -> float:
        return face.getbbox(line)[2] if line else 0

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and width_of(candidate) > max_w:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines) or text


def _caption_bitmap(text: str, *, font: str, size: int, max_w: int):
    """One cue, drawn with Pillow, as (rgb, alpha).

    Drawn here rather than by `TextClip` because MoviePy sizes the bitmap wrong
    for multi-line text and then crops its own output. Measured on a real cue at
    the 9:16 caption size: two lines of 86px type were allocated a 170px canvas,
    and the second line came out sliced in half horizontally. That is what put
    "dollars." on screen with its bottom missing. Both `method="label"` and
    `method="caption"` do it, so there was no MoviePy-side setting to change.

    Pillow gives the line height, the stroke and the centring explicitly, which
    is all this ever needed — and compose.py already draws every thumbnail this
    way, so it is the same dependency and the same idiom.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    try:
        face = ImageFont.truetype(font, size)
    except OSError:  # pragma: no cover - resolved but unloadable
        face = ImageFont.load_default(size=size)

    lines = _wrap_caption(text, font=font, size=size, max_w=max_w).split("\n")
    stroke = max(2, size // 14)
    # 1.25 rather than the font's own metric: caption faces vary, and a fixed
    # multiple keeps two-line cues the same height whatever font is resolved.
    line_h = int(size * 1.25)
    pad = stroke * 3

    widths = []
    for line in lines:
        left, _, right, _ = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
            (0, 0), line, font=face, stroke_width=stroke
        )
        widths.append(right - left)

    box_w = max([*widths, 1]) + pad * 2
    box_h = line_h * len(lines) + pad * 2

    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    for i, line in enumerate(lines):
        draw.text(
            (box_w // 2, pad + i * line_h),
            line,
            font=face,
            fill=(255, 255, 255, 255),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 255),
            anchor="ma",  # middle-ascender: centred, and top-aligned per line
        )

    array = np.asarray(canvas, dtype="uint8")
    return array[:, :, :3].copy(), (array[:, :, 3].astype("float64") / 255.0)


def _subtitle_overlay(cues: list[dict], *, width: int, height: int, font: str, duration: float):
    """One clip that draws whichever cue is on screen, instead of one clip per cue.

    This was a list comprehension building a `TextClip` per cue up front. A
    `TextClip` rasterises at construction, so a 9:16 caption is about 3.6MB of
    bitmap, and a full script is hundreds of cues — every one of them resident from
    the moment the list was built until the encode finished, for a frame that is on
    screen for two seconds. Measured at 1,227MB peak for 200 cues.

    Here the bitmaps are built on demand and at most `_CUE_CACHE` are kept, so the
    cost is bounded by the cache rather than by the length of the script. Frames are
    cached already composited onto the full canvas: the composite asks for colour and
    mask separately at the same `t`, and pasting twice per frame would undo the point.
    """
    import numpy as np
    from moviepy import VideoClip

    if not cues:
        return None

    # Sorted so the lookup can stop early, and because a cue list assembled from two
    # TTS backends is not guaranteed to arrive in order.
    ordered = sorted(cues, key=lambda c: c["start"])
    spans = [(c["start"], max(c["end"], c["start"] + 0.4), c["text"]) for c in ordered]
    font_px = int(height * 0.045)
    box_w_limit = int(width * 0.86)
    cache: dict[int, tuple[Any, Any]] = {}
    blank_rgb = np.zeros((height, width, 3), dtype="uint8")
    blank_mask = np.zeros((height, width), dtype="float64")

    def active(t: float) -> int | None:
        for index, (start, end, _) in enumerate(spans):
            if start <= t < end:
                return index
            if start > t:
                break
        return None

    def render(index: int) -> tuple[Any, Any]:
        cached = cache.get(index)
        if cached is not None:
            return cached

        bitmap, alpha = _caption_bitmap(spans[index][2], font=font, size=font_px, max_w=box_w_limit)

        rgb = blank_rgb.copy()
        mask = blank_mask.copy()
        box_h, box_w = bitmap.shape[0], bitmap.shape[1]
        # Centred horizontally, 72% down the frame — the same placement the
        # per-cue clips used via `.with_position(("center", height * 0.72))`.
        left = max(0, (width - box_w) // 2)
        top = max(0, min(int(height * 0.72), height - box_h))
        box_h, box_w = min(box_h, height - top), min(box_w, width - left)
        rgb[top : top + box_h, left : left + box_w] = bitmap[:box_h, :box_w]
        if alpha is not None:
            mask[top : top + box_h, left : left + box_w] = alpha[:box_h, :box_w]
        else:
            mask[top : top + box_h, left : left + box_w] = 1.0

        # Bounded, and evicted oldest-first. Playback is sequential, so one entry
        # would already be enough; two covers the frame that straddles a boundary.
        while len(cache) >= _CUE_CACHE:
            del cache[next(iter(cache))]
        cache[index] = (rgb, mask)
        return cache[index]

    def frame(t: float):
        index = active(t)
        return blank_rgb if index is None else render(index)[0]

    def mask_frame(t: float):
        index = active(t)
        return blank_mask if index is None else render(index)[1]

    overlay = VideoClip(frame_function=frame, duration=duration)
    overlay.mask = VideoClip(frame_function=mask_frame, duration=duration, is_mask=True)
    return overlay


def _cover_span(group, span: float):
    """Make a beat's footage last exactly as long as the beat does.

    Sourced footage is regularly shorter than the slot it has to fill: stock clips
    are accepted from 3 seconds and a beat's share of the runtime can be well past
    that. Truncating to whatever the source had was what let the timeline finish
    short of the narration.

    Looped rather than frozen. A still frame held for two seconds reads as a
    playback fault; repeated b-roll reads as b-roll.
    """
    from moviepy.video.fx import Loop

    if group.duration >= span - 0.02:
        return group.with_duration(span)
    return group.with_effects([Loop(duration=span)]).with_duration(span)


def _cover_file_span(clip, span: float):
    """`_cover_span` for a clip backed by a *file*, with no tolerance.

    Separate from `_cover_span` because of the one thing that differs: reading a
    file-backed clip past its last frame does not hold the last frame, it returns
    black. `_cover_span` treats a shortfall under 20ms as "close enough" and simply
    calls `with_duration(span)`, which is right for the in-memory composites it is
    used on and is precisely wrong here — a window file that reports 3.03s asked for
    3.0333s yields one black frame at the seam, the exact defect this is meant to
    remove.

    So: truncate when long, loop when short, never extend. The worst case is one
    repeated frame at a window boundary instead of one black one.
    """
    from moviepy.video.fx import Loop

    if clip.duration >= span:
        return clip.with_duration(span)
    return clip.with_effects([Loop(duration=span)]).with_duration(span)


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
    """Scale-and-crop to the target frame. Never letterbox — black bars read as cheap.

    Returns the clip untouched when it is already the target size, which is the
    common case and was costing real time: stock providers are asked for footage
    in the video's aspect, so most sources arrive at exactly 1080x1920 and every
    one of them was still being run through `resized(1.0)` and a full-frame
    `cropped` — measured at 21ms per frame, on every frame, for two resamples
    that could not change a pixel. Over a 2:41 vertical render that is ~100
    seconds spent copying an image onto itself.
    """
    if clip.w == width and clip.h == height:
        return clip

    scale = max(width / clip.w, height / clip.h)
    # `resized(1.0)` is not free either — it still wraps the clip in a per-frame
    # resampler — so it is skipped when the scale is already right and only the
    # crop is needed.
    resized = clip if abs(scale - 1.0) < 1e-9 else clip.resized(scale)
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
    # `.get`, not `[...]`. `concept` is parsed LLM output, and a model that omits a
    # key — or returns null for it — would otherwise raise KeyError/AttributeError
    # and fail the whole thumbnail stage. The template's own direction is enough to
    # generate against on its own.
    prompt = f"{str(concept.get('image_prompt') or '').strip()} {template.image_direction}".strip()

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
    words = str(concept.get("overlay_text") or "").upper().split()[: template.max_words]

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
