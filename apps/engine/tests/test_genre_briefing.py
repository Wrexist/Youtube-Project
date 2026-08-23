"""Tests for `engine.genre.briefing` — genre evidence as prompt text.

The brief is what actually reaches the model, so its shape is contractual:
quotable facts, strongest strategies first, and an empty watchlist producing
an empty string rather than a paragraph of hedging.
"""

from __future__ import annotations

from engine.genre import briefing

REPORT = {
    "video_count": 42,
    "hook_patterns": [
        {
            "pattern": "curiosity",
            "count": 14,
            "share": 0.333,
            "median_views": 120_000,
            "median_views_per_day": 12_400.0,
        },
        {
            "pattern": "number",
            "count": 9,
            "share": 0.214,
            "median_views": 80_000,
            "median_views_per_day": 8_100.0,
        },
    ],
    "median_duration_s": 500.0,
    "duration_buckets": {"under_60s": 10, "60s_to_8m": 22, "over_8m": 10},
    "uploads_per_week": 2.1,
    "top_by_velocity": [
        {
            "title": "Why Dams Fail",
            "channel_label": "C1",
            "views": 900_000,
            "views_per_day": 30_000.0,
        }
    ],
}


def fake_report(report):
    async def _fake():
        return report

    return _fake


async def test_an_empty_corpus_briefs_nothing():
    """No watchlist → "" — the prompt must be byte-identical to before."""
    assert await briefing.hook_guidance() == ""
    assert await briefing.title_guidance() == ""


async def test_the_brief_quotes_real_numbers(monkeypatch):
    monkeypatch.setattr(briefing, "_report", fake_report(REPORT))
    text = await briefing.hook_guidance()

    assert "42 recent videos" in text
    assert "33%" in text
    assert "12,400 views/day" in text
    assert "≈ 8 min" in text
    assert "2.1×/week" in text
    assert '"Why Dams Fail"' in text
    # Strategies are ordered strongest-first, matching the report's order.
    assert text.index("curiosity") < text.index("number")


async def test_partial_data_degrades_to_what_is_known(monkeypatch):
    sparse = {
        "video_count": 3,
        "hook_patterns": [],
        "median_duration_s": None,
        "duration_buckets": {},
        "uploads_per_week": None,
        "top_by_velocity": [],
    }
    monkeypatch.setattr(briefing, "_report", fake_report(sparse))
    text = await briefing.hook_guidance()
    assert "3 recent videos" in text
    assert "runtime" not in text
    assert "/week" not in text
