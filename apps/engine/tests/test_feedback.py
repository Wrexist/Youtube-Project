"""Feedback-loop tests.

This module rewrites the prompts that generate every future video, so the gate it
enforces — confirmed findings only — is the thing most worth testing. A regression
here would be invisible: the system would keep producing confident output, just
trained on noise.
"""

from __future__ import annotations

from engine.feedback import guidance_for, retention_guidance
from engine.insights import Verdict, VideoRecord, analyze


def records(dimension: str, groups: dict[str, list[float]]) -> list[VideoRecord]:
    out = []
    for name, ctrs in groups.items():
        for i, ctr in enumerate(ctrs):
            out.append(
                VideoRecord(
                    video_id=f"{name}{i}",
                    title=f"{name} {i}",
                    published_at="2026-07-01",
                    ctr=ctr,
                    **{dimension: name},
                )
            )
    return out


def test_a_new_channel_gets_no_guidance():
    """Nine videos teach the generator nothing. An empty string is correct here,
    not a failure."""
    report = analyze(records("title_strategy", {"a": [5.0, 6.0, 4.0]}))
    assert guidance_for(report, "titles") == ""


def test_confirmed_findings_reach_the_prompt():
    report = analyze(
        records(
            "title_strategy",
            {
                "curiosity_gap": [6.0, 6.4, 6.1, 6.3, 6.2, 6.5, 6.0, 6.6],
                "number_list": [4.0, 4.2, 3.9, 4.1, 4.0, 4.3, 3.8, 4.1],
            },
        )
    )
    guidance = guidance_for(report, "titles")
    assert "curiosity_gap outperforms number_list" in guidance
    assert "n=8 vs 8" in guidance  # the sample size travels with the claim


def test_suggestive_findings_are_withheld_from_the_generator():
    """The user sees these on the dashboard; the prompt does not."""
    report = analyze(
        records(
            "title_strategy",
            {
                "a": [5.0, 6.0, 4.0, 7.0, 3.0, 5.5, 4.5, 6.5, 5.2, 4.8],
                "b": [4.6, 5.6, 3.6, 6.6, 2.6, 5.1, 4.1, 6.1, 4.8, 4.4],
            },
        )
    )
    assert any(f.verdict is Verdict.SUGGESTIVE for f in report.findings)
    assert guidance_for(report, "titles") == ""


def test_guidance_permits_deviation():
    """Without this, a channel converges on one title shape and stops improving."""
    report = analyze(
        records(
            "title_strategy",
            {
                "curiosity_gap": [6.0, 6.4, 6.1, 6.3, 6.2, 6.5, 6.0, 6.6],
                "number_list": [4.0, 4.2, 3.9, 4.1, 4.0, 4.3, 3.8, 4.1],
            },
        )
    )
    guidance = guidance_for(report, "titles")
    assert "do not force them" in guidance
    assert "stops finding what works better" in guidance


def test_guidance_is_routed_to_the_right_stage():
    report = analyze(
        records(
            "hook_device",
            {
                "contradiction": [6.0, 6.4, 6.1, 6.3, 6.2, 6.5, 6.0, 6.6],
                "question": [4.0, 4.2, 3.9, 4.1, 4.0, 4.3, 3.8, 4.1],
            },
        )
    )
    assert "contradiction" in guidance_for(report, "hook")
    assert guidance_for(report, "titles") == ""  # not a title finding


def test_script_model_findings_are_never_fed_back_as_instruction():
    """Which model wrote the script is informational. Telling the prompt about it
    would just be confusing noise."""
    report = analyze(
        records(
            "script_model",
            {
                "model-a": [6.0, 6.4, 6.1, 6.3, 6.2, 6.5, 6.0, 6.6],
                "model-b": [4.0, 4.2, 3.9, 4.1, 4.0, 4.3, 3.8, 4.1],
            },
        )
    )
    assert report.confirmed  # the finding exists
    assert all(guidance_for(report, s) == "" for s in ("titles", "hook", "thumbnail"))


# ── retention ───────────────────────────────────────────────────────────────


def test_retention_guidance_names_the_offending_beat():
    beat_map = [
        {"label": "hook", "drop": 4.0, "drop_rate": 0.2},
        {"label": "first data point", "drop": 22.0, "drop_rate": 1.4, "worst": True},
    ]
    guidance = retention_guidance(beat_map)
    assert "first data point" in guidance
    assert "22 points" in guidance


def test_a_shallow_drop_produces_no_nagging():
    beat_map = [{"label": "hook", "drop": 2.0, "drop_rate": 0.1, "worst": True}]
    assert retention_guidance(beat_map) == ""


def test_no_retention_data_is_silent():
    assert retention_guidance([]) == ""
