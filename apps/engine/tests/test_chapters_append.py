"""`append_chapters` — the plumbing that makes ChaptersStage worth its $0.01.

YouTube renders chapters only from timestamps in the description; until this
function existed the stage's output was generated, billed and read by nothing
(KNOWN-ISSUES §5.8). The behaviour under the 5000-byte ceiling is all-or-
nothing on purpose: a silently truncated chapter list misdescribes the video,
and the description is prose someone may have edited — never cut.
"""

from __future__ import annotations

from engine.workflows.seo import DESCRIPTION_MAX, append_chapters

CHAPTERS = [("0:00", "The promise"), ("1:10", "The design flaw"), ("4:35", "The collapse")]


def test_chapters_are_appended_as_youtube_reads_them():
    out = append_chapters("A video about bridges.", CHAPTERS)
    expected = (
        "A video about bridges.\n\nChapters:\n"
        "0:00 The promise\n1:10 The design flaw\n4:35 The collapse"
    )
    assert out.endswith(expected)


def test_lists_from_a_persisted_job_work_like_tuples():
    # A restored job's chapter pairs come back as JSON lists, not tuples.
    out = append_chapters("desc", [list(c) for c in CHAPTERS])
    assert "0:00 The promise" in out


def test_fewer_than_three_chapters_is_not_a_chapter_list():
    assert append_chapters("desc", CHAPTERS[:2]) == "desc"


def test_a_list_not_starting_at_zero_is_dropped_whole():
    shifted = [("0:30", "Late start"), *CHAPTERS[1:]]
    assert append_chapters("desc", shifted) == "desc"


def test_a_block_that_would_breach_the_ceiling_is_dropped_not_truncated():
    description = "x" * (DESCRIPTION_MAX - 20)  # room for nothing
    assert append_chapters(description, CHAPTERS) == description


def test_the_description_itself_is_never_cut():
    description = "y" * (DESCRIPTION_MAX - 60)
    out = append_chapters(description, CHAPTERS)
    # Either the whole block fit or nothing was added; the prose is intact.
    assert out.startswith(description)


def test_malformed_entries_are_ignored_rather_than_crashing():
    assert append_chapters("desc", ["0:00", None, {"time": "0:00"}]) == "desc"
