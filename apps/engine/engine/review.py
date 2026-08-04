"""The weekly review — what changed in what the system believes.

`analyze()` already answers "what is true right now", and the Analytics screen can
ask for that any time. The thing nobody can get on demand is **what changed**: a
finding that was suggestive last week and is confirmed today is the moment the
generator's behaviour actually changes, and it happens silently in the middle of a
week with nobody watching.

So this module is a diff, not a re-run. The re-run is the cheap part.

Two decisions worth stating, because both have a tempting wrong answer:

- **A finding is identified by what it claims, not by its numbers.** The key is
  (dimension, metric, winner, loser). Include the lift or the p-value and every
  finding is "new" every single week, because the numbers move with every video
  published — the diff becomes noise that always says everything changed.

- **A reversal is reported separately from an appearance.** "Hook A beats hook B"
  turning into "hook B beats hook A" is not one finding leaving and another
  arriving; it is the system contradicting itself, which is the single most
  important thing a weekly review can surface and the easiest to lose in a pair of
  add/remove lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from loguru import logger

from engine.insights import Finding, InsightReport, Verdict

#: A finding's identity: what it claims, with the numbers deliberately left out.
Key = tuple[str, str, str, str]


def key_of(finding: Finding) -> Key:
    return (finding.dimension, finding.metric, finding.winner, finding.loser)


def _reversal_of(key: Key) -> Key:
    """The same comparison with the winner and loser swapped."""
    dimension, metric, winner, loser = key
    return (dimension, metric, loser, winner)


@dataclass(frozen=True)
class Change:
    kind: str
    """One of: appeared, promoted, demoted, reversed, disappeared."""

    finding: Finding | None
    """The current finding. None for `disappeared`, which by definition has none."""

    was: str | None = None
    """The previous verdict, where there was one."""

    def sentence(self) -> str:
        if self.kind == "disappeared":
            return "No longer supported by the data."
        assert self.finding is not None
        base = self.finding.sentence()
        if self.kind == "promoted":
            return f"{base} Confirmed this week — it now feeds back into generation."
        if self.kind == "demoted":
            return f"{base} Weaker than last week; no longer fed back."
        if self.kind == "reversed":
            return f"{base} This reverses last week's finding — treat both as unsafe."
        return base


@dataclass
class Review:
    """One week's reading, and how it differs from the week before."""

    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    video_count: int = 0
    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    is_first: bool = False
    """No previous review to compare against, so `changes` is empty by definition
    rather than because nothing moved. The two look identical in the payload and
    read as opposite things on a screen."""

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict == Verdict.CONFIRMED]

    @property
    def worth_reading(self) -> bool:
        """Whether this week is worth telling anyone about.

        A weekly job that always produces a notification trains people to ignore
        it, and most weeks genuinely have nothing in them — the sample sizes here
        move slowly by construction (`MIN_PER_GROUP` is 8).
        """
        return bool(self.changes)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "video_count": self.video_count,
            "is_first": self.is_first,
            "worth_reading": self.worth_reading,
            "confirmed_count": len(self.confirmed),
            "findings": [f.as_dict() for f in self.findings],
            "skipped": self.skipped,
            "changes": [
                {
                    "kind": c.kind,
                    "was": c.was,
                    "sentence": c.sentence(),
                    "finding": c.finding.as_dict() if c.finding else None,
                }
                for c in self.changes
            ],
        }


def snapshot(report: InsightReport) -> dict:
    """The minimum a future diff needs: each finding's identity and its verdict.

    Deliberately not the whole report. A snapshot is stored every week forever, and
    what the next diff reads is four strings and a verdict per finding.
    """
    return {
        "findings": [
            {
                "dimension": f.dimension,
                "metric": f.metric,
                "winner": f.winner,
                "loser": f.loser,
                "verdict": str(f.verdict),
            }
            for f in report.findings
        ]
    }


def diff(previous: dict | None, report: InsightReport) -> tuple[list[Change], bool]:
    """Compare this week's findings against a stored snapshot.

    Returns the changes and whether this is the first review (no previous snapshot).
    """
    if previous is None:
        return [], True

    was: dict[Key, str] = {
        (row["dimension"], row["metric"], row["winner"], row["loser"]): row["verdict"]
        for row in previous.get("findings", [])
    }

    changes: list[Change] = []
    seen: set[Key] = set()

    for finding in report.findings:
        key = key_of(finding)
        seen.add(key)
        verdict = str(finding.verdict)

        if key in was:
            if was[key] == verdict:
                continue
            kind = (
                "promoted"
                if verdict == str(Verdict.CONFIRMED)
                else "demoted"
                if was[key] == str(Verdict.CONFIRMED)
                else "appeared"
            )
            changes.append(Change(kind=kind, finding=finding, was=was[key]))
            continue

        # Not seen before under this identity — but the same comparison pointing the
        # other way is a contradiction, not a new discovery.
        flipped = _reversal_of(key)
        if flipped in was:
            seen.add(flipped)
            changes.append(Change(kind="reversed", finding=finding, was=was[flipped]))
        else:
            changes.append(Change(kind="appeared", finding=finding))

    for key, verdict in was.items():
        if key in seen:
            continue
        # Only a finding that had actually earned belief is worth reporting gone.
        # An `insufficient` finding dropping out is the sample size moving by one
        # video, which is not news.
        if verdict == str(Verdict.CONFIRMED):
            changes.append(Change(kind="disappeared", finding=None, was=verdict))

    return changes, False


def build(report: InsightReport, previous: dict | None, video_count: int) -> Review:
    """Assemble a `Review` from a fresh report and the previous week's snapshot."""
    changes, is_first = diff(previous, report)
    return Review(
        video_count=video_count,
        findings=report.findings,
        skipped=report.skipped,
        changes=changes,
        is_first=is_first,
    )


async def run() -> Review:
    """Pull fresh metrics, re-analyse, diff against last week, and store the result.

    The refresh is the half that has no other trigger. `POST /v1/insights/refresh`
    exists but nothing calls it on a schedule, so before this job the metrics
    behind every finding were as old as the last time somebody happened to open
    the Analytics screen and press a button.

    Failing to reach the Analytics API is not fatal. The stored records are still
    worth re-analysing — the sample grows as videos are published whether or not
    today's numbers arrived — so the review is produced either way and says which
    it was.
    """
    from engine.api import insights as insights_api
    from engine.insights import analyze
    from engine.providers.analytics import Analytics
    from engine.repository import latest_review_snapshot, save_review_snapshot

    creds = insights_api.CHANNELS.get("default")
    if creds is not None:
        try:
            rows = await Analytics(creds).per_video(days=90)
        except Exception as exc:  # noqa: BLE001 — a dead API must not kill the review
            logger.warning("weekly review could not refresh metrics: {}", exc)
        else:
            for row in rows:
                record = insights_api.RECORDS.get(row["video_id"])
                if record is None:
                    continue  # published outside Studio; no provenance to attribute
                record.ctr = row["ctr"]
                record.avd_seconds = row["avd_seconds"]
                record.views = row["views"]
                record.retention_30s = row["avd_percent"]

    records = list(insights_api.RECORDS.values())
    report = analyze(records)

    previous = await latest_review_snapshot()
    review = build(report, previous, video_count=len(records))
    await save_review_snapshot(snapshot(report), video_count=len(records))

    logger.info(
        "weekly review: {} findings, {} confirmed, {} change(s)",
        len(review.findings),
        len(review.confirmed),
        len(review.changes),
    )
    return review
