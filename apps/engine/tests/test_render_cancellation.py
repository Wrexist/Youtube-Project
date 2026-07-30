"""Cancelling a render that is already inside MoviePy.

`asyncio.to_thread` is not cancellable. Cancelling the job's task raises
`CancelledError` in the *awaiting coroutine* and does nothing whatsoever to the
thread, so the encode carried on to completion — CPU-saturating a box whose
operator had pressed Cancel ten minutes earlier — and `async with _render_slots()`
released its slot the moment the await unwound, letting the next job start a second
encode alongside the one that would not stop. Two renders on a machine configured
for one, neither of which anybody asked for.

So the thread needs to be *told*, and the coroutine has to wait for it to go. Both
halves are asserted here, and neither is visible from a test that cancels a
coroutine and checks the status.

The render itself is stubbed. What is under test is the handshake — a real encode
would add a minute to the suite and prove nothing extra about it.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


def _abort_signal(args: tuple, kwargs: dict) -> threading.Event | None:
    """The abort flag among whatever `_render_sync` was handed.

    Found by type rather than by position or keyword: how the signal is plumbed
    through is an implementation choice, that there *is* one is not. This keeps the
    test pinned to the contract instead of to a parameter name.
    """
    return next(
        (a for a in (*args, *kwargs.values()) if isinstance(a, threading.Event)),
        None,
    )


@pytest.fixture
def stub_render(monkeypatch, tmp_path):
    """`_render_sync` replaced by a thread that blocks until it is told to stop.

    Returns the two Events the assertions read: one set when the render thread is
    running, one set when it has actually left. The second is the whole point —
    without it "cancelled" means only that somebody stopped waiting.
    """
    from engine.render import compose

    monkeypatch.setenv("STUDIO_STORAGE_ROOT", str(tmp_path))
    from engine.settings import get_settings

    get_settings.cache_clear()

    entered = threading.Event()
    exited = threading.Event()

    def blocking_render(*args, **kwargs):
        signal = _abort_signal(args, kwargs)
        assert signal is not None, "the render thread was given no way to be told to stop"
        entered.set()
        try:
            if not signal.wait(timeout=20):
                raise AssertionError("the abort was never signalled to the render thread")
            raise compose.RenderAborted("cancelled")
        finally:
            exited.set()

    monkeypatch.setattr(compose, "_render_sync", blocking_render)
    return entered, exited


async def _start_render(job_id: str = "j1") -> asyncio.Task:
    """`compose_video` running against no clips, so only the encode step matters."""
    from engine.render import compose

    async def on_progress(_fraction: float, _message: str) -> None:
        return None

    return asyncio.create_task(
        compose.compose_video(
            clips=[],
            beats=[],
            audio_path=None,
            cues=[],
            aspect="1:1",
            job_id=job_id,
            on_progress=on_progress,
        )
    )


def test_the_render_thread_can_be_told_to_stop():
    """The signal has to reach `_render_sync`, or nothing below is reachable.

    Stated as its own test because the failure is otherwise reported as a timeout
    twenty seconds into the test after it, which says nothing about the cause.
    """
    import inspect

    from engine.render import compose

    assert issubclass(compose.RenderAborted, Exception)
    parameters = inspect.signature(compose._render_sync).parameters
    assert any("abort" in name or "cancel" in name for name in parameters), (
        f"_render_sync takes no abort signal: {list(parameters)}"
    )


async def test_cancelling_a_render_does_not_return_before_the_thread_does(stub_render):
    """The await must outlive the thread, not the other way round.

    Returning first is what let a "cancelled" job leave a live ffmpeg behind, and
    it is also what released the render slot early — the semaphore is held by the
    `async with` around this await and nothing else.
    """
    entered, exited = stub_render

    task = await _start_render()
    await asyncio.to_thread(entered.wait, 10)
    assert entered.is_set(), "the render never started"

    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await asyncio.wait_for(task, timeout=25)

    assert exited.is_set(), "the coroutine unwound while the encode was still running"


async def test_the_render_slot_is_held_until_the_thread_has_gone(stub_render):
    """`max_concurrent_renders` is the guardrail that keeps one box usable.

    A slot released while the abandoned encode is still burning CPU is worse than
    no guardrail at all: it is a guardrail that reports it is holding.

    Driven through `RenderStage.run`, because that is the only thing that touches
    `_render_slots` — `compose_video` never does. An earlier version of this test
    took the semaphore itself and then asserted the count came back, which is true
    however production behaves: it was measuring its own `async with`. The slot has
    to be observed *from outside* the code that owns it, mid-render, which is what
    the second acquire below is for.
    """
    from engine.workflows.media import RenderStage, _render_slots

    entered, exited = stub_render
    slots = _render_slots()
    free = slots._value

    task = asyncio.create_task(RenderStage().run(_Ctx("j2")))
    await asyncio.to_thread(entered.wait, 10)

    # The stage is inside its `async with` now. This is the assertion the old
    # version could not make.
    assert slots._value == free - 1, "the stage did not take a slot while rendering"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=25)

    # The ordering is the whole finding: the thread must be gone *before* the slot
    # is free, or a second render starts while the first is still saturating the CPU.
    assert exited.is_set(), "the slot came back while the encode was still running"
    assert slots._value == free, "the render slot was not returned"


class _Ctx:
    """The four things `RenderStage.run` reads off a context, and nothing else.

    `materials` and `voiceover` come back shaped rather than as `[]`, because a test
    that drives the real `_render` (rather than monkeypatching it) gets as far as
    `materials.clips` and `voiceover.audio_key`. No clips and no audio is exactly the
    render these tests want — the stubbed `_render_sync` is what blocks — so the
    values are empty, but the attributes have to exist.
    """

    def __init__(self, job_id: str = "j4") -> None:
        self.job_id = job_id
        self.inputs: dict = {}

    def get(self, name: str):
        if name == "materials":
            return SimpleNamespace(clips=[])
        if name == "voiceover":
            # A key, not None: `store.local_path` joins it onto the storage root, so
            # None is a TypeError before the render is even reached. The file need
            # not exist — the stubbed `_render_sync` never opens it.
            return SimpleNamespace(audio_key="voice/absent.mp3", duration_s=0.0)
        return []

    async def progress(self, _message: str, _fraction: float | None = None) -> None:
        return None


async def test_an_aborted_render_is_a_cancel_not_a_failure(monkeypatch):
    """A failure is a lie the Queue tells about a job the operator stopped.

    It puts a red row and an error string in front of somebody who did exactly what
    they meant to do, and — worse — `_existing_publish` and the resume logic both
    branch on that status. `RenderAborted` is translated at the stage into the
    cancellation the rest of the system already understands, rather than being left
    to surface as a mystifying render error.
    """
    from engine.render import compose
    from engine.workflows.media import _ABORTS, RenderStage, _render_slots

    async def aborted(*_args, **_kwargs):
        raise compose.RenderAborted("render cancelled")

    monkeypatch.setattr(RenderStage, "_render", aborted)

    slots = _render_slots()
    free = slots._value

    with pytest.raises(asyncio.CancelledError):
        await RenderStage().run(_Ctx())

    assert slots._value == free, "the render slot was not returned on an abort"
    assert "j4" not in _ABORTS, "the abort switch outlived the render it belonged to"
