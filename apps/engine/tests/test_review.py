"""Tests for the weekly review.

The re-analysis is `analyze()`, already tested in `test_insights.py`. What is new
here is the *diff*, and every test below is about a way a diff can be wrong while
looking right: reporting churn as change, reporting a contradiction as two
unrelated events, or reporting silence and absence identically.
"""

from __future__ import annotations

import pytest

from engine.insights import Finding, InsightReport, Verdict
from engine.review import Review, build, diff, snapshot
from engine.stats import Comparison, Summary


def summary(n: int = 10, mean: float = 5.0) -> Summary:
    return Summary(mean=mean, std=1.0, n=n)


def finding(
    *,
    dimension: str = "hook_device",
    metric: str = "ctr",
    winner: str = "question",
    loser: str = "statement",
    verdict: Verdict = Verdict.CONFIRMED,
    lift: float = 20.0,
    p_value: float = 0.01,
) -> Finding:
    return Finding(
        dimension=dimension,
        metric=metric,
        winner=winner,
        loser=loser,
        comparison=Comparison(
            a=summary(mean=6.0),
            b=summary(mean=5.0),
            t=3.0,
            p_value=p_value,
            lift=lift,
            df=17.0,
        ),
        verdict=verdict,
    )


def report(*findings: Finding, skipped: list[str] | None = None) -> InsightReport:
    return InsightReport(findings=list(findings), skipped=skipped or [])


class TestFirstRun:
    def test_no_previous_snapshot_is_reported_as_first_not_as_no_change(self):
        """These are opposite things on a screen. A first review with three brand
        new findings must not read as a quiet week."""
        changes, is_first = diff(None, report(finding()))
        assert is_first is True
        assert changes == []

    def test_a_previous_run_that_found_nothing_is_not_a_first_run(self):
        changes, is_first = diff({"findings": []}, report(finding()))
        assert is_first is False
        assert [c.kind for c in changes] == ["appeared"]


class TestStability:
    def test_an_unchanged_finding_is_not_reported(self):
        before = snapshot(report(finding()))
        changes, _ = diff(before, report(finding()))
        assert changes == []

    def test_moving_numbers_are_not_a_change(self):
        """A finding's identity is what it claims, not its lift or p-value. Both
        move every time a video is published, and keying on them makes every
        finding new every week — a diff that always says everything changed."""
        before = snapshot(report(finding(lift=20.0, p_value=0.01)))
        changes, _ = diff(before, report(finding(lift=31.5, p_value=0.004)))
        assert changes == []

    def test_a_quiet_week_is_not_worth_reading(self):
        review = build(report(finding()), snapshot(report(finding())), video_count=12)
        assert review.changes == []
        assert review.worth_reading is False


class TestVerdictMovement:
    def test_suggestive_becoming_confirmed_is_a_promotion(self):
        before = snapshot(report(finding(verdict=Verdict.SUGGESTIVE)))
        changes, _ = diff(before, report(finding(verdict=Verdict.CONFIRMED)))
        assert [c.kind for c in changes] == ["promoted"]
        assert changes[0].was == str(Verdict.SUGGESTIVE)
        assert "feeds back into generation" in changes[0].sentence()

    def test_confirmed_becoming_suggestive_is_a_demotion(self):
        before = snapshot(report(finding(verdict=Verdict.CONFIRMED)))
        changes, _ = diff(before, report(finding(verdict=Verdict.SUGGESTIVE)))
        assert [c.kind for c in changes] == ["demoted"]
        assert "no longer fed back" in changes[0].sentence()

    def test_movement_between_two_unbelieved_verdicts_is_not_reported(self):
        """Movement between two verdicts that both mean "not acted on" is not a
        promotion or a demotion; nothing about the generator changed.

        This asserted `appeared` — contradicting its own docstring. A finding that
        was already in last week's snapshot has not appeared, and reporting it made
        `worth_reading` true, so a week of pure sample-size drift sent a
        notification claiming a discovery.
        """
        for was, now in (
            (Verdict.INSUFFICIENT, Verdict.SUGGESTIVE),
            (Verdict.SUGGESTIVE, Verdict.INSUFFICIENT),
        ):
            before = snapshot(report(finding(verdict=was)))
            changes, _ = diff(before, report(finding(verdict=now)))
            assert changes == [], f"{was} -> {now} was reported as a change"

    def test_drift_between_unbelieved_verdicts_does_not_trigger_a_notification(self):
        before = snapshot(report(finding(verdict=Verdict.SUGGESTIVE)))
        review = build(report(finding(verdict=Verdict.INSUFFICIENT)), before, video_count=12)
        assert review.worth_reading is False


