"""The originality gate, re-checked where the money is spent.

`OriginalityStage` already raises and stops the run, so this looks redundant until
you ask how a blocked video could still reach the publish endpoint. Two routes do:

  * a stage output edited by hand through `POST /v1/jobs/{id}/edit`;
  * a grant that lapsed between the build and the publish — a campaign that ended
    last week does not retract the report it passed under.

Both end at the approval gate, which is the one that spends 1,600 quota units. That
is where enforcement has to be, not only where the file was produced.
"""

from __future__ import annotations

from engine import automation

SERIES = automation.Series(id="s1", name="Test", niche="engineering", monthly_budget_usd=50)


def _video(**kw) -> automation.VideoState:
    base = dict(
        id="v1",
        series_id="s1",
        has_sources=True,
        source_count=3,
        has_thumbnail=True,
        has_seo=True,
        keyword_grounded=True,
        render_ok=True,
        title="A title",
    )
    return automation.VideoState(**{**base, **kw})


def _codes(video) -> set[str]:
    return {b.code for b in automation.publish_blockers(video, SERIES)}


def _blocked_report(**overrides) -> dict:
    report = {
        "publishable": False,
        "headline": "Blocked on originality — 1 check failed.",
        "thresholds_version": 1,
        "rights": {"cleared": True, "ungranted": [], "problems": {}},
        "transformation": {
            "passed": False,
            "signals": [
                {
                    "name": "longest_bare_run",
                    "severity": "block",
                    "message": "longest unbroken lift is 42s",
                    "value": 42,
                    "threshold": 15,
                }
            ],
        },
    }
    report.update(overrides)
    return report


def test_a_video_that_failed_the_gate_cannot_publish():
    assert "not_original_enough" in _codes(_video(originality=_blocked_report()))


def test_the_blocker_says_what_failed():
    blockers = automation.publish_blockers(_video(originality=_blocked_report()), SERIES)
    message = next(b.message for b in blockers if b.code == "not_original_enough")

    assert "Blocked on originality" in message
    assert "42s" in message


def test_an_uncleared_rights_failure_is_named_first():
    """Rights and transformation need different fixes. The message must say which."""
    report = _blocked_report(
        rights={"cleared": False, "ungranted": ["clip1"], "problems": {}},
        headline="Blocked — rights are not cleared and the edit is not original enough.",
    )
    blockers = automation.publish_blockers(_video(originality=report), SERIES)
    message = next(b.message for b in blockers if b.code == "not_original_enough")

    assert "not cleared for use" in message


def test_a_video_that_passed_the_gate_is_not_blocked_by_it():
    passing = {
        "publishable": True,
        "headline": "Cleared to publish.",
        "thresholds_version": 1,
        "rights": {"cleared": True, "ungranted": [], "problems": {}},
        "transformation": {"passed": True, "signals": []},
    }
    assert "not_original_enough" not in _codes(_video(originality=passing))


def test_a_video_built_from_no_clips_is_not_judged_by_it():
    """The ordinary case, and not a failure. A wholly original video has nothing
    for that gate to judge — inventing a passing report for it would be the wrong
    default in the one direction that matters."""
    assert "not_original_enough" not in _codes(_video(originality=None))


def test_the_originality_blocker_does_not_mask_the_others():
    """Every check is independent — the caller sees the full list, not the first
    failure."""
    codes = _codes(_video(originality=_blocked_report(), has_thumbnail=False, has_seo=False))

    assert {"not_original_enough", "no_thumbnail", "no_seo"} <= codes
