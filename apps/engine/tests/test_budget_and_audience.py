"""Two failures that each turn a working run into a broken one.

Both are cases where the code takes the harshest possible action on something it
should have shrugged at.
"""

from __future__ import annotations

import pytest

from engine.workflows.base import (
    BudgetExceeded,
    Stage,
    StageOutput,
    StageStatus,
    Workflow,
    WorkflowContext,
)


class _Cheap(Stage[str]):
    name = "cheap"
    title = "Cheap"
    estimated_cost_usd = 0.001

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        from engine.workflows.base import Provenance

        return StageOutput(value="done", provenance=Provenance(model="test"), cost_usd=0.001)


class _ExpensiveOptional(_Cheap):
    name = "extra"
    title = "Expensive but optional"
    optional = True
    estimated_cost_usd = 99.0


class _ExpensiveRequired(_Cheap):
    name = "must"
    title = "Expensive and required"
    optional = False
    estimated_cost_usd = 99.0


async def _run(stages, budget):
    workflow = Workflow("t", stages)
    states = workflow.initial_states()
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    await workflow.run(job_id="j", inputs={}, emit=emit, states=states, budget_usd=budget)
    return states, events


# ── the budget ceiling ──────────────────────────────────────────────────────


async def test_an_unaffordable_optional_stage_is_skipped_not_failed():
    """ThumbnailStage is optional *and* last.

    The pre-flight check raised regardless of `optional`, so an unaffordable
    thumbnail marked the whole job `failed` — and the publish endpoint refuses
    anything not `completed`. A finished, rendered, stored MP4 became
    unpublishable over a stage that was allowed to be skipped.
    """
    states, events = await _run([_Cheap(), _ExpensiveOptional()], budget=0.01)

    assert states["cheap"].status is StageStatus.DONE
    assert states["extra"].status is StageStatus.SKIPPED
    assert "budget" in (states["extra"].error or "")
    assert [e["type"] for e in events if e["type"] == "workflow.failed"] == []
    assert any(e["type"] == "stage.skipped" and e["stage"] == "extra" for e in events)


async def test_an_unaffordable_required_stage_still_fails_the_job():
    """The guard has to keep working — this is the case it exists for."""
    with pytest.raises(BudgetExceeded, match="budget ceiling"):
        await _run([_Cheap(), _ExpensiveRequired()], budget=0.01)


async def test_the_skip_reason_names_the_ceiling_and_the_stage():
    """A bare "skipped" is not an acceptable thing to show someone."""
    states, _ = await _run([_Cheap(), _ExpensiveOptional()], budget=0.01)
    error = states["extra"].error or ""
    assert "$0.01" in error and "extra" in error


# ── the audience profile ────────────────────────────────────────────────────


def test_the_audience_profile_field_is_daily_not_weekday():
    """`GET /v1/analytics/audience` raised on every single request: TypeError in
    the constructor, AttributeError in the reader."""
    from engine.scheduling import AudienceProfile

    profile = AudienceProfile(daily=[1.0] * 7)
    assert profile.daily == [1.0] * 7
    assert not hasattr(profile, "weekday")

    with pytest.raises(TypeError):
        AudienceProfile(weekday=[1.0] * 7)


def test_nothing_constructs_or_reads_a_weekday_attribute():
    """The guard: this was two separate call sites, both wrong the same way."""
    import re
    from pathlib import Path

    engine = Path(__file__).resolve().parents[1] / "engine"
    offenders = [
        f"{p.name}:{i}"
        for p in engine.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"AudienceProfile\([^)]*weekday=|profile\.weekday\b", line)
    ]
    assert not offenders, f"AudienceProfile has no `weekday` field: {offenders}"