class TestReversal:
    def test_a_flipped_comparison_is_one_reversal_not_two_events(self):
        """ "A beats B" becoming "B beats A" is the system contradicting itself. As
        an add plus a remove it reads as two ordinary weeks of drift."""
        before = snapshot(report(finding(winner="question", loser="statement")))
        changes, _ = diff(before, report(finding(winner="statement", loser="question")))
        assert [c.kind for c in changes] == ["reversed"]
        assert "reverses last week" in changes[0].sentence()

    def test_a_reversal_does_not_also_report_the_old_side_as_gone(self):
        before = snapshot(
            report(finding(winner="question", loser="statement", verdict=Verdict.CONFIRMED))
        )
        changes, _ = diff(before, report(finding(winner="statement", loser="question")))
        assert "disappeared" not in [c.kind for c in changes]

    def test_a_reversal_in_a_different_metric_is_not_a_reversal(self):
        before = snapshot(report(finding(metric="ctr", winner="a", loser="b")))
        changes, _ = diff(before, report(finding(metric="avd_seconds", winner="b", loser="a")))
        kinds = sorted(c.kind for c in changes)
        assert kinds == ["appeared", "disappeared"]


class TestDisappearance:
    def test_a_confirmed_finding_going_away_is_reported(self):
        before = snapshot(report(finding(verdict=Verdict.CONFIRMED)))
        changes, _ = diff(before, report())
        assert [c.kind for c in changes] == ["disappeared"]
        assert changes[0].finding is None
        assert changes[0].sentence()

    @pytest.mark.parametrize("verdict", [Verdict.SUGGESTIVE, Verdict.INSUFFICIENT])
    def test_an_unconfirmed_finding_going_away_is_not_news(self, verdict):
        """Below `confirmed` a finding was never acted on, and it drops in and out
        as the sample moves by a single video. Reporting it makes the review noise."""
        before = snapshot(report(finding(verdict=verdict)))
        changes, _ = diff(before, report())
        assert changes == []


class TestPayload:
    def test_the_snapshot_keeps_identity_and_verdict_and_nothing_else(self):
        stored = snapshot(report(finding()))
        assert stored["findings"] == [
            {
                "dimension": "hook_device",
                "metric": "ctr",
                "winner": "question",
                "loser": "statement",
                "verdict": str(Verdict.CONFIRMED),
            }
        ]

    def test_a_review_serialises_its_changes_as_sentences(self):
        before = snapshot(report(finding(verdict=Verdict.SUGGESTIVE)))
        review = build(report(finding(verdict=Verdict.CONFIRMED)), before, video_count=14)
        payload = review.as_dict()

        assert payload["video_count"] == 14
        assert payload["is_first"] is False
        assert payload["worth_reading"] is True
        assert payload["confirmed_count"] == 1
        assert payload["changes"][0]["kind"] == "promoted"
        assert payload["changes"][0]["sentence"]

    def test_an_empty_review_still_serialises(self):
        payload = Review().as_dict()
        assert payload["findings"] == []
        assert payload["changes"] == []
        assert payload["worth_reading"] is False


