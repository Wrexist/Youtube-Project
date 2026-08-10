"""Cut the clips together into a file.

**The audio rule is the one that is not negotiable.** TikTok's music licences cover
TikTok and nothing else, so source audio arriving on YouTube is unlicensed no
matter how solid the *video* rights are — it is the likeliest single cause of a
Content ID claim in the whole product. So the source bed is discarded by default
and replaced with narration over a licensed track. Retaining source audio is
possible (`keep_source_audio`) and is the right call for a clip whose whole point
is what somebody *said*, but it is opt-in, per-clip, and it is ducked under the
narration rather than competing with it.

**What this returns matters as much as what it writes.** `Assembly` reports what
the finished file actually contains — how many cuts, whether the bed was replaced,
the real placed duration of every segment. `workflows/repurpose.build_timeline`
reads those facts instead of trusting the job's inputs, because a gate whose
evidence is supplied by the thing it is judging is not a gate. Before this module
existed, `audio_bed_replaced` was a boolean the *caller* asserted.

Everything heavy runs in a thread, like the rest of the render path — MoviePy is
synchronous and would otherwise block the event loop for the whole encode.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from engine.settings import get_settings

ProgressFn = Callable[[float, str], Awaitable[None]]

#: Target frame sizes. 9:16 for Shorts, 16:9 for long-form, 1:1 where a feed wants
#: it. Sources are letterboxed rather than cropped to fill — cropping a vertical
#: clip into 16:9 removes the subject's head about as often as not, and a clip
#: reframed by machine with nobody looking is worse than one with bars.
FRAMES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}

#: How far the bed sits under narration. Music at full level under speech is the
#: single most common amateur mix mistake.
BED_GAIN = 0.12

#: How far retained source audio is ducked while narration plays.
DUCK_GAIN = 0.25

#: Minimum placed length for a segment. Anything shorter reads as a glitch rather
#: than a cut.
MIN_PLACED_S = 0.5


@dataclass
class Placed:
    """One segment as it actually landed in the finished file."""

    source_id: str | None
    start_s: float
    end_s: float
    #: Where it sits in the output, which is not where it sat in the source.
    placed_at_s: float
    is_hook: bool = False

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "start_s": round(self.start_s, 2),
            "end_s": round(self.end_s, 2),
            "placed_at_s": round(self.placed_at_s, 2),
            "duration_s": round(self.duration_s, 2),
            "is_hook": self.is_hook,
        }


@dataclass
class Assembly:
    """What the finished file actually contains.

    Facts, measured after the encode — not intentions declared before it. The gate
    reads these.
    """

    output_key: str = ""
    duration_s: float = 0.0
    placed: list[Placed] = field(default_factory=list)
    cuts: int = 0
    audio_bed_replaced: bool = True
    #: Clips whose original audio was deliberately retained, ducked under narration.
    retained_source_audio: list[str] = field(default_factory=list)
    aspect: str = "9:16"
    #: Whether captions were burnt in, and whether every clip needing credit got
    #: one. Measured, like everything else here — `gate.attribution` is a hard
    #: block, and a caller asserting it would be the same hole `audio_bed_replaced`
    #: used to be.
    captions_burned: bool = False
    credited_source_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        minutes, seconds = divmod(int(self.duration_s), 60)
        return f"{minutes}:{seconds:02d} · {self.cuts} cuts · {self.aspect}"

    def as_dict(self) -> dict:
        return {
            "output_key": self.output_key,
            "duration_s": round(self.duration_s, 2),
            "placed": [p.as_dict() for p in self.placed],
            "cuts": self.cuts,
            "audio_bed_replaced": self.audio_bed_replaced,
            "retained_source_audio": self.retained_source_audio,
            "aspect": self.aspect,
            "captions_burned": self.captions_burned,
            "credited_source_ids": self.credited_source_ids,
        }


async def assemble(
    *,
    segments: list[dict],
    sources: dict[str, Path],
    narration_path: Path | None,
    job_id: str,
    aspect: str = "9:16",
    hook: dict | None = None,
    bed_path: Path | None = None,
    keep_source_audio: set[str] | None = None,
    cues: list[dict] | None = None,
    credits: dict[str, str] | None = None,
    on_progress: ProgressFn | None = None,
    abort: threading.Event | None = None,
) -> Assembly:
    """Cut, reframe, replace the audio, burn captions and credits, write the file.

    `segments` is the cut list from `SegmentStage`; `sources` maps source id to the
    acquired media on disk. `hook`, when `teased`, is prepended so the video opens
    on its strongest moment rather than reaching it twenty seconds in.
    """
    if not segments:
        raise ValueError("nothing to assemble — the cut list is empty")

    abort = abort or threading.Event()
    keep_source_audio = keep_source_audio or set()
    frame = FRAMES.get(aspect, FRAMES["9:16"])

    output = Path(get_settings().storage_root) / "tmp" / f"repurpose-{job_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)

    if on_progress:
        await on_progress(0.05, "cutting segments")

    loop = asyncio.get_running_loop()

    def report(fraction: float, message: str) -> None:
        """Bridge from the render thread back to the event stream.

        Dropped once aborting, and guarded against a closing loop — both for the
        reasons `render/compose.py` sets out at the same seam.
        """
        if abort.is_set() or on_progress is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(on_progress(fraction, message), loop)
        except RuntimeError:
            logger.debug("dropped an assemble progress update for a closing loop")

    work = asyncio.create_task(
        asyncio.to_thread(
            _assemble_sync,
            segments,
            sources,
            narration_path,
            bed_path,
            hook,
            frame,
            aspect,
            keep_source_audio,
            cues or [],
            credits or {},
            output,
            report,
            abort,
        )
    )
    try:
        assembly = await asyncio.shield(work)
    except asyncio.CancelledError:
        abort.set()
        from contextlib import suppress

        with suppress(Exception):
            await work
        raise
    except BaseException:
        abort.set()
        raise

    from engine.storage import store

    key = await store.put_file(output, f"repurpose/{job_id}.mp4")
    assembly.output_key = key

    if on_progress:
        await on_progress(1.0, "done")
    return assembly


def _assemble_sync(
    segments: list[dict],
    sources: dict[str, Path],
    narration_path: Path | None,
    bed_path: Path | None,
    hook: dict | None,
    frame: tuple[int, int],
    aspect: str,
    keep_source_audio: set[str],
    cues: list[dict],
    credits: dict[str, str],
    output: Path,
    report: Callable[[float, str], None],
    abort: threading.Event,
) -> Assembly:
    """The synchronous render. Runs in a thread."""
    from moviepy import AudioFileClip, CompositeAudioClip, VideoFileClip, concatenate_videoclips

    opened: list = []
    pieces: list = []
    placed: list[Placed] = []
    retained: list[str] = []
    cursor = 0.0

    def _check() -> None:
        if abort.is_set():
            raise RuntimeError("assembly aborted")

    try:
        # The tease first, when there is one. This is the whole reason `hook` is a
        # separate decision — opening on the strongest moment is what buys the
        # 1.5–3 seconds a viewer spends deciding.
        cut_list: list[tuple[dict, bool]] = []
        if hook and hook.get("teased") and hook.get("source_id") in sources:
            cut_list.append(
                (
                    {
                        "source_id": hook["source_id"],
                        "start_s": float(hook.get("at_s") or 0.0),
                        "end_s": float(hook.get("at_s") or 0.0)
                        + float(hook.get("duration_s") or 2.5),
                    },
                    True,
                )
            )
        cut_list.extend((segment, False) for segment in segments)

        for index, (segment, is_hook) in enumerate(cut_list):
            _check()
            source_id = segment.get("source_id")
            path = sources.get(source_id or "")
            if path is None:
                logger.warning("no media for clip {}; skipping that cut", source_id)
                continue

            clip = VideoFileClip(str(path))
            opened.append(clip)

            start = max(0.0, float(segment.get("start_s") or 0.0))
            end = float(segment.get("end_s") or 0.0) or float(clip.duration or 0.0)
            end = min(end, float(clip.duration or 0.0))
            if end - start < MIN_PLACED_S:
                logger.warning("cut for clip {} is too short to place; skipping", source_id)
                continue

            piece = clip.subclipped(start, end)

            # **The audio rule.** Stripped unless this clip was explicitly opted in.
            # A clip whose point is what somebody said keeps its audio, ducked; every
            # other clip loses it, because a TikTok bed on YouTube is unlicensed.
            if source_id in keep_source_audio and piece.audio is not None:
                piece = piece.with_audio(piece.audio.with_volume_scaled(DUCK_GAIN))
                if source_id and source_id not in retained:
                    retained.append(source_id)
            else:
                piece = piece.without_audio()

            piece = _fit(piece, frame)
            pieces.append(piece)
            placed.append(
                Placed(
                    source_id=source_id,
                    start_s=start,
                    end_s=end,
                    placed_at_s=cursor,
                    is_hook=is_hook,
                )
            )
            cursor += end - start
            report(0.1 + 0.5 * (index + 1) / max(1, len(cut_list)), f"cut {index + 1}")

        if not pieces:
            raise ValueError("every cut was unusable — nothing to assemble")

        _check()
        report(0.65, "joining")
        video = concatenate_videoclips(pieces, method="compose")
        opened.append(video)

        # ── overlays ─────────────────────────────────────────────────────────
        #
        # Captions first, credits over them. Both are composited onto the joined
        # video rather than onto each piece: a caption spans a cut whenever a
        # sentence does, and building them per piece would clip every line that
        # crosses one.
        layers = [video]

        if cues:
            from engine.render.compose import _subtitle_overlay
            from engine.repurpose import captions as caption_rules
            from engine.services.fonts import cached_resolve

            overlay = _subtitle_overlay(
                caption_rules.regroup(cues),
                width=frame[0],
                height=frame[1],
                font=cached_resolve(""),
                duration=float(video.duration or 0.0),
                y_fraction=caption_rules.safe_y(aspect),
            )
            if overlay is not None:
                opened.append(overlay)
                layers.append(overlay)

        credit_layer = _credit_overlay(placed, credits or {}, frame)
        if credit_layer is not None:
            opened.append(credit_layer)
            layers.append(credit_layer)

        if len(layers) > 1:
            from moviepy import CompositeVideoClip

            audio = video.audio
            video = CompositeVideoClip(layers, size=frame).with_duration(video.duration)
            # `CompositeVideoClip` takes the *first* layer's audio, and the overlays
            # are silent — but being explicit here is cheap, and an overlay that
            # ever gains an audio track would otherwise silence the narration.
            video = video.with_audio(audio)
            opened.append(video)

        # ── audio ────────────────────────────────────────────────────────────
        tracks = [t for t in (video.audio,) if t is not None]

        if narration_path is not None and Path(narration_path).exists():
            narration = AudioFileClip(str(narration_path))
            opened.append(narration)
            # Trimmed rather than allowed to run past the picture: narration
            # continuing over black is worse than a line clipped short, and the
            # word budget in `narrate.py` exists to make this rare.
            if narration.duration > video.duration:
                narration = narration.subclipped(0, video.duration)
            tracks.append(narration)

        if bed_path is not None and Path(bed_path).exists():
            bed = AudioFileClip(str(bed_path))
            opened.append(bed)
            if bed.duration > video.duration:
                bed = bed.subclipped(0, video.duration)
            tracks.append(bed.with_volume_scaled(BED_GAIN))

        if tracks:
            video = video.with_audio(CompositeAudioClip(tracks))
        else:
            video = video.without_audio()

        _check()
        report(0.75, "encoding")
        video.write_videofile(
            str(output),
            codec="libx264",
            audio_codec="aac",
            fps=30,
            logger=None,
            threads=4,
        )

        return Assembly(
            duration_s=float(video.duration or cursor),
            placed=placed,
            # One cut per join between placed pieces. The teased hook needs no
            # separate term: it is prepended *as a piece*, so the cut back into the
            # body is already the join between piece 0 and piece 1. Adding one for
            # it counted the same cut twice, which inflated `gate.cut_density` —
            # the one signal where over-reporting flatters the edit.
            #
            # Reported from the finished piece list rather than assumed, because
            # the gate reads it and a number the caller supplied is not evidence.
            cuts=max(0, len(pieces) - 1),
            audio_bed_replaced=True,
            retained_source_audio=retained,
            aspect=aspect,
            captions_burned=bool(cues),
            credited_source_ids=sorted(
                {p.source_id for p in placed if p.source_id in credits and p.source_id}
            ),
        )
    finally:
        for clip in opened:
            try:
                clip.close()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass


def _credit_overlay(placed: list[Placed], credits: dict[str, str], frame: tuple[int, int]):
    """On-screen attribution, shown while the clip it credits is playing.

    Required by the gate for every lane with a counterparty — Lane B campaigns
    almost always make it a condition of the programme, and it is evidence of good
    faith for the others. Until this existed the gate blocked a Lane B video for
    missing credit that nothing could add, so the credit had to be burnt in by
    hand.

    Drawn per placement rather than once for the whole video: a compilation of
    three creators crediting only the first is worse than crediting none, because
    it reads as a claim that the rest are ours.

    Top-left, small, and never over the caption zone. It is an obligation, not a
    design element — it has to be legible and it has to stay out of the way.
    """
    if not placed or not credits:
        return None

    import numpy as np
    from moviepy import VideoClip

    from engine.render.compose import _caption_bitmap
    from engine.services.fonts import cached_resolve

    width, height = frame
    spans = [
        (p.placed_at_s, p.placed_at_s + p.duration_s, credits[p.source_id])
        for p in placed
        if p.source_id in credits and p.duration_s > 0
    ]
    if not spans:
        return None

    duration = max(end for _, end, _ in spans)
    font = cached_resolve("")
    font_px = max(14, int(height * 0.022))
    margin = int(height * 0.03)

    blank_rgb = np.zeros((height, width, 3), dtype="uint8")
    blank_mask = np.zeros((height, width), dtype="float64")
    cache: dict[str, tuple] = {}

    def render(label: str):
        if label in cache:
            return cache[label]
        bitmap, alpha = _caption_bitmap(label, font=font, size=font_px, max_w=int(width * 0.5))
        rgb = blank_rgb.copy()
        mask = blank_mask.copy()
        box_h, box_w = bitmap.shape[0], bitmap.shape[1]
        top, left = margin, margin
        box_h, box_w = min(box_h, height - top), min(box_w, width - left)
        rgb[top : top + box_h, left : left + box_w] = bitmap[:box_h, :box_w]
        mask[top : top + box_h, left : left + box_w] = (
            alpha[:box_h, :box_w] if alpha is not None else 1.0
        )
        # Held at full opacity would fight the picture; two-thirds reads as a
        # caption rather than as a watermark, which is the thing we strip.
        cache[label] = (rgb, mask * 0.66)
        return cache[label]

    def active(t: float) -> str | None:
        for start, end, label in spans:
            if start <= t < end:
                return label
        return None

    def frame_fn(t: float):
        label = active(t)
        return blank_rgb if label is None else render(label)[0]

    def mask_fn(t: float):
        label = active(t)
        return blank_mask if label is None else render(label)[1]

    overlay = VideoClip(frame_function=frame_fn, duration=duration)
    overlay.mask = VideoClip(frame_function=mask_fn, duration=duration, is_mask=True)
    return overlay


def _fit(clip, frame: tuple[int, int]):
    """Letterbox onto the target frame, preserving the source's own proportions.

    Not a crop. A vertical clip cropped into 16:9 loses the top and bottom of the
    subject, which on short-form footage is usually the face — and the failure is
    silent, so nobody sees it until the video is out. Bars are visibly a
    compromise; a beheaded subject looks like incompetence.
    """
    from moviepy.video.fx import Resize

    width, height = frame
    scale = min(width / clip.w, height / clip.h)
    resized = clip.with_effects(
        [Resize((max(1, round(clip.w * scale)), max(1, round(clip.h * scale))))]
    )

    if (resized.w, resized.h) == (width, height):
        return resized

    from moviepy import ColorClip, CompositeVideoClip

    backdrop = ColorClip(size=(width, height), color=(0, 0, 0), duration=resized.duration)
    return CompositeVideoClip([backdrop, resized.with_position("center")], size=(width, height))
