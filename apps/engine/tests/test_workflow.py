"""Framework tests.

These cover the behaviours the whole system leans on — resume, staleness
propagation, the budget ceiling, retries — because a bug in any of them shows up as
a confusing failure three stages downstream rather than as an obvious one here.
"""

from __future__ import annotations

import pytest

from engine.workflows.base import (
    BudgetExceeded,
    Provenance,
    Stage,
    StageOutput,
    StageStatus,
    Workflow,
    WorkflowContext,
    WorkflowError,
)


class Counter(Stage[str]):
    """Records how many times it actually executed, so replay can be observed."""

    def __init__(self, name: str, depends_on: tuple[str, ...] = (), cost: float = 0.0):
        self.name = name
        self.title = name.title()
        self.depends_on = depends_on
        self.estimated_cost_usd = cost
        self.runs = 0

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        self.runs += 1
        upstream = "".join(ctx.get(d) for d in self.depends_on)
        return StageOutput(
            value=f"{upstream}{self.name}",
            cost_usd=self.estimated_cost_usd,
            provenance=Provenance(model="test"),
        )


async def collect(events: list):
    async def emit(event: dict) -> None:
        events.append(event)

    return emit


def linear() -> tuple[Workflow, list[Counter]]:
    a = Counter("a")
    b = Counter("b", ("a",))
    c = Counter("c", ("b",))
    return Workflow("t", [a, b, c]), [a, b, c]


async def test_runs_in_order_and_threads_values():
    wf, (a, b, c) = linear()
    events: list = []
    states = await wf.run("job1", {}, await collect(events), budget_usd=10)

    assert states["c"].output.value == "abc"
    assert all(s.status is StageStatus.DONE for s in states.values())
    assert events[0]["type"] == "workflow.started"
    assert events[-1]["type"] == "workflow.completed"


async def test_completed_stages_replay_on_resume():
    """The resume path. A restarted worker must not pay to regenerate finished work."""
    wf, (a, b, c) = linear()
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)

    await wf.run("job1", {}, await collect([]), states=states, budget_usd=10)
    assert (a.runs, b.runs, c.runs) == (1, 1, 1)


async def test_edit_invalidates_only_downstream():
    """The Create screen's core interaction: edit a stage, keep everything above it."""
    wf, (a, b, c) = linear()
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)

    invalidated = wf.mark_edited(states, "b", "EDITED")
    assert invalidated == ["c"]
    assert states["a"].status is StageStatus.DONE
    assert states["c"].status is StageStatus.STALE

    await wf.run("job1", {}, await collect([]), states=states, budget_usd=10)
    assert a.runs == 1  # untouched
    assert b.runs == 1  # holds the user's value, not regenerated
    assert c.runs == 2  # rebuilt on top of the edit
    assert states["c"].output.value == "EDITEDc"
    assert states["b"].output.provenance.params["edited_by_user"] is True


async def test_budget_ceiling_refuses_before_spending():
    wf = Workflow("t", [Counter("a", cost=3.0), Counter("b", ("a",), cost=3.0)])
    with pytest.raises(BudgetExceeded):
        await wf.run("job1", {}, await collect([]), budget_usd=4.0)


async def test_retries_then_fails_with_message():
    class Flaky(Stage[str]):
        name, title, max_attempts, timeout_s = "flaky", "Flaky", 2, 1.0

        async def run(self, ctx):
            raise ValueError("provider exploded")

    wf = Workflow("t", [Flaky()])
    events: list = []
    with pytest.raises(WorkflowError):
        await wf.run("job1", {}, await collect(events), budget_usd=10)

    assert any(e["type"] == "stage.retrying" for e in events)
    assert any(e["type"] == "workflow.failed" for e in events)


async def test_optional_stage_failure_does_not_stop_the_run():
    class Optional(Stage[str]):
        name, title, optional, max_attempts = "opt", "Opt", True, 1

        async def run(self, ctx):
            raise ValueError("nope")

    after = Counter("after")
    states = await Workflow("t", [Optional(), after]).run(
        "job1", {}, await collect([]), budget_usd=10
    )
    assert states["opt"].status is StageStatus.SKIPPED
    assert after.runs == 1


async def test_reading_an_incomplete_dependency_is_an_error():
    """A wiring bug should surface here, not as a None three stages later."""

    class Bad(Stage[str]):
        name, title = "bad", "Bad"

        async def run(self, ctx):
            ctx.get("nonexistent")

    with pytest.raises(WorkflowError):
        await Workflow("t", [Bad()]).run("j", {}, await collect([]), budget_usd=10)


def test_dependency_order_is_validated_at_construction():
    class Late(Stage[str]):
        name, title, depends_on = "late", "Late", ("missing",)

        async def run(self, ctx): ...

    with pytest.raises(ValueError, match="depends on"):
        Workflow("t", [Late()])