class TestRun:
    """`review.run()` — the orchestration around the diff."""

    @pytest.fixture
    def stored(self, monkeypatch):
        """Capture what run() would persist, and control what it reads back."""
        from engine import review as review_mod

        box: dict = {"previous": None, "saved": [], "reports": []}

        async def fake_latest():
            return box["previous"]

        async def fake_save(payload, video_count, report=None):
            box["saved"].append((payload, video_count))
            # Kept apart from `saved`, so the existing assertions on that stay
            # about the diff snapshot and this one is about what a reader sees.
            box["reports"].append(report)

        import engine.repository as repo

        monkeypatch.setattr(repo, "latest_review_snapshot", fake_latest)
        monkeypatch.setattr(repo, "save_review_snapshot", fake_save)
        monkeypatch.setattr(review_mod, "logger", _SilentLogger())
        return box

    @pytest.fixture
    def records(self, monkeypatch):
        from engine.api import insights as insights_api

        insights_api.RECORDS.clear()
        monkeypatch.setattr(insights_api, "CHANNELS", {}, raising=False)
        yield insights_api.RECORDS
        insights_api.RECORDS.clear()

    @pytest.mark.asyncio
    async def test_a_run_with_no_channel_still_produces_and_stores_a_review(self, stored, records):
        from engine import review as review_mod

        result = await review_mod.run()
        assert result.is_first is True
        assert len(stored["saved"]) == 1

    @pytest.mark.asyncio
    async def test_an_analytics_failure_does_not_kill_the_review(
        self, stored, records, monkeypatch
    ):
        """The stored records are worth re-analysing whether or not today's numbers
        arrived — the sample grows as videos publish, independently of the API."""
        from engine import review as review_mod
        from engine.api import insights as insights_api

        monkeypatch.setattr(insights_api, "CHANNELS", {"default": object()}, raising=False)

        class Boom:
            def __init__(self, creds):
                pass

            async def per_video(self, days=90):
                raise RuntimeError("analytics is down")

        monkeypatch.setattr("engine.providers.analytics.Analytics", Boom)

        result = await review_mod.run()
        assert isinstance(result, Review)
        assert len(stored["saved"]) == 1

    @pytest.mark.asyncio
    async def test_the_report_is_built_from_the_refreshed_metrics(
        self, stored, records, monkeypatch
    ):
        """The fix for "the refresh was not persisted" introduced this one.

        Hydrating a second time after mutating re-reads the database and `update()`s
        the freshly-refreshed objects straight back out of the mapping, so the review
        analysed last week's numbers while writing this week's.
        """
        from engine import review as review_mod
        from engine.api import insights as insights_api
        from engine.insights import VideoRecord

        record = VideoRecord(video_id="v1", title="t", published_at="2026-01-01T00:00:00+00:00")
        records["v1"] = record
        monkeypatch.setattr(insights_api, "CHANNELS", {"default": object()}, raising=False)

        class Rows:
            def __init__(self, creds):
                pass

            async def per_video(self, days=90):
                return [
                    {
                        "video_id": "v1",
                        "ctr": 9.9,
                        "avd_seconds": 120.0,
                        "views": 5000,
                        "avd_percent": 71.0,
                    }
                ]

        monkeypatch.setattr("engine.providers.analytics.Analytics", Rows)

        # `current_records` re-reads in production. Simulate that faithfully: a
        # second call hands back a *different, stale* object for the same id.
        calls = {"n": 0}
        original = insights_api.current_records

        async def counting():
            calls["n"] += 1
            if calls["n"] > 1:
                records["v1"] = VideoRecord(
                    video_id="v1", title="t", published_at="2026-01-01T00:00:00+00:00"
                )
            return await original()

        monkeypatch.setattr(insights_api, "current_records", counting)

        # What `analyze` is *handed* is the only thing that decides the report.
        # Asserting on the local `record` reference proves nothing: the reload
        # replaces the entry in the mapping while that reference keeps the
        # mutation, so the check passes either way.
        seen: list = []
        import engine.insights as insights_mod

        original_analyze = insights_mod.analyze

        def capturing(videos, **kw):
            seen.extend(videos)
            return original_analyze(videos, **kw)

        monkeypatch.setattr(insights_mod, "analyze", capturing)

        await review_mod.run()

        assert seen, "analyze was never called"
        # Stated directly, because the assertion below only catches a second
        # hydration when the reload happens to hand back a different object. The
        # invariant being protected is that there is exactly one.
        assert calls["n"] == 1, "run() hydrated the records more than once"
        assert [v.ctr for v in seen] == [9.9], (
            "the report was built from records reloaded *after* the refresh, so it "
            "analysed last week's numbers while persisting this week's"
        )

    @pytest.mark.asyncio
    async def test_a_manual_run_becomes_the_baseline_for_the_next_diff(self, stored, records):
        """Otherwise Monday re-reports everything an ad-hoc run already showed."""
        from engine import review as review_mod

        await review_mod.run()
        assert stored["saved"], "a run that stores nothing leaves no baseline"

    async def test_the_readable_report_is_stored_next_to_the_snapshot(self, stored, records):
        """The snapshot is four strings per finding — everything the next diff
        needs and nothing a person would read. Storing only that is why the cron
        produced a review every Monday that nobody could ever see."""
        from engine import review as review_mod

        await review_mod.run()

        assert stored["reports"], "a snapshot was saved but no readable report"
        report = stored["reports"][-1]
        assert set(report) >= {"generated_at", "findings", "changes", "worth_reading"}


