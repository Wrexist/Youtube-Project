"""Tests for the publish workflow and its approval gate.

The bug these exist for: `workflows/publish.py` defined four complete stages and
`PUBLISH_STAGES`, and **nothing imported the module**. `WORKFLOWS` held only
video/script/seo, so `POST /v1/jobs {"workflow":"publish"}` 404'd and no code path
in the repository could upload a video to YouTube.

It could not simply be registered, either: `UploadStage.depends_on` names video
stages, and `Workflow._validate` requires dependencies to be defined earlier in the
same workflow — so `Workflow("publish", PUBLISH_STAGES)` raises at import. Publishing
has to *extend* the video workflow. These tests pin that arrangement down.
"""

from __future__ import annotations

import pytest

from engine.workflows import publish, video
from engine.workflows.base import StageStatus, Workflow

# ── registration ────────────────────────────────────────────────────────────


def test_publish_is_a_registered_workflow():
    assert "publish" in video.WORKFLOWS
    assert video.get("publish").name == "publish"


def test_publish_extends_the_video_workflow():
    """The publish stages run after every video stage, not instead of them."""
    names = [s.name for s in video.get("publish").stages]
    video_names = [s.name for s in video.get("video").stages]

    assert names[: len(video_names)] == video_names
    assert names[len(video_names) :] == ["upload", "thumbnail_set", "captions", "playlist"]


def test_publish_stages_alone_cannot_form_a_workflow():
    """Guards the reason this is composed rather than standalone.

    If someone 'simplifies' this to Workflow("publish", publish_stages()), it will
    raise — and this test says why before they spend an afternoon on it.
    """
    with pytest.raises(ValueError, match="depends on 'render'"):
        Workflow("publish-alone", publish.publish_stages())


def test_upload_depends_on_everything_it_needs():
    upload = next(s for s in video.get("publish").stages if s.name == "upload")
    assert set(upload.depends_on) == {"render", "titles", "description", "tags"}


def test_publish_stages_returns_fresh_instances():
    """Two workflows must not share Stage objects — they carry per-run state."""
    a = publish.publish_stages()
    b = publish.publish_stages()
    assert [s.name for s in a] == [s.name for s in b]
    assert all(x is not y for x, y in zip(a, b, strict=True))


def test_only_the_publish_stages_are_not_optional_beyond_upload():
    """Upload must be mandatory; the follow-ups must not fail a live video.

    Once the video is up, a failed caption upload is a retryable annoyance — it must
    not mark the whole publish failed and it must not re-spend 1,600 upload units.
    """
    stages = {s.name: s for s in publish.publish_stages()}
    assert stages["upload"].optional is False
    assert stages["upload"].max_attempts == 1
    for name in ("thumbnail_set", "captions", "playlist"):
        assert stages[name].optional is True, name


# ── seeding: video stages replay, publish stages run ────────────────────────


def test_seeding_marks_video_stages_done_so_they_replay():
    """A publish job seeded from a finished video job re-runs nothing expensive."""
    wf = video.get("publish")
    source = video.get("video")

    finished = source.initial_states()
    for state in finished.values():
        state.status = StageStatus.DONE

    states = wf.initial_states()
    for name, state in finished.items():
        if name in states:
            states[name] = state

    replayed = [n for n, s in states.items() if s.status is StageStatus.DONE]
    to_run = [n for n, s in states.items() if s.status is StageStatus.PENDING]

    assert set(to_run) == {"upload", "thumbnail_set", "captions", "playlist"}
    assert len(replayed) == len(source.stages)


def test_playlist_is_skipped_without_a_playlist_id():
    """Most videos are not in a playlist; that must not count as a failure."""
    stage = next(s for s in publish.publish_stages() if s.name == "playlist")

    class Ctx:
        inputs: dict = {}

    assert stage.should_skip(Ctx()) is True
    Ctx.inputs = {"playlist_id": "PL123"}
    assert stage.should_skip(Ctx()) is False


async def test_the_playlist_stage_actually_adds_the_video():
    """The half `should_skip` cannot tell you.

    Both branches of the skip were covered and `run` never was — so the stage was
    proven to *decline* correctly and never proven to work, which is the same
    coverage a stage that raises on line one would have. It could afford to be: no
    publish in this project's history ever set `playlist_id`, because nothing in
    the web app could, so `run` had genuinely never executed.
    """
    stage = next(s for s in publish.publish_stages() if s.name == "playlist")
    added: list[tuple[str, str]] = []

    class Client:
        async def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
            added.append((video_id, playlist_id))

    class Ctx:
        inputs = {"youtube_client": Client(), "playlist_id": "PL123"}

        def get(self, name: str) -> str:
            assert name == "upload"  # the id it adds is the one just uploaded
            return "yt-abc"

    output = await stage.run(Ctx())

    assert added == [("yt-abc", "PL123")]
    # The value is what the Library and the re-run path read back.
    assert output.value == "PL123"


# ── SRT emission ────────────────────────────────────────────────────────────


def test_srt_is_well_formed():
    srt = publish._to_srt(
        [
            {"start": 0.0, "end": 2.5, "text": "The bridge collapsed."},
            {"start": 2.5, "end": 5.25, "text": "Here is why."},
        ]
    )
    assert srt.splitlines()[:3] == [
        "1",
        "00:00:00,000 --> 00:00:02,500",
        "The bridge collapsed.",
    ]
    assert "2\n00:00:02,500 --> 00:00:05,250" in srt


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "00:00:00,000"),
        (61.5, "00:01:01,500"),
        (3661.001, "01:01:01,001"),
        (7200.0, "02:00:00,000"),
    ],
)
def test_srt_timestamps(seconds, expected):
    assert publish._ts(seconds) == expected
