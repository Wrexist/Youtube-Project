"""Compositing, against real files.

These render actual MP4s rather than mocking MoviePy. That is slower and it is the
point: the module's whole job is producing a file, and a test that asserts against
a mocked encoder proves the calls were made, not that the video exists or that its
audio is what we think it is.

The audio assertions are the ones that matter most. TikTok's music licences cover
TikTok, so source audio reaching YouTube is unlicensed regardless of the video
rights — it is the likeliest single cause of a claim in the whole product, and
"the bed was replaced" has to be a measured fact rather than a flag somebody set.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from engine.repurpose.assemble import FRAMES, Assembly, _fit, assemble


@pytest.fixture
def clip_factory(tmp_path):
    """Real short MP4s, with or without an audio track."""

    def make(name: str, *, seconds: float = 3.0, size=(320, 568), with_audio=True):
        from moviepy import AudioArrayClip, ColorClip

        path = tmp_path / f"{name}.mp4"
        video = ColorClip(size=size, color=(20, 90, 160), duration=seconds).with_fps(12)
        if with_audio:
            rate = 22_050
            samples = np.sin(2 * np.pi * 440 * np.arange(int(rate * seconds)) / rate).reshape(-1, 1)
            video = video.with_audio(AudioArrayClip(np.hstack([samples, samples]), fps=rate))
        video.write_videofile(str(path), codec="libx264", audio_codec="aac", logger=None)
        video.close()
        return path

    return make


@pytest.fixture
def narration(tmp_path):
    from moviepy import AudioArrayClip

    rate = 22_050
    samples = np.sin(2 * np.pi * 220 * np.arange(int(rate * 4)) / rate).reshape(-1, 1)
    path = tmp_path / "narration.mp3"
    AudioArrayClip(np.hstack([samples, samples]), fps=rate).write_audiofile(str(path), logger=None)
    return path


def _probe(path):
    from moviepy import VideoFileClip

    with VideoFileClip(str(path)) as clip:
        return {
            "duration": float(clip.duration or 0),
            "size": clip.size,
            "has_audio": clip.audio is not None,
        }


# ── the audio rule ──────────────────────────────────────────────────────────


async def test_source_audio_is_stripped_by_default(clip_factory, narration, tmp_path):
    """The non-negotiable. A TikTok bed on YouTube is unlicensed however solid
    the video rights are."""
    source = clip_factory("a", with_audio=True)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 2.0}],
        sources={"a": source},
        narration_path=narration,
        job_id="j1",
        aspect="9:16",
    )

    assert result.audio_bed_replaced is True
    assert result.retained_source_audio == []


async def test_source_audio_can_be_retained_per_clip(clip_factory, narration):
    """Opt-in, and the right call for a clip whose point is what somebody said."""
    source = clip_factory("a", with_audio=True)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 2.0}],
        sources={"a": source},
        narration_path=narration,
        job_id="j2",
        keep_source_audio={"a"},
    )

    assert result.retained_source_audio == ["a"]
    # Still reported as replaced: the *bed* is ours, and the retained track is
    # ducked underneath rather than left to compete with the narration.
    assert result.audio_bed_replaced is True


async def test_the_finished_file_carries_the_narration(clip_factory, narration):
    from engine.storage import store

    source = clip_factory("a", with_audio=False)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 2.0}],
        sources={"a": source},
        narration_path=narration,
        job_id="j3",
    )

    probed = _probe(await store.local_path(result.output_key))
    assert probed["has_audio"], "a video with no narration track is a silent reupload"


async def test_narration_longer_than_the_picture_is_trimmed(clip_factory, narration):
    """Narration continuing over black is worse than a line clipped short."""
    from engine.storage import store

    source = clip_factory("a", seconds=1.5, with_audio=False)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 1.5}],
        sources={"a": source},
        narration_path=narration,  # 4s of narration over 1.5s of picture
        job_id="j4",
    )

    probed = _probe(await store.local_path(result.output_key))
    assert probed["duration"] < 3.0


# ── the cut list ────────────────────────────────────────────────────────────


async def test_segments_are_joined_in_order_with_real_placements(clip_factory, narration):
    source_a = clip_factory("a", with_audio=False)
    source_b = clip_factory("b", with_audio=False)

    result = await assemble(
        segments=[
            {"source_id": "a", "start_s": 0.0, "end_s": 2.0},
            {"source_id": "b", "start_s": 0.5, "end_s": 2.5},
        ],
        sources={"a": source_a, "b": source_b},
        narration_path=narration,
        job_id="j5",
    )

    assert [p.source_id for p in result.placed] == ["a", "b"]
    # Placement is where it sits in the *output*, not in the source.
    assert result.placed[0].placed_at_s == 0.0
    assert result.placed[1].placed_at_s == pytest.approx(2.0)
    assert result.cuts == 1


async def test_a_teased_hook_is_prepended(clip_factory, narration):
    """The whole reason the hook is a separate decision — opening on the
    strongest moment is what buys the 1.5–3 seconds a viewer spends deciding."""
    source = clip_factory("a", seconds=4.0, with_audio=False)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 2.0}],
        sources={"a": source},
        narration_path=narration,
        job_id="j6",
        hook={"source_id": "a", "at_s": 2.5, "duration_s": 1.0, "teased": True},
    )

    assert result.placed[0].is_hook
    assert result.placed[0].start_s == pytest.approx(2.5)
    # One cut: the join back into the body. The hook is prepended *as a piece*, so
    # it needs no separate term — counting one for it double-counted the same cut
    # and inflated cut_density, the one signal where over-reporting flatters.
    assert result.cuts == 1


async def test_an_unteased_hook_is_not_prepended(clip_factory, narration):
    source = clip_factory("a", seconds=4.0, with_audio=False)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 2.0}],
        sources={"a": source},
        narration_path=narration,
        job_id="j7",
        hook={"source_id": "a", "at_s": 0.0, "duration_s": 1.0, "teased": False},
    )

    assert not any(p.is_hook for p in result.placed)


async def test_a_missing_source_is_skipped_not_fatal(clip_factory, narration):
    """One unreadable clip must not lose the whole episode."""
    source = clip_factory("a", with_audio=False)

    result = await assemble(
        segments=[
            {"source_id": "a", "start_s": 0.0, "end_s": 2.0},
            {"source_id": "gone", "start_s": 0.0, "end_s": 2.0},
        ],
        sources={"a": source},
        narration_path=narration,
        job_id="j8",
    )

    assert [p.source_id for p in result.placed] == ["a"]


async def test_a_cut_shorter_than_a_frame_flicker_is_skipped(clip_factory, narration):
    source = clip_factory("a", with_audio=False)

    result = await assemble(
        segments=[
            {"source_id": "a", "start_s": 0.0, "end_s": 0.1},
            {"source_id": "a", "start_s": 0.0, "end_s": 2.0},
        ],
        sources={"a": source},
        narration_path=narration,
        job_id="j9",
    )

    assert len(result.placed) == 1


async def test_an_empty_cut_list_is_refused():
    with pytest.raises(ValueError, match="cut list is empty"):
        await assemble(segments=[], sources={}, narration_path=None, job_id="j10")


async def test_every_cut_being_unusable_is_refused(narration):
    with pytest.raises(ValueError, match="nothing to assemble"):
        await assemble(
            segments=[{"source_id": "gone", "start_s": 0, "end_s": 2}],
            sources={},
            narration_path=narration,
            job_id="j11",
        )


# ── reframing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("aspect", list(FRAMES))
async def test_the_output_matches_the_requested_aspect(clip_factory, narration, aspect):
    from engine.storage import store

    source = clip_factory("a", size=(320, 568), with_audio=False)

    result = await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 1.5}],
        sources={"a": source},
        narration_path=narration,
        job_id=f"j-{aspect.replace(':', '-')}",
        aspect=aspect,
    )

    probed = _probe(await store.local_path(result.output_key))
    assert tuple(probed["size"]) == FRAMES[aspect]


def test_reframing_letterboxes_rather_than_cropping():
    """A vertical clip cropped into 16:9 loses the subject's head about as often
    as not, and the failure is silent."""
    from moviepy import ColorClip

    vertical = ColorClip(size=(1080, 1920), color=(255, 0, 0), duration=1)
    fitted = _fit(vertical, (1920, 1080))

    assert tuple(fitted.size) == (1920, 1080)
    # The source's own proportions survive: 1080x1920 scaled to fit 1080 height
    # is 607 wide, so most of the frame is bar rather than stretched picture.
    frame = fitted.get_frame(0)
    assert frame[540, 10].tolist() == [0, 0, 0], "edges should be letterbox, not stretched source"


def test_an_exact_match_is_passed_through_untouched():
    from moviepy import ColorClip

    clip = ColorClip(size=(1080, 1920), color=(1, 2, 3), duration=1)
    assert tuple(_fit(clip, (1080, 1920)).size) == (1080, 1920)


# ── reporting ───────────────────────────────────────────────────────────────


def test_assembly_reports_facts_rather_than_intentions():
    """`build_timeline` reads these instead of the job's inputs. A gate whose
    evidence is supplied by the thing it judges is not a gate."""
    payload = Assembly(output_key="k", duration_s=42.0, cuts=7, audio_bed_replaced=True).as_dict()

    assert payload["cuts"] == 7
    assert payload["audio_bed_replaced"] is True
    assert payload["duration_s"] == 42.0


async def test_progress_is_reported(clip_factory, narration):
    source = clip_factory("a", with_audio=False)
    seen: list[tuple[float, str]] = []

    async def on_progress(fraction: float, message: str) -> None:
        seen.append((fraction, message))

    await assemble(
        segments=[{"source_id": "a", "start_s": 0.0, "end_s": 1.5}],
        sources={"a": source},
        narration_path=narration,
        job_id="j12",
        on_progress=on_progress,
    )

    assert seen and seen[-1][0] == 1.0


async def test_an_aborted_assembly_stops(clip_factory, narration):
    source = clip_factory("a", with_audio=False)
    abort = threading.Event()
    abort.set()

    with pytest.raises(RuntimeError, match="aborted"):
        await assemble(
            segments=[{"source_id": "a", "start_s": 0.0, "end_s": 1.5}],
            sources={"a": source},
            narration_path=narration,
            job_id="j13",
            abort=abort,
        )
