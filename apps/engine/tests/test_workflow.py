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
        # These produce plain strings, so they opt into hand-editing the same way
        # `description` does. Without it `mark_edited` refuses them — correctly,
        # since it now defaults to refusing — and the invalidation tests below
        # would be measuring the guard rather than the propagation they are about.
        self.editable = True
        self.editable_type = str

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


async def test_a_stage_holding_a_dataclass_refuses_a_hand_edit():
    """`mark_edited` writes straight over the stage's value, and there is no undo.

    A plausible JSON payload for a dataclass stage makes the next stage raise
    `AttributeError`, the retry loop burns three attempts, the job fails, and the
    plain dict is persisted — so the corruption survives a restart with no route
    back. Refusing before the write is the only place this can be caught.
    """
    wf, _ = linear()
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)

    structured = Counter("b")
    structured.editable = False
    wf._by_name["b"] = structured
    wf.stages[1] = structured

    with pytest.raises(WorkflowError, match="cannot be edited by hand"):
        wf.mark_edited(states, "b", {"anything": "at all"})

    # And the original value is untouched — the refusal happens before the write.
    assert states["b"].output.value == "ab"
    assert states["c"].status is StageStatus.DONE


async def test_an_edit_of_the_wrong_type_is_refused():
    wf, _ = linear()
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)

    with pytest.raises(WorkflowError, match="expects str"):
        wf.mark_edited(states, "b", {"text": "a dict where a string belongs"})
    assert states["b"].output.value == "ab"


async def test_a_list_edit_must_contain_only_strings():
    """`isinstance(value, list)` alone lets a list of dicts through.

    That is the same corruption in a different coat: `list` is checkable,
    `list[str]` is not, so the element type needs asserting separately.
    """
    wf, (a, b, c) = linear()
    b.editable_type = list
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)

    with pytest.raises(WorkflowError, match="list of strings"):
        wf.mark_edited(states, "b", [{"not": "a string"}])

    wf.mark_edited(states, "b", ["fine", "also fine"])
    assert states["b"].output.value == ["fine", "also fine"]


# ── an edit must clear the limits the stage's own run() clears ──────────────
#
# `editable_type` proves the *shape* and nothing else, so the edit path skipped
# every ceiling the generated path enforces: `DescriptionStage.run` fits its output
# to 5,000 bytes and `TagsStage.run` trims to 500 characters, and an edit went round
# both. The result was accepted, persisted, and rejected by YouTube — after the
# upload had already spent 1,600 quota units on it.
#
# Against the real seo stages rather than a `Counter`: the limits are YouTube's, and
# a fixture with invented ones would pass while the shipped stage was wrong.


def _real_states(stage: str, value):
    """The video workflow with one stage finished, ready to be edited."""
    from engine.workflows import video

    wf = video.get("video")
    states = wf.initial_states()
    states[stage].status = StageStatus.DONE
    states[stage].output = StageOutput(value=value, provenance=Provenance(model="test"))
    return wf, states


def test_an_over_long_description_edit_is_refused():
    from engine.workflows.seo import DESCRIPTION_MAX

    wf, states = _real_states("description", "the generated description")

    with pytest.raises(WorkflowError, match="YouTube's limit"):
        wf.mark_edited(states, "description", "x" * (DESCRIPTION_MAX + 1))

    assert states["description"].output.value == "the generated description"


def test_the_description_ceiling_is_counted_in_bytes():
    """YouTube measures the field in bytes and `len()` measures characters.

    The assembled description is full of em dashes — one per source line — and each
    is three bytes, so a block Python calls 2,000 long is 6,000 to the API. A
    character-counted guard passes it and the upload is refused.
    """
    wf, states = _real_states("description", "short")

    with pytest.raises(WorkflowError):
        wf.mark_edited(states, "description", "—" * 2000)


def test_a_description_inside_the_ceiling_is_still_editable():
    """The guard must not cost the interaction the Create screen is built on."""
    wf, states = _real_states("description", "the generated description")

    wf.mark_edited(states, "description", "A better description.")
    assert states["description"].output.value == "A better description."


def test_an_over_budget_tag_edit_is_trimmed_rather_than_refused():
    """Tags clamp where the description refuses, and the asymmetry is deliberate.

    Nobody adding a tag is tracking a 500-character running total, and the generated
    path already trims silently — so an edit that goes over is trimmed the same way.
    Losing the tail of a tag list is recoverable; losing the tail of someone's prose
    is not.
    """
    from engine.workflows.seo import TAGS_TOTAL_MAX

    wf, states = _real_states("tags", ["bridges"])

    wf.mark_edited(states, "tags", [f"bridge failure analysis {i:02d}" for i in range(40)])

    stored = states["tags"].output.value
    assert stored, "trimming must not empty the field"
    assert len(stored) < 40, "an over-budget list must actually lose entries"
    assert sum(len(t) + (3 if " " in t else 1) for t in stored) <= TAGS_TOTAL_MAX


def test_an_empty_tag_is_dropped_rather_than_sinking_the_whole_field():
    """YouTube rejects the entire `tags` field over one blank entry, and a blank is
    what a trailing comma in the editor produces."""
    wf, states = _real_states("tags", ["bridges"])

    wf.mark_edited(states, "tags", ["bridges", "   ", "", "collapse"])
    assert states["tags"].output.value == ["bridges", "collapse"]


async def test_editing_an_unknown_stage_is_refused():
    wf, _ = linear()
    states = await wf.run("job1", {}, await collect([]), budget_usd=10)
    with pytest.raises(WorkflowError, match="unknown stage"):
        wf.mark_edited(states, "not-a-stage", "x")


def test_only_plain_text_stages_are_editable():
    """The whitelist, asserted against the real workflow rather than a fixture.

    If a stage holding a dataclass is ever marked editable, this is the test that
    should stop it — the failure mode is silent and unrecoverable.
    """
    from engine.workflows import video

    editable = {s.name for s in video.get("video").stages if s.editable}
    assert editable == {"description", "tags"}, (
        f"unexpected editable stages: {editable} — every one of these accepts a raw "
        f"JSON overwrite of its value"
    )


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
