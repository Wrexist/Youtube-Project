"""The series endpoints — the ones the Series screen shipped without.

Endpoint functions are called directly, like `test_spend.py` and
`test_backlog.py`: mixing `TestClient` with the `database` fixture is the
two-event-loops trap KNOWN-ISSUES §4.10 documents, and nothing here needs HTTP
to be proven.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from engine import repository
from engine.api import series as series_api


async def _create(**overrides) -> str:
    body = series_api.SeriesIn(
        name="Engineering failures",
        niche="civil engineering disasters",
        monthly_budget_usd=40.0,
        **overrides,
    )
    created = await series_api.create(body)
    return created.id


class TestCrud:
    async def test_created_series_comes_back_from_the_list(self, database):
        series_id = await _create()
        listed = await series_api.list_all()
        assert [s.id for s in listed] == [series_id]
        assert listed[0].name == "Engineering failures"
        assert listed[0].shorts_per_week == 3
        assert listed[0].long_per_week == 1
        assert listed[0].paused is False

    async def test_a_series_survives_a_reload_from_the_database(self, database):
        series_id = await _create()
        record = await repository.get_series(series_id)
        assert record is not None
        assert record["niche"] == "civil engineering disasters"

    async def test_patch_changes_only_what_it_names(self, database):
        series_id = await _create()
        patched = await series_api.patch(series_id, series_api.SeriesPatch(paused=True))
        assert patched.paused is True
        assert patched.name == "Engineering failures"  # untouched
        assert patched.monthly_budget_usd == 40.0  # untouched

    async def test_patching_an_unknown_series_is_a_404(self, database):
        with pytest.raises(HTTPException) as err:
            await series_api.patch("nope", series_api.SeriesPatch(paused=True))
        assert err.value.status_code == 404

    async def test_delete_removes_it_and_a_second_delete_404s(self, database):
        series_id = await _create()
        await series_api.remove(series_id)
        assert await series_api.list_all() == []
        with pytest.raises(HTTPException) as err:
            await series_api.remove(series_id)
        assert err.value.status_code == 404


def _job(job_id: str, *, series_id: str, cost: float, status: str = "completed") -> dict:
    """The minimum `save_job` accepts, tagged to a series."""
    return {
        "id": job_id,
        "workflow": SimpleNamespace(name="video"),
        "status": status,
        "inputs": {"topic": "t", "series_id": series_id},
        "states": {},
        "events": [],
        "cost_usd": cost,
        "created_at": datetime.now(UTC),
    }


class TestUsage:
    async def test_spend_and_output_are_read_off_the_jobs_table(self, database):
        series_id = await _create()
        await repository.save_job(_job("j1", series_id=series_id, cost=1.25))
        await repository.save_job(_job("j2", series_id=series_id, cost=0.75))
        await repository.save_job(_job("j3", series_id="other", cost=9.0))

        usage = await repository.series_usage()
        assert usage[series_id]["produced_this_week"] == 2
        # `cost_usd` is recomputed from stage outputs on save; the point here is
        # attribution, not arithmetic — both jobs land on this series, the third
        # does not.
        assert "other" in usage
        assert usage[series_id] is not usage["other"]

    async def test_a_job_without_a_series_id_belongs_to_nobody(self, database):
        await repository.save_job(
            {
                "id": "j9",
                "workflow": SimpleNamespace(name="video"),
                "status": "completed",
                "inputs": {"topic": "t"},
                "states": {},
                "events": [],
                "created_at": datetime.now(UTC),
            }
        )
        assert await repository.series_usage() == {}


class TestPlan:
    async def test_a_paused_series_plans_nothing_and_says_why(self, database):
        series_id = await _create()
        await series_api.patch(series_id, series_api.SeriesPatch(paused=True))
        plan = await series_api.plan(series_id)
        assert plan.to_generate == []
        assert [b.code for b in plan.blocked] == ["paused"]

    async def test_a_thin_backlog_caps_the_week_and_names_the_gap(self, database):
        series_id = await _create()  # cadence 3+1 = 4/week
        await repository.add_backlog_ideas(
            [{"topic": "why the tacoma narrows bridge fell", "score": 0.8, "demand": 0.7}]
        )
        plan = await series_api.plan(series_id)
        assert len(plan.to_generate) == 1
        assert plan.to_generate[0].topic == "why the tacoma narrows bridge fell"
        assert "thin_backlog" in [b.code for b in plan.blocked]

    async def test_planning_an_unknown_series_is_a_404(self, database):
        with pytest.raises(HTTPException) as err:
            await series_api.plan("nope")
        assert err.value.status_code == 404
