"""`GET /v1/calendar/pending` — the server-side join the Calendar tray needed.

The tray rendered a demo fixture unconditionally, so a genuinely scheduled
video's chip looked its title up in a list of inventions. Driven directly
against the `JOBS` mirror, like `test_sse_stream.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine import main
from engine.api import insights as insights_api
from engine.api import publishing
from engine.insights import VideoRecord
from engine.workflows.base import Provenance, StageOutput, StageState, StageStatus
from engine.workflows.seo import TitleVariant


def _stage(name: str, value, artifacts: dict | None = None) -> StageState:
    state = StageState(name=name, status=StageStatus.DONE)
    state.output = StageOutput(value=value, provenance=Provenance(), artifacts=artifacts or {})
    return state


def _video_job(job_id: str, topic: str = "why bridges collapse") -> dict:
    return {
        "id": job_id,
        "workflow": main.video.get("video"),
        "status": "completed",
        "inputs": {"topic": topic, "format": "short"},
        "states": {
            "titles": _stage("titles", [TitleVariant(text="Why Bridges Fall", strategy="c")]),
            "subtitles": _stage("subtitles", [{"start": 0.0, "end": 42.0, "text": "hi"}]),
            "render": _stage("render", "renders/x.mp4", artifacts={"render": "renders/x.mp4"}),
        },
        "events": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def clean_mirrors(monkeypatch):
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(publishing, "SCHEDULE", {})
    yield


async def test_a_completed_render_is_pending_with_its_chosen_title():
    main.JOBS["j1"] = _video_job("j1")
    out = await main.pending_videos()
    assert [v.id for v in out] == ["j1"]
    assert out[0].title == "Why Bridges Fall"
    assert out[0].format == "short"
    assert out[0].duration == "0:42"
    assert out[0].scheduled_at is None


async def test_a_booked_video_carries_its_slot():
    main.JOBS["j1"] = _video_job("j1")
    at = datetime(2026, 9, 1, 17, 0, tzinfo=UTC)
    publishing.SCHEDULE["j1"] = at
    out = await main.pending_videos()
    assert out[0].scheduled_at == at


async def test_an_uploaded_video_is_no_longer_pending():
    main.JOBS["j1"] = _video_job("j1")
    main.JOBS["pub"] = {
        "id": "pub",
        "workflow": main.video.get("publish"),
        "status": "completed",
        "inputs": {"source_job_id": "j1"},
        "states": {"upload": _stage("upload", "yt123")},
        "events": [],
    }
    assert await main.pending_videos() == []


async def test_a_failed_publish_that_never_uploaded_stays_pending():
    main.JOBS["j1"] = _video_job("j1")
    main.JOBS["pub"] = {
        "id": "pub",
        "workflow": main.video.get("publish"),
        "status": "failed",
        "inputs": {"source_job_id": "j1"},
        "states": {},
        "events": [],
    }
    out = await main.pending_videos()
    assert [v.id for v in out] == ["j1"]


async def test_a_job_without_a_render_is_not_schedulable():
    job = _video_job("j1")
    del job["states"]["render"]
    main.JOBS["j1"] = job
    assert await main.pending_videos() == []


async def test_the_per_video_analytics_rows_come_from_the_records(monkeypatch):
    monkeypatch.setattr(insights_api, "RECORDS", {})
    insights_api.RECORDS["v1"] = VideoRecord(
        video_id="v1",
        title="Why Bridges Fall",
        published_at="2026-08-01T12:00:00Z",
        ctr=0.052,
        views=1200,
        avd_seconds=41.0,
        avd_percent=0.7,
        title_strategy="curiosity_gap",
        thumbnail_concept="stakes",
    )
    rows = await insights_api.analytics_videos()
    assert len(rows) == 1
    assert rows[0].title == "Why Bridges Fall"
    assert rows[0].ctr == 0.052
    assert rows[0].title_strategy == "curiosity_gap"
