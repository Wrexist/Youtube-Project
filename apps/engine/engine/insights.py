"""Performance attribution.

Every generated artifact records the prompt and model that produced it — that rule
exists in CLAUDE.md for this module. Here it pays off: a video's click-through rate
is traced back to the title *strategy* that produced it, its 30-second retention back
to the hook *device*, and so on.

The output is a set of Findings, each with a verdict:

  confirmed    — enough evidence to change what the generator does
  suggestive   — a real-looking gap that hasn't cleared the bar yet
  insufficient — not enough videos to say anything

Only `confirmed` findings are allowed to feed back into generation. That gate is the
whole reason this module is careful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from engine.stats import Comparison, summarize, welch_t_test

# A dimension needs this many videos per group before it is compared at all.
MIN_PER_GROUP = 8

# Two-tailed significance threshold for promoting a finding to `confirmed`.
ALPHA = 0.05

# Below this, a statistically real difference is still not worth acting on.
MIN_MEANINGFUL_LIFT = 8.0  # percent


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    SUGGESTIVE = "suggestive"
    INSUFFICIENT = "insufficient"


Metric = Literal["ctr", "avd_seconds", "retention_30s", "views"]

METRIC_LABELS = {
    "ctr": "click-through rate",
    "avd_seconds": "average view duration",
    "retention_30s": "30-second retention",
    "views": "views",
}


@dataclass
class VideoRecord:
    """One published video, joined to the provenance of what created it."""

    video_id: str
    title: str
    published_at: str

    # Metrics from the Analytics API.
    ctr: float = 0.0
    avd_seconds: float = 0.0
    retention_30s: float = 0.0
    views: int = 0

    # Provenance, carried through from the workflow that produced it.
    title_strategy: str = ""
    hook_device: str = ""
    thumbnail_concept: str = ""
    script_model: str = ""
    format: str = "short"

    def dimension(self, name: str) -> str:
        return str(getattr(self, name, "") or "")

    def metric(self, name: Metric) -> float:
        return float(getattr(self, name, 0.0))


@dataclass
class Finding:
    dimension: str
    metric: Metric
    winner: str
    loser: str
    comparison: Comparison
    verdict: Verdict

    @property
    def lift(self) -> float:
        return self.comparison.lift

    def sentence(self) -> str:
        """The Analytics screen shows findings as sentences, not charts.

        The sample size and the hedging are part of the sentence, not a footnote —
        a claim from 9 videos should not read like a claim from 90.
        """
        label = METRIC_LABELS[self.metric]
        a, b = self.comparison.a, self.comparison.b
        fmt = _formatter(self.metric)

        hedge = {
            Verdict.CONFIRMED: "",
            Verdict.SUGGESTIVE: "appears to — not yet conclusive — ",
            Verdict.INSUFFICIENT: "too little data to say whether ",
        }[self.verdict]

        return (
            f"{hedge}{self.winner} {'beats' if not hedge else 'beats'} {self.loser} "
            f"on {label}: {fmt(a.mean)} vs {fmt(b.mean)} "
            f"across {a.n} and {b.n} videos"
        )

    def as_dict(self) -> dict:
        low, high = self.comparison.ci95
        return {
            "dimension": self.dimension,
            "metric": self.metric,
            "winner": self.winner,
            "loser": self.loser,
            "verdict": self.verdict.value,
            "lift": round(self.lift, 1),
            "p_value": round(self.comparison.p_value, 4),
            "n_winner": self.comparison.a.n,
            "n_loser": self.comparison.b.n,
            "ci95": [round(low, 3), round(high, 3)],
            "sentence": self.sentence(),
        }


@dataclass
class InsightReport:
    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.CONFIRMED]

    def summary(self) -> str:
        return f"{len(self.confirmed)} confirmed of {len(self.findings)} findings"


def analyze(
    videos: list[VideoRecord],
    *,
    dimensions: tuple[str, ...] = (
        "title_strategy",
        "hook_device",
        "thumbnail_concept",
        "script_model",
    ),
    metrics: tuple[Metric, ...] = ("ctr", "retention_30s", "avd_seconds"),
) -> InsightReport:
    """Compare the best and worst group within each dimension, per metric.

    Only best-vs-worst is tested, not every pair. Testing all pairs across four
    dimensions and three metrics would produce dozens of comparisons and, at p<0.05,
    a couple of false positives every run — which the loop would then train on.
    """
    report = InsightReport()

    for dimension in dimensions:
        groups: dict[str, list[VideoRecord]] = {}
        for video in videos:
            key = video.dimension(dimension)
            if key:
                groups.setdefault(key, []).append(video)

        eligible = {k: v for k, v in groups.items() if len(v) >= MIN_PER_GROUP}
        if len(eligible) < 2:
            report.skipped.append(
                f"{dimension}: needs 2 groups of {MIN_PER_GROUP}+ videos, "
                f"has {len(eligible)} ({len(groups)} groups total)"
            )
            continue

        for metric in metrics:
            means = {
                key: summarize([v.metric(metric) for v in vids]).mean
                for key, vids in eligible.items()
            }
            best = max(means, key=lambda k: means[k])
            worst = min(means, key=lambda k: means[k])
            if best == worst:
                continue

            comparison = welch_t_test(
                [v.metric(metric) for v in eligible[best]],
                [v.metric(metric) for v in eligible[worst]],
            )
            report.findings.append(
                Finding(
                    dimension=dimension,
                    metric=metric,
                    winner=best,
                    loser=worst,
                    comparison=comparison,
                    verdict=_verdict(comparison),
                )
            )

    report.findings.sort(key=lambda f: (f.verdict is not Verdict.CONFIRMED, -abs(f.lift)))
    return report


def _verdict(comparison: Comparison) -> Verdict:
    if comparison.a.n < MIN_PER_GROUP or comparison.b.n < MIN_PER_GROUP:
        return Verdict.INSUFFICIENT
    if comparison.p_value < ALPHA and abs(comparison.lift) >= MIN_MEANINGFUL_LIFT:
        return Verdict.CONFIRMED
    return Verdict.SUGGESTIVE


def _formatter(metric: Metric):
    if metric == "ctr":
        return lambda v: f"{v:.1f}%"
    if metric == "retention_30s":
        return lambda v: f"{v:.0f}%"
    if metric == "avd_seconds":
        return lambda v: f"{int(v) // 60}:{int(v) % 60:02d}"
    return lambda v: f"{v:,.0f}"


def map_retention_to_beats(curve: list[float], beats: list, duration_s: float) -> list[dict]:
    """Locate each script beat on the retention curve and find the steepest drop.

    This is what turns "retention falls at 20%" into "retention falls at the first
    data point" — the difference between a number and something actionable.
    """
    if not curve or not beats or duration_s <= 0:
        return []

    total_weight = sum(max(getattr(b, "est_seconds", 1.0), 0.5) for b in beats)
    cursor = 0.0
    out: list[dict] = []

    for beat in beats:
        weight = max(getattr(beat, "est_seconds", 1.0), 0.5)
        span = weight / total_weight
        start_pct, end_pct = cursor, cursor + span
        cursor = end_pct

        # Interpolate rather than index. The curve is sampled at fixed intervals and
        # beats are not aligned to them, so a short beat can span less than one
        # sample — indexing would report a drop of exactly zero for it.
        start_value = _sample(curve, start_pct)
        end_value = _sample(curve, end_pct)
        drop = start_value - end_value

        out.append(
            {
                "at_percent": round(start_pct * 100, 1),
                "label": getattr(beat, "purpose", "")[:40],
                "retention_start": round(start_value, 1),
                "retention_end": round(end_value, 1),
                "drop": round(drop, 1),
                # Normalised so a long beat isn't flagged merely for being long.
                "drop_rate": round(drop / max(span * 100, 0.1), 2),
            }
        )

    if out:
        worst = max(out, key=lambda b: b["drop_rate"])
        worst["worst"] = True

    return out


def _sample(curve: list[float], pct: float) -> float:
    """Linear interpolation into a retention curve at a fractional position."""
    if not curve:
        return 0.0
    pct = min(max(pct, 0.0), 1.0)
    position = pct * (len(curve) - 1)
    low = int(position)
    if low >= len(curve) - 1:
        return curve[-1]
    fraction = position - low
    return curve[low] + (curve[low + 1] - curve[low]) * fraction
