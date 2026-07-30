"""`bake()`, actually executed.

`_render_sync` flattens beats to intermediate files once a render has more than
`MAX_OPEN_SOURCES` sources — the path that keeps a forty-clip long-form render from
holding forty ffmpeg subprocesses resident. Every other render test in this suite
stays under that threshold, so `bake()` and the window arithmetic around it were
run by nothing, and a seam between two consecutive windows is invisible until
somebody watches the video.

That is what this pins: nine sources (one over the limit, so the window size lands
at three) and a frame-by-frame decode of the result. A window that comes back a
frame short of the span it was written for leaves the black base clip showing
through, which is a hard black flash at a beat boundary in a video that is
otherwise fine — the kind of defect that ships.

Deliberately in the default run rather than behind a marker. It encodes four short
clips at 160×160 and takes a couple of seconds; the regression it catches cost more
than that to find by eye.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# A frame this dark is not footage. Real content here is solid colour with a mean
# luma above 100, and the failure being caught is a *fully* black frame at 0 — so
# 20 sits far from both and does not depend on the encoder's exact quantisation.
BLACK_LUMA = 20

#: Nine distinct, unambiguously bright colours — one per beat, so a seam cannot be
#: mistaken for a dark shot.
COLOURS = [
    (220, 40, 40),
    (40, 220, 40),
    (60, 60, 230),
    (230, 230, 40),
    (230, 40, 230),
    (40, 230, 230),
    (240, 150, 40),
    (150, 40, 240),
    (200, 200, 200),
]

#: Narration length, and the one number in this file that must not be tidied.
#:
#: A window is written with `write_videofile(fps=30)` and then *reopened*, so what
#: comes back is a whole number of frames — and it replaces a span that is not.
#: 9.13s over nine beats puts the first window boundary at 3.0433s while the file
#: reopens at 3.0333s, and the frame sampled at 3.0333s falls in the 10ms of
#: nothing between them. A round 9.0s hides the bug completely: every boundary
#: lands exactly on a frame, the arithmetic comes out even, and the render is
#: clean. Real narration is never a round number of frames long.
TOTAL_S = 9.13
SOURCE_S = 1.2  # longer than the beat span, as stock footage usually is
SIZE = (160, 160)
FPS = 30


class _Beat:
    """The only attribute `_beat_spans` reads."""

    def __init__(self, est_seconds: float) -> None:
        self.est_seconds = est_seconds


def _luma(frame) -> float:
    """Mean Rec.601 luma of one decoded RGB frame."""
    return float((0.299 * frame[..., 0] + 0.587 * frame[..., 1] + 0.114 * frame[..., 2]).mean())


@pytest.fixture
def render_inputs(tmp_path, monkeypatch):
    """Nine solid-colour sources, a silent narration, and a fast small canvas."""
    import numpy as np
    from moviepy import AudioArrayClip, ColorClip

    from engine.render import compose
    from engine.services import bgm

    # 160×160 instead of 1080×1080. `_render_sync` reads the size from this table,
    # and the seam is a timeline defect — it reproduces at any resolution, while
    # the full one turns a two-second test into a minute of encoding.
    monkeypatch.setitem(compose.RESOLUTIONS, "1:1", SIZE)
    # No music bed: `bgm.resolve()` scans `storage/` and the test has no business
    # reading it, and the mix is not what is under test.
    monkeypatch.setattr(bgm, "should_mix", lambda _volume: False)
    # Ken Burns pans and zooms each shot. Off here so that a dark frame can only
    # come from the timeline, never from an effect mid-transform.
    monkeypatch.setenv("STUDIO_KEN_BURNS", "none")
    from engine.settings import get_settings

    get_settings.cache_clear()

    clips = []
    for index, colour in enumerate(COLOURS):
        path = tmp_path / f"source-{index}.mp4"
        source = ColorClip(SIZE, color=colour, duration=SOURCE_S)
        source.write_videofile(str(path), fps=FPS, codec="libx264", audio=False, logger=None)
        source.close()
        clips.append({"id": f"c{index}", "path": str(path), "beat_index": index})

    audio_path = tmp_path / "narration.wav"
    silence = np.zeros((int(44100 * TOTAL_S), 2))
    track = AudioArrayClip(silence, fps=44100)
    track.write_audiofile(str(audio_path), logger=None)
    track.close()

    # Equal weights: `_beat_spans` rescales them onto the real audio length, so the
    # nine beats divide TOTAL_S evenly and every window boundary is a third of it.
    beats = [_Beat(1.0) for _ in COLOURS]
    return clips, beats, audio_path, tmp_path / "out.mp4"


def test_the_window_size_makes_this_render_bake(render_inputs):
    """The premise, asserted rather than assumed.

    Nine sources is one over `MAX_OPEN_SOURCES`, which is what puts `_window_size`
    above zero and makes `bake()` run at all. If the constant is ever raised past
    nine this test would silently stop testing anything, so it says so instead.
    """
    from engine.render.compose import MAX_OPEN_SOURCES, _window_size

    assert len(COLOURS) > MAX_OPEN_SOURCES
    assert _window_size(len(COLOURS)) == 3


def test_baked_windows_leave_no_black_frames(render_inputs):
    """Every frame of a baked render carries footage.

    Decoded with the same reader `compose` uses, one frame at a time, because a
    seam is one or two frames wide: a mean over the whole video, or a sample every
    half second, walks straight past it.
    """
    from moviepy import VideoFileClip

    from engine.render.compose import _render_sync

    clips, beats, audio_path, output = render_inputs
    _render_sync(clips, beats, audio_path, [], "1:1", output, lambda _f, _m: None)

    assert output.is_file()

    dark: list[tuple[int, float, float]] = []
    with VideoFileClip(str(output)) as rendered:
        for index, frame in enumerate(rendered.iter_frames()):
            luma = _luma(frame)
            if luma < BLACK_LUMA:
                dark.append((index, index / FPS, luma))

    assert not dark, (
        "black frames between baked windows at (frame, seconds, luma): "
        f"{dark[:10]}{' …' if len(dark) > 10 else ''}"
    )


def test_the_intermediate_files_are_cleaned_up(render_inputs):
    """The other half of `bake()`'s bargain.

    Each window is half a gigabyte on a long-form render and it lives in
    `storage/tmp` beside the output, so a leak here is a disk that fills up weeks
    later with nothing pointing back at the render that did it.
    """
    from engine.render.compose import _render_sync

    clips, beats, audio_path, output = render_inputs
    _render_sync(clips, beats, audio_path, [], "1:1", output, lambda _f, _m: None)

    leftovers = sorted(Path(output.parent).glob(f"{output.stem}-w*.mp4"))
    assert leftovers == [], f"baked windows were left behind: {leftovers}"
