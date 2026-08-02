"""The workflow framework.

Every piece of intelligence in Studio is a Stage inside a Workflow. The framework
exists to give all of them, uniformly and without each stage having to care:

  * **Resumability** — a job survives a worker restart. Completed stage outputs are
    persisted; a resumed run replays them instead of regenerating.
  * **Staleness propagation** — when the user edits a stage's output in the UI,
    everything transitively downstream is marked stale and re-runs. Nothing upstream
    is touched. This is what makes the Create screen editable rather than
    all-or-nothing.
  * **Provenance** — every output records the prompt and model that produced it.
    Phase 8's feedback loop is impossible without this, so the framework enforces it
    rather than trusting stages to remember.
  * **Cost metering and a hard ceiling** — spend is accumulated across stages and the
    run aborts before exceeding the per-video cap.
  * **Progress events** — a single event stream that the SSE endpoint forwards to the
    browser verbatim.

A Stage never talks to the database, the event bus, or the cost ledger directly.
It receives a context, reads its dependencies, returns an output. That constraint is
what makes stages independently testable and re-runnable.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from loguru import logger

T = TypeVar("T")


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowError(Exception):
    """A stage failed in a way that should stop the run."""


class BudgetExceeded(WorkflowError):
    """The run would exceed its cost ceiling. Raised before the spend happens."""


@dataclass
class Provenance:
    """How an output came to exist. Required — see module docstring."""

    model: str | None = None
    prompt: str | None = None
    sources: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class StageOutput(Generic[T]):
    value: T
    provenance: Provenance
    cost_usd: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)  # name -> storage key


@dataclass
class StageState:
    name: str
    status: StageStatus = StageStatus.PENDING
    output: StageOutput | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0

    @property
    def elapsed_ms(self) -> int:
        if self.started_at is None:
            return 0
        end = self.finished_at or time.monotonic()
        return int((end - self.started_at) * 1000)


EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class WorkflowContext:
    """What a stage is handed. Deliberately small."""

    def __init__(
        self,
        job_id: str,
        inputs: dict[str, Any],
        states: dict[str, StageState],
        emit: EventSink,
        budget_usd: float,
    ) -> None:
        self.job_id = job_id
        self.inputs = inputs
        self._states = states
        self._emit = emit
        self.budget_usd = budget_usd
        #: Set by `Workflow._run_stage` around each stage so `progress()` can name
        #: the stage it came from. The context is per-run and stages run one at a
        #: time, so a single slot is enough.
        self._current_stage: str | None = None

    def get(self, stage_name: str) -> Any:
        """Read a completed dependency's value.

        Raises rather than returning None — a stage reading a dependency that hasn't
        run is a wiring bug in `depends_on`, and silently getting None turns it into a
        confusing failure three stages later.
        """
        state = self._states.get(stage_name)
        if state is None:
            raise WorkflowError(f"unknown stage '{stage_name}'")
        if state.status is not StageStatus.DONE or state.output is None:
            raise WorkflowError(
                f"stage '{stage_name}' has not completed (status={state.status.value}); "
                "check depends_on"
            )
        return state.output.value

    def try_get(self, stage_name: str, default: Any = None) -> Any:
        """For genuinely optional dependencies — a stage that was skipped."""
        state = self._states.get(stage_name)
        if state is None or state.output is None:
            return default
        return state.output.value

    @property
    def spent_usd(self) -> float:
        return sum(s.output.cost_usd for s in self._states.values() if s.output)

    async def progress(self, message: str, fraction: float | None = None) -> None:
        """Report intra-stage progress. Long stages (render, upload) must use this."""
        await self._emit(
            {
                "type": "stage.progress",
                "job_id": self.job_id,
                # Self-describing, like every other frame. This was the one event
                # type with no `stage`, so a consumer had to guess which row it
                # belonged to by looking for whichever stage was RUNNING — a guess
                # that is wrong for a resumed job whose graph the client never
                # loaded, and one the web reducer carries a special case for.
                "stage": self._current_stage,
                "message": message,
                "fraction": fraction,
            }
        )


class Stage(Generic[T]):
    """One unit of work.

    Subclasses set `name`, `title`, `depends_on` and implement `run`. Everything else
    — retries, timing, cost accounting, event emission, provenance validation — is
    handled by the Workflow.
    """

    name: str = ""
    title: str = ""
    depends_on: tuple[str, ...] = ()

    #: Retries for transient failures. Provider calls fail; renders mostly don't.
    max_attempts: int = 3
    #: Seconds before the stage is abandoned. None means no limit (renders).
    timeout_s: float | None = 180.0
    #: A skipped optional stage doesn't fail the run.
    optional: bool = False
    #: Rough pre-flight cost estimate, used to refuse a run that can't afford to finish.
    estimated_cost_usd: float = 0.0
    #: How often to emit a keepalive progress message while a stage is running.
    heartbeat_interval_s: float = 10.0

    #: May an operator replace this stage's value by hand?
    #:
    #: Off by default, and that default is the point. `mark_edited` writes the
    #: submitted JSON straight over the stage's output, and most stages hold
    #: dataclasses — a plausible payload for `titles` leaves `DescriptionStage`
    #: raising `AttributeError` on `variants[0].text`, the retry loop burns three
    #: attempts, the job fails, and the plain dict is persisted, so the corruption
    #: survives a restart with no route back.
    #:
    #: Turned on only for stages whose value is a plain `str` or `list[str]`,
    #: which is also the only kind of edit anyone has asked for: fixing the
    #: wording of a description, or adding a tag. `editable_type` is checked at
    #: edit time so the promise is enforced, not merely declared.
    editable: bool = False
    #: The type an edit must produce. Only consulted when `editable`.
    editable_type: type | None = None

    async def run(self, ctx: WorkflowContext) -> StageOutput[T]:  # pragma: no cover
        raise NotImplementedError

    def validate_edit(self, value: Any) -> Any:
        """Check — and optionally clamp — a hand-submitted value. Returns what to store.

        `editable_type` only proves the *shape*. It says nothing about the limits the
        stage's own `run` enforces, so an edit went straight past every one of them:
        a 20,000-character description and a 2,590-character tag list were both
        accepted, persisted, and only rejected by YouTube after the upload had spent
        its 1,600 units. Raise `WorkflowError` to refuse, or return a corrected
        value to clamp.
        """
        return value

    def should_skip(self, ctx: WorkflowContext) -> bool:
        """Override for conditional stages (e.g. thumbnail only for long-form)."""
        return False

    def __repr__(self) -> str:
        return f"<Stage {self.name}>"


class Workflow:
    """An ordered, dependency-checked set of stages."""

    def __init__(self, name: str, stages: list[Stage]) -> None:
        self.name = name
        self.stages = stages
        self._by_name = {s.name: s for s in stages}
        self._validate()

    def _validate(self) -> None:
        """Catch wiring mistakes at import time, not three minutes into a render."""
        seen: set[str] = set()
        for stage in self.stages:
            if not stage.name:
                raise ValueError(f"stage {stage!r} has no name")
            if stage.name in seen:
                raise ValueError(f"duplicate stage name '{stage.name}' in workflow '{self.name}'")
            for dep in stage.depends_on:
                if dep not in seen:
                    raise ValueError(
                        f"stage '{stage.name}' depends on '{dep}', which is not defined "
                        f"before it in workflow '{self.name}'"
                    )
            seen.add(stage.name)

    # ── staleness ───────────────────────────────────────────────────────────

    def dependents_of(self, stage_name: str) -> list[str]:
        """Every stage transitively downstream of `stage_name`, in run order.

        This is what the UI calls when the user edits a stage's output: the edited
        stage keeps its new value, everything returned here is invalidated.
        """
        stale = {stage_name}
        out: list[str] = []
        for stage in self.stages:
            if stage.name in stale:
                continue
            if any(dep in stale for dep in stage.depends_on):
                stale.add(stage.name)
                out.append(stage.name)
        return out

    def mark_edited(
        self, states: dict[str, StageState], stage_name: str, new_value: Any
    ) -> list[str]:
        """Apply a user edit and invalidate downstream. Returns the invalidated names.

        Refuses anything the stage has not declared editable, and anything of the
        wrong shape. Both checks are before the write, because there is no way
        back after it: the value is persisted, and a corrupted stage takes every
        stage downstream of it with them.
        """
        stage = next((s for s in self.stages if s.name == stage_name), None)
        if stage is None:
            raise WorkflowError(f"unknown stage '{stage_name}'")

        if not stage.editable:
            raise WorkflowError(
                f"'{stage_name}' cannot be edited by hand — its value is a structured "
                f"object, not text. Re-run it instead."
            )

        expected = stage.editable_type
        if expected is not None and not isinstance(new_value, expected):
            got = type(new_value).__name__
            raise WorkflowError(f"'{stage_name}' expects {expected.__name__}, got {got}")
        # `list[str]` is not checkable with isinstance, so the element type is
        # asserted separately — a list of dicts would otherwise sail through a
        # bare `isinstance(value, list)` and reproduce the exact corruption this
        # guard exists to prevent.
        if expected is list and not all(isinstance(item, str) for item in new_value):
            raise WorkflowError(f"'{stage_name}' expects a list of strings")

        # Shape is not the same as legality — see `Stage.validate_edit`. Before the
        # write, like the checks above, because there is no way back after it.
        new_value = stage.validate_edit(new_value)

        state = states[stage_name]
        if state.output is None:
            raise WorkflowError(f"cannot edit '{stage_name}': it has not produced output")
        state.output.value = new_value
        state.output.provenance.model = None
        state.output.provenance.params["edited_by_user"] = True

        invalidated = self.dependents_of(stage_name)
        for name in invalidated:
            states[name].status = StageStatus.STALE
            states[name].output = None
        return invalidated

    # ── execution ───────────────────────────────────────────────────────────

    def initial_states(self) -> dict[str, StageState]:
        return {s.name: StageState(name=s.name) for s in self.stages}

    async def run(
        self,
        job_id: str,
        inputs: dict[str, Any],
        emit: EventSink,
        states: dict[str, StageState] | None = None,
        budget_usd: float = 8.0,
        start_from: str | None = None,
    ) -> dict[str, StageState]:
        """Execute the workflow.

        Stages already DONE are replayed from their stored output — that is both the
        resume path after a crash and the re-run path after a user edit. Passing
        `start_from` forces that stage and everything downstream to re-run.
        """
        states = states or self.initial_states()
        ctx = WorkflowContext(job_id, inputs, states, emit, budget_usd)

        if start_from:
            if start_from not in self._by_name:
                raise WorkflowError(f"unknown start_from stage '{start_from}'")
            for name in [start_from, *self.dependents_of(start_from)]:
                states[name].status = StageStatus.STALE
                states[name].output = None

        await emit({"type": "workflow.started", "job_id": job_id, "workflow": self.name})

        for stage in self.stages:
            state = states[stage.name]

            if state.status is StageStatus.DONE:
                await emit(
                    {
                        "type": "stage.replayed",
                        "job_id": job_id,
                        "stage": stage.name,
                        "title": stage.title,
                    }
                )
                continue

            if stage.should_skip(ctx):
                state.status = StageStatus.SKIPPED
                await emit({"type": "stage.skipped", "job_id": job_id, "stage": stage.name})
                continue

            # Refuse to start a stage we cannot afford to finish. Failing here is far
            # better than failing after the spend.
            if ctx.spent_usd + stage.estimated_cost_usd > budget_usd:
                reason = (
                    f"budget ceiling ${budget_usd:.2f} would be exceeded "
                    f"(spent ${ctx.spent_usd:.2f}, '{stage.name}' needs "
                    f"~${stage.estimated_cost_usd:.2f})"
                )
                state.error = reason

                if stage.optional:
                    # Optional means optional. This branch used to raise regardless,
                    # and since ThumbnailStage is both optional and last, an
                    # unaffordable thumbnail marked the whole job `failed` — while
                    # `POST /v1/jobs/{id}/publish` refuses anything not `completed`.
                    # A finished, rendered, stored MP4 became unpublishable over a
                    # skippable stage. The retry-exhaustion path below already got
                    # this right; only the pre-flight check did not.
                    state.status = StageStatus.SKIPPED
                    await emit(
                        {
                            "type": "stage.skipped",
                            "job_id": job_id,
                            "stage": stage.name,
                            "message": reason,
                        }
                    )
                    continue

                state.status = StageStatus.FAILED
                await emit(
                    {
                        "type": "workflow.failed",
                        "job_id": job_id,
                        "stage": stage.name,
                        "error": reason,
                    }
                )
                raise BudgetExceeded(reason)

            await self._run_stage(stage, state, ctx, emit, job_id)

            if state.status is StageStatus.FAILED and not stage.optional:
                await emit(
                    {
                        "type": "workflow.failed",
                        "job_id": job_id,
                        "stage": stage.name,
                        "error": state.error,
                    }
                )
                raise WorkflowError(f"stage '{stage.name}' failed: {state.error}")

        await emit(
            {
                "type": "workflow.completed",
                "job_id": job_id,
                "cost_usd": round(ctx.spent_usd, 4),
            }
        )
        return states

    async def _run_stage(
        self,
        stage: Stage,
        state: StageState,
        ctx: WorkflowContext,
        emit: EventSink,
        job_id: str,
    ) -> None:
        state.status = StageStatus.RUNNING
        state.started_at = time.monotonic()
        state.error = None
        ctx._current_stage = stage.name
        await emit(
            {
                "type": "stage.started",
                "job_id": job_id,
                "stage": stage.name,
                "title": stage.title,
            }
        )

        for attempt in range(1, stage.max_attempts + 1):
            state.attempts = attempt
            try:

                async def _heartbeat() -> None:
                    # Some providers do not stream partial tokens, and some research
                    # sources are slow. A stage can therefore be healthy while the UI
                    # appears frozen. Emit a low-rate heartbeat so the Create screen
                    # says what is happening instead of looking stuck.
                    while True:
                        await asyncio.sleep(stage.heartbeat_interval_s)
                        elapsed = int(time.monotonic() - (state.started_at or time.monotonic()))
                        await ctx.progress(f"still working — {elapsed}s elapsed")

                heartbeat = asyncio.create_task(_heartbeat())
                try:
                    coro = stage.run(ctx)
                    output = (
                        await asyncio.wait_for(coro, stage.timeout_s)
                        if stage.timeout_s
                        else await coro
                    )
                finally:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat

                # Provenance is not optional. A stage that forgets it silently breaks
                # the analytics feedback loop months later, so fail loudly now.
                if output.provenance is None:
                    raise WorkflowError(f"stage '{stage.name}' returned no provenance")
                output.provenance.duration_ms = state.elapsed_ms

                state.output = output
                state.status = StageStatus.DONE
                state.finished_at = time.monotonic()
                await emit(
                    {
                        "type": "stage.completed",
                        "job_id": job_id,
                        "stage": stage.name,
                        "summary": summarize(output.value),
                        "cost_usd": round(output.cost_usd, 4),
                        "elapsed_ms": state.elapsed_ms,
                    }
                )
                return

            except TimeoutError:
                err = f"timed out after {stage.timeout_s}s"
            except WorkflowError:
                raise
            except Exception as exc:  # noqa: BLE001 — stages wrap arbitrary providers
                err = f"{type(exc).__name__}: {exc}"
                logger.exception("stage {} attempt {} failed", stage.name, attempt)

            if attempt < stage.max_attempts:
                backoff = min(2 ** (attempt - 1), 15)
                await emit(
                    {
                        "type": "stage.retrying",
                        "job_id": job_id,
                        "stage": stage.name,
                        "attempt": attempt,
                        "error": err,
                    }
                )
                await asyncio.sleep(backoff)
            else:
                state.status = StageStatus.SKIPPED if stage.optional else StageStatus.FAILED
                state.error = err
                state.finished_at = time.monotonic()
                # Retries exhausted, and until now this emitted nothing at all.
                #
                # For a *required* stage the run does go on to emit
                # `workflow.failed`, but that frame names no stage, so the row that
                # actually died stayed on "retrying" in the UI forever and the
                # persistence hooks — which key off `stage.failed` — never wrote it.
                #
                # For an *optional* stage it was worse: nothing was emitted, the run
                # continued, and `workflow.completed` arrived with that row still
                # spinning next to an enabled Publish button. `ThumbnailStage` is
                # optional and last, so that is the likelier of the two.
                await emit(
                    {
                        "type": "stage.skipped" if stage.optional else "stage.failed",
                        "job_id": job_id,
                        "stage": stage.name,
                        "error": err,
                        "message": err,  # `stage.skipped` frames elsewhere carry this
                        "elapsed_ms": state.elapsed_ms,
                    }
                )


def summarize(value: Any) -> str:
    """The one-line collapse shown on a completed stage row in the UI.

    The Create screen shows this next to every finished stage, so it has to be
    genuinely informative — "8 sources", "1,240 words · ~8:20" — not a type name.
    """
    if hasattr(value, "summary"):
        summary = value.summary
        return summary() if callable(summary) else str(summary)
    if isinstance(value, str):
        words = len(value.split())
        return f"{words:,} words" if words > 30 else value[:80]
    if isinstance(value, (list, tuple)):
        return f"{len(value)} items"
    if isinstance(value, dict):
        return ", ".join(list(value)[:4])
    return str(value)[:80]