class _SilentLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class TestSchedule:
    def test_the_review_is_registered_as_a_weekly_cron_job(self):
        from engine.worker import WorkerSettings

        # Not `len(...) == 1` any more — FIX-TASKS E2 registered a second cron
        # job (thumbnail A/B sweeps) alongside this one. Found by name rather
        # than by position, so neither test cares which was registered first.
        names = [job.name for job in WorkerSettings.cron_jobs]
        assert any(name.endswith("weekly_review_task") for name in names)

    def test_it_fires_once_a_week_rather_than_all_day_monday(self):
        """arq reads an unset field as *every* value, so `cron(fn, weekday="mon")`
        on its own runs 1,440 times on Monday. `hour` and `minute` are what pin it
        to one firing."""
        job = _cron_job()
        assert job.weekday == "mon"
        assert job.hour == 6
        assert job.minute == 0
        assert job.second == 0

    def test_it_does_not_fire_on_worker_restart(self):
        """A restart is not a week passing, and a review triggered by one consumes
        the snapshot the real weekly diff was going to compare against."""
        assert _cron_job().run_at_startup is False


def _cron_job():
    from engine.worker import WorkerSettings

    return next(job for job in WorkerSettings.cron_jobs if job.name.endswith("weekly_review_task"))


class TestItCanBeReadBack:
    """The review was produced every Monday and readable by nobody.

    `run()` stored the diff snapshot — four strings and a verdict per finding — and
    returned the readable report into arq's result store, where `keep_result = 3600`
    dropped it an hour later. The only other way to see one was to run a fresh
    review, which consumes the baseline the real weekly diff compares against, so
    reading this week's destroyed next week's.
    """

    async def test_latest_review_returns_the_last_stored_report(self, database):
        from engine import repository

        assert await repository.latest_review() is None

        await repository.save_review_snapshot(
            {"findings": []}, video_count=1, report={"generated_at": "2026-08-03", "findings": []}
        )
        await repository.save_review_snapshot(
            {"findings": []}, video_count=2, report={"generated_at": "2026-08-10", "findings": []}
        )

        latest = await repository.latest_review()
        assert latest is not None
        assert latest["generated_at"] == "2026-08-10"

    async def test_rows_written_before_the_column_existed_are_skipped(self, database):
        """A row with no report is not the latest review, it is an absence. Ordering
        by date alone would return None for an install that has one readable review
        and one older snapshot written after it by a manual run."""
        from engine import repository

        await repository.save_review_snapshot(
            {"findings": []}, video_count=1, report={"generated_at": "2026-08-03"}
        )
        await repository.save_review_snapshot({"findings": []}, video_count=2)  # no report

        latest = await repository.latest_review()
        assert latest is not None and latest["generated_at"] == "2026-08-03"
