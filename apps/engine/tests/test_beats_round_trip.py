"""Beats survive from the script that wrote them to the features that read them.

Two features read `VideoRecord.beats` — the retention map and the Shorts selector —
and the field did not exist. Both used `getattr(record, "beats", [])`, so both got an
empty list on every real video, silently, forever. Fifty-odd unit tests passed the
whole time because every one of them constructed beats by hand.

What was missing was a test of the *seam*: workflow output → stored record →
database → reader. That is all this file is.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from engine import repository, shorts
from engine.insights import (
    ScriptBeat,
    VideoRecord,
    as_beats,
    beats_to_payload,
    map_retention_to_beats,
)
from engine.workflows.script import Beat


def script_beats() -> list[Beat]:
    return [
        Beat(
            purpose=f"beat {i}",
            text_direction="say something",
            visual_direction="show something",
            energy="medium",
            est_seconds=30.0,
        )
        for i in range(10)
    ]


class TestNormalisation:
    def test_workflow_beats_are_accepted(self):
        got = as_beats(script_beats())
        assert len(got) == 10
        assert got[0].purpose == "beat 0"
        assert got[0].est_seconds == 30.0

    def test_stored_dicts_are_accepted(self):
        got = as_beats([{"purpose": "hook", "est_seconds": 12.0}])
        assert got == [ScriptBeat(purpose="hook", est_seconds=12.0)]

    def test_a_shape_that_is_not_a_beat_raises_rather_than_returning_nothing(self):
        """The whole bug in one line. `getattr(b, "est_seconds", 1.0)` on a dict
        returns 1.0 — a plausible number — so beats of wildly different lengths all
        became equal and nothing anywhere said so."""
        with pytest.raises(TypeError):
            as_beats(["not a beat"])

    def test_a_dict_no_longer_silently_becomes_a_default_length(self):
        stored = beats_to_payload(script_beats())
        assert [b.est_seconds for b in as_beats(stored)] == [30.0] * 10


class TestRoundTrip:
    def test_beats_survive_the_json_column(self):
        """`asdict()` is what `save_performance_record` stores and
        `VideoRecord(**payload)` is what reads it back. A dataclass in that field
        would serialise but not restore."""
        record = VideoRecord(
            video_id="v1",
            title="t",
            published_at="2026-01-01T00:00:00+00:00",
            beats=beats_to_payload(script_beats()),
        )
        restored = repository._record_from_payload(asdict(record))

        assert restored is not None
        assert restored.beats == record.beats
        assert as_beats(restored.beats)[3].purpose == "beat 3"

    def test_a_record_with_no_beats_still_round_trips(self):
        record = VideoRecord(video_id="v1", title="t", published_at="2026-01-01T00:00:00+00:00")
        restored = repository._record_from_payload(asdict(record))
        assert restored is not None
        assert restored.beats == []

    def test_an_older_row_without_the_field_still_loads(self):
        """Rows written before `beats` existed have no such key. They must load with
        an empty list, not be dropped as malformed."""
        payload = {
            "video_id": "v1",
            "title": "t",
            "published_at": "2026-01-01T00:00:00+00:00",
            "ctr": 0.0,
            "avd_seconds": 0.0,
            "avd_percent": 0.0,
            "views": 0,
            "title_strategy": "",
            "hook_device": "",
            "thumbnail_concept": "",
            "script_model": "",
            "format": "short",
        }
        restored = repository._record_from_payload(payload)
        assert restored is not None
        assert restored.beats == []


class TestFieldRenames:
    """A rename must not destroy the rows written before it.

    `retention_30s` became `avd_percent` because that is the only thing it ever
    held. The rename on its own dropped every historical record at startup:
    `VideoRecord(**payload)` raises on the unknown key, the caller caught it, and
    the attribution loop's entire sample went with one warning line.
    """

    def test_a_row_written_before_the_rename_still_loads(self):
        legacy = {
            "video_id": "v1",
            "title": "t",
            "published_at": "2026-01-01T00:00:00+00:00",
            "ctr": 4.2,
            "avd_seconds": 95.0,
            "retention_30s": 61.5,
            "views": 1200,
            "title_strategy": "question",
            "hook_device": "loop",
            "thumbnail_concept": "face",
            "script_model": "claude",
            "format": "short",
        }
        restored = repository._record_from_payload(legacy)

        assert restored is not None, "the row was dropped, taking its provenance with it"
        assert restored.avd_percent == 61.5, "the old value did not follow the rename"
        assert restored.title_strategy == "question"

    def test_a_field_removed_in_a_later_version_costs_the_field_not_the_row(self):
        """A row is provenance that cannot be regenerated. Losing one because it
        carries a key nobody reads any more is the wrong trade."""
        payload = {
            "video_id": "v1",
            "title": "t",
            "published_at": "2026-01-01T00:00:00+00:00",
            "some_field_we_deleted": 1,
        }
        restored = repository._record_from_payload(payload)
        assert restored is not None
        assert restored.video_id == "v1"

    def test_the_canonical_name_wins_when_a_row_carries_both(self):
        """Order must not decide. A single `setdefault` pass took whichever key
        came first, so the migrated legacy value could beat the real one."""
        for payload in (
            {
                "video_id": "v",
                "title": "t",
                "published_at": "x",
                "retention_30s": 11.0,
                "avd_percent": 99.0,
            },
            {
                "video_id": "v",
                "title": "t",
                "published_at": "x",
                "avd_percent": 99.0,
                "retention_30s": 11.0,
            },
        ):
            restored = repository._record_from_payload(payload)
            assert restored is not None
            assert restored.avd_percent == 99.0

    def test_a_row_missing_a_required_field_is_still_refused(self):
        assert repository._record_from_payload({"title": "t"}) is None


class TestReadersSeeThem:
    """Both readers, driven from a stored record rather than hand-built beats."""

    def stored(self) -> VideoRecord:
        return VideoRecord(
            video_id="v1",
            title="t",
            published_at="2026-01-01T00:00:00+00:00",
            beats=beats_to_payload(script_beats()),
        )

    def test_the_retention_map_locates_stored_beats(self):
        curve = [100 - i for i in range(50)]
        mapped = map_retention_to_beats(curve, self.stored().beats, duration_s=300.0)

        assert len(mapped) == 10
        assert mapped[0]["label"] == "beat 0"
        # Equal-length beats spread evenly. Under the old getattr-on-a-dict path
        # they also came out equal — but at the *default* 1.0s, which is the same
        # answer for the wrong reason. The positions are what tell them apart.
        assert [b["at_percent"] for b in mapped[:3]] == [0.0, 10.0, 20.0]

    def test_the_shorts_selector_reads_stored_beats(self):
        n = 100
        curve = [100 + (20 - 100) * i / (n - 1) for i in range(n)]
        centre, half = 0.6 * (n - 1), 6.0
        curve = [
            v + (20.0 * (1 - abs(i - centre) / half) if abs(i - centre) < half else 0.0)
            for i, v in enumerate(curve)
        ]

        picks = shorts.find_candidates(curve, self.stored().beats, duration_s=300.0)
        assert picks, "a stored record must be a usable input to the selector"
        assert picks[0].label.startswith("beat ")
