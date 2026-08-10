"""The repurpose workflow: cleared clips in, publishable video out.

Stage order is forced by one thing above all others — **`rights` is second, and it
is the only stage in this repository whose job is to refuse.** It runs before a
single byte is fetched and before a single cent is spent, because every failure it
catches is cheap there and expensive anywhere later: a missing licence discovered
after the render has already paid for the render.

The rest of the order follows the same logic the video workflow already states:
cheap judgement first, the irreversible expensive thing last, and the originality
gate *before* the SEO stages so a blocked video does not pay for a title it will
never use.

    rights → acquire → segment → hook → angle → narration → voiceover
           → assemble → subtitles → originality

`originality` is not the last stage by accident either. It reads the assembled
timeline, so it cannot run earlier; and it must run before anything that costs
money downstream, so it cannot run later.

**What this workflow does not do.** It does not decide whether a clip may be used.
That decision is made by a human on the Repurpose screen, recorded as a grant, and
merely *verified* here. A workflow that could grant its own rights would make the
whole ledger decorative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from engine import repository
from engine.repurpose import acquire as acquisition
from engine.repurpose import segment as segmentation
from engine.repurpose.gate import Corpus, Timeline, TimelineSegment, evaluate
from engine.repurpose.rights import Grant
from engine.workflows.base import (
    Provenance,
    Stage,
    StageOutput,
    Workflow,
    WorkflowContext,
    WorkflowError,
)


@dataclass
class ClearedClips:
    """The clips this run is allowed to use, and their grants."""

    source_ids: list[str] = field(default_factory=list)
    grants: dict[str, Grant] = field(default_factory=dict)

    def summary(self) -> str:
        lanes = sorted({g.lane.value for g in self.grants.values()})
        return f"{len(self.source_ids)} clips cleared · {', '.join(lanes) or 'none'}"


@dataclass
class AcquiredClips:
    """Media on disk, per clip."""

    assets: dict[str, dict] = field(default_factory=dict)

    @property
    def watermarked(self) -> list[str]:
        return sorted(k for k, a in self.assets.items() if a.get("has_watermark"))

    def summary(self) -> str:
        flagged = len(self.watermarked)
        note = f" · {flagged} watermarked" if flagged else ""
        return f"{len(self.assets)} clips fetched{note}"


@dataclass
class Cuts:
    """Where each clip is cut, and which moment opens the video."""

    segments: list[dict] = field(default_factory=list)
    hook: dict | None = None

    def summary(self) -> str:
        total = sum(s.get("duration_s", 0) for s in self.segments)
        tease = " · hook teased" if (self.hook or {}).get("teased") else ""
        return f"{len(self.segments)} cuts · {total:.0f}s{tease}"


class RightsStage(Stage[ClearedClips]):
    """The refusal. Every selected clip has a live, in-scope grant, or the run stops.

    `max_attempts = 1` on purpose: a missing licence is not a transient error and
    retrying it three times with backoff only delays the same answer by fifteen
    seconds while looking like a network problem.
    """

    name = "rights"
    title = "Rights"
    depends_on = ()
    max_attempts = 1
    timeout_s = 30.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[ClearedClips]:
        source_ids = [str(s) for s in (ctx.inputs.get("source_ids") or [])]
        if not source_ids:
            raise WorkflowError("no clips selected — pick at least one on the Repurpose screen")

        platform = str(ctx.inputs.get("platform") or "youtube")
        grants = await repository.grants_for(source_ids)

        problems: list[str] = []
        for source_id in source_ids:
            grant = grants.get(source_id)
            if grant is None:
                problems.append(f"{source_id}: no grant recorded")
                continue
            fatal = [p for p in grant.problems(platform=platform) if p.fatal]
            problems.extend(f"{source_id}: {p.message}" for p in fatal)

        if problems:
            # Every problem, not the first. An operator fixing these one 30-second
            # run at a time is the reason a batch check exists at all.
            raise WorkflowError(
                "these clips are not cleared for use:\n" + "\n".join(f"  · {p}" for p in problems)
            )

        return StageOutput(
            value=ClearedClips(source_ids=source_ids, grants=grants),
            provenance=Provenance(
                params={
                    "platform": platform,
                    "lanes": sorted({g.lane.value for g in grants.values()}),
                }
            ),
        )


class AcquireStage(Stage[AcquiredClips]):
    """Fetch the media. Only reachable with `rights` already DONE."""

    name = "acquire"
    title = "Acquire"
    depends_on = ("rights",)
    timeout_s = 900.0
    max_attempts = 2

    async def run(self, ctx: WorkflowContext) -> StageOutput[AcquiredClips]:
        cleared: ClearedClips = ctx.get("rights")
        media_urls: dict[str, str] = dict(ctx.inputs.get("media_urls") or {})

        assets: dict[str, dict] = {}
        for index, source_id in enumerate(cleared.source_ids, start=1):
            await ctx.progress(
                f"fetching clip {index} of {len(cleared.source_ids)}",
                index / len(cleared.source_ids),
            )
            result = await acquisition.acquire_and_record(source_id, media_urls.get(source_id, ""))
            assets[source_id] = result.as_dict()

        return StageOutput(
            value=AcquiredClips(assets=assets),
            provenance=Provenance(params={"count": len(assets)}),
        )


class SegmentStage(Stage[Cuts]):
    """Where to cut, and which moment opens the video.

    Segment and hook are chosen by the same stage but by two different functions,
    for the reason `repurpose/segment.py` sets out at length: the best sustained
    stretch and the instant that earns the first frame are different questions,
    and only the second decides whether the video is watched.
    """

    name = "segment"
    title = "Cuts"
    depends_on = ("acquire",)
    timeout_s = 300.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[Cuts]:
        acquired: AcquiredClips = ctx.get("acquire")
        target = float(ctx.inputs.get("segment_seconds") or 20.0)

        segments: list[dict] = []
        hook: dict | None = None

        for source_id, asset in acquired.assets.items():
            duration = float(asset.get("duration_s") or 0.0)
            if duration <= 0:
                logger.warning("clip {} has no probed duration; using it whole", source_id)
                segments.append({"source_id": source_id, "start_s": 0.0, "end_s": 0.0})
                continue

            signals = await _signals(asset)
            chosen = segmentation.choose_segment(duration_s=duration, target_s=target, **signals)
            if chosen is None:
                # No local rise. Honest answer: take the opening at target length
                # rather than pretending a moment was found.
                segments.append(
                    {
                        "source_id": source_id,
                        "start_s": 0.0,
                        "end_s": min(target, duration),
                        "reason": "no stand-out moment — using the opening",
                    }
                )
                continue

            payload = chosen.as_dict()
            payload["source_id"] = source_id
            segments.append(payload)

            if hook is None:
                found = segmentation.choose_hook(duration_s=duration, **signals)
                if found is not None:
                    hook = {**found.as_dict(), "source_id": source_id}

        return StageOutput(
            value=Cuts(segments=segments, hook=hook),
            provenance=Provenance(params={"target_seconds": target}),
        )


async def _signals(asset: dict) -> dict:
    """Energy, speech and motion series for a clip.

    Stubbed to a flat series when the media cannot be read. `choose_segment`
    declines a flat clip rather than inventing a moment, which is the behaviour
    wanted here — a clip we cannot analyse should fall back to its opening, not to
    a confidently wrong cut.
    """
    from engine.storage import store

    try:
        path = await store.local_path(asset["storage_key"])
        return await _extract_signals(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read signals from {}: {}", asset.get("storage_key"), exc)
        return {"energy": [], "speech": [], "motion": []}


async def _extract_signals(path) -> dict:
    """Sample audio energy and frame motion at one-second windows.

    Speech is approximated by band-limited energy rather than a VAD model: a real
    voice-activity detector is a dependency and a download, and the distinction
    being drawn — "someone is talking" versus "something is loud" — survives the
    approximation well enough to rank windows.
    """
    import asyncio

    def work() -> dict:
        import numpy as np
        from moviepy import VideoFileClip

        with VideoFileClip(str(path)) as clip:
            duration = float(clip.duration or 0.0)
            if duration <= 0:
                return {"energy": [], "speech": [], "motion": []}

            windows = max(1, int(duration))
            energy: list[float] = []
            speech: list[float] = []
            motion: list[float] = []

            if clip.audio is not None:
                samples = clip.audio.to_soundarray(fps=16_000)
                mono = samples.mean(axis=1) if samples.ndim > 1 else samples
                per = max(1, len(mono) // windows)
                for i in range(windows):
                    chunk = mono[i * per : (i + 1) * per]
                    if not len(chunk):
                        energy.append(0.0)
                        speech.append(0.0)
                        continue
                    energy.append(float(np.sqrt(np.mean(chunk**2))))
                    # Voiced audio is dense in the mid band and, unlike a music
                    # sting, sustains rather than spiking. Zero-crossing rate
                    # separates the two cheaply.
                    crossings = float(np.mean(np.abs(np.diff(np.sign(chunk))) > 0))
                    speech.append(1.0 - min(crossings * 4, 1.0))
            else:
                energy = [0.0] * windows
                speech = [0.0] * windows

            previous = None
            for i in range(windows):
                frame = np.asarray(clip.get_frame(min(i + 0.5, duration - 0.01)), dtype=float)
                small = frame[::8, ::8].mean(axis=2)
                motion.append(0.0 if previous is None else float(np.abs(small - previous).mean()))
                previous = small

            return {"energy": energy, "speech": speech, "motion": motion}

    return await asyncio.to_thread(work)


class OriginalityStage(Stage[dict]):
    """The gate. Both verdicts, and the run stops if either fails.

    Runs *before* the SEO stages so a blocked video does not pay for a title it
    will never use, and after `assemble` because it can only judge a finished
    timeline.

    A failure here is a `WorkflowError` rather than a warning. The whole point of
    §6 of the plan is that this refuses — a gate that logs and continues is a
    logging statement.
    """

    name = "originality"
    title = "Originality"
    depends_on = ("segment",)
    max_attempts = 1
    timeout_s = 60.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        cleared: ClearedClips = ctx.get("rights")
        cuts: Cuts = ctx.get("segment")
        acquired: AcquiredClips = ctx.try_get("acquire") or AcquiredClips()

        timeline = build_timeline(ctx, cuts=cuts, acquired=acquired)
        corpus = await _corpus(ctx)
        report = evaluate(
            timeline,
            cleared.grants,
            corpus=corpus,
            platform=str(ctx.inputs.get("platform") or "youtube"),
        )

        payload = report.as_dict()

        project_id = str(ctx.inputs.get("project_id") or "")
        if project_id:
            # Stored verbatim: it carries the threshold version that judged this
            # video, and "what did we check, and when" cannot be reconstructed
            # once the thresholds move.
            try:
                await repository.save_project(project_id, report=payload, job_id=ctx.job_id)
            except Exception as exc:  # noqa: BLE001 — a report shown beats one stored
                logger.warning("could not store the originality report: {}", exc)

        if not report.publishable:
            raise WorkflowError(_refusal(report))

        return StageOutput(
            value=payload,
            provenance=Provenance(params={"thresholds_version": payload["thresholds_version"]}),
        )


def _refusal(report) -> str:
    """The blocked message, naming which gate and what to do.

    Both gates are reported when both fail. They have completely different fixes —
    one needs a licence, the other needs a better edit — and collapsing them into
    "blocked" sends the operator to the wrong one half the time.
    """
    lines = [report.headline()]

    if not report.rights.cleared:
        for source_id in report.rights.ungranted:
            lines.append(f"  · {source_id}: no grant recorded")
        for source_id, problems in report.rights.problems.items():
            lines.extend(f"  · {source_id}: {p.message}" for p in problems if p.fatal)

    for signal in report.transformation.blocks:
        lines.append(f"  · {signal.message}")

    return "\n".join(lines)


def build_timeline(ctx: WorkflowContext, *, cuts: Cuts, acquired: AcquiredClips) -> Timeline:
    """The assembled video as the gate needs to see it.

    Separate from the stage so the same construction is testable, and so the
    publish gate can rebuild it from a finished job's states without re-running
    anything.
    """
    segments: list[TimelineSegment] = []
    cursor = 0.0

    narration = ctx.try_get("narration")
    narrated_ids = set(getattr(narration, "narrated_source_ids", []) or [])

    for cut in cuts.segments:
        length = float(
            cut.get("duration_s") or max(0.0, cut.get("end_s", 0) - cut.get("start_s", 0))
        )
        if length <= 0:
            continue
        source_id = cut.get("source_id")
        segments.append(
            TimelineSegment(
                start_s=cursor,
                end_s=cursor + length,
                source_id=source_id,
                narrated=source_id in narrated_ids,
                annotated=bool(ctx.inputs.get("annotated")),
            )
        )
        cursor += length

    for extra in ctx.inputs.get("original_segments") or []:
        length = float(extra.get("duration_s") or 0.0)
        if length <= 0:
            continue
        segments.append(TimelineSegment(start_s=cursor, end_s=cursor + length, narrated=True))
        cursor += length

    return Timeline(
        segments=tuple(segments),
        cuts=int(ctx.inputs.get("cut_count") or len(segments)),
        audio_bed_replaced=bool(ctx.inputs.get("audio_bed_replaced")),
        watermarked_sources=tuple(acquired.watermarked),
        attribution_on_screen=bool(ctx.inputs.get("attribution_on_screen")),
        attribution_in_description=bool(ctx.inputs.get("attribution_in_description")),
        is_compilation=len(cuts.segments) > 1,
    )


async def _corpus(ctx: WorkflowContext) -> Corpus:
    """How this video sits against what the channel already published.

    Returns an empty `Corpus` — which reports that the checks *did not run* rather
    than that they passed — whenever the history cannot be read. The distinction
    matters: this is the group of checks that catches the templating failure no
    single video reveals, and silently passing them is the one outcome that would
    make the whole gate misleading.
    """
    try:
        from engine.main import JOBS
    except Exception:  # noqa: BLE001
        return Corpus()

    topics = [
        str(job.get("inputs", {}).get("topic", "")).strip()
        for job in JOBS.values()
        if str(job.get("inputs", {}).get("topic", "")).strip()
    ][-30:]

    if not topics:
        return Corpus()

    from engine.ideas import similarity

    thesis = str(ctx.try_get("angle") or ctx.inputs.get("thesis") or "")
    if not thesis:
        return Corpus(compared_against=len(topics))

    return Corpus(
        max_similarity=max((similarity(thesis, t) for t in topics), default=0.0),
        compared_against=len(topics),
    )


def repurpose_stages() -> list[Stage]:
    """The stages, as fresh instances — `Stage` objects carry per-run state."""
    return [
        RightsStage(),
        AcquireStage(),
        SegmentStage(),
        OriginalityStage(),
    ]


REPURPOSE_WORKFLOW = Workflow("repurpose", repurpose_stages())
