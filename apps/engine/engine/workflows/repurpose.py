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

    rights → acquire → segment → thesis → narration → draft → voiceover
           → assemble → subtitles → originality
           → grounding → titles → description → tags → chapters → thumbnail

`originality` sits where it does for two reasons at once. It reads the *assembled*
timeline, so it cannot run earlier — before `assemble` there is no finished video
to judge, only an intention. And everything after it costs money, so it cannot run
later without paying for packaging on a video that will never go out.

**`narration` is what makes the gate passable at all.** `gate.py` measures
authorship, and without commentary every source segment is bare source and
`authored_share` is zero by construction. A version of this workflow without that
stage — which is what existed first — could only ever produce refusals, and the
refusals were correct: the thing it produced was a reupload.

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
from engine.repurpose import assemble as assembly
from engine.repurpose import narrate as narration_writer
from engine.repurpose import segment as segmentation
from engine.repurpose.gate import Corpus, Timeline, TimelineSegment, evaluate
from engine.repurpose.rights import Grant
from engine.workflows import media, publish, seo
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
    #: Creator handle per source id. Carried from here because this is the stage
    #: that establishes *which clips, from whom* — and because the feedback loop
    #: needs it at publish time, long after the clip rows have been re-queried.
    handles: dict[str, str] = field(default_factory=dict)

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
            value=ClearedClips(
                source_ids=source_ids, grants=grants, handles=await _handles(source_ids)
            ),
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


class ThesisStage(Stage[str]):
    """What these clips are *about* — the claim the video argues.

    First creative stage, and the one that decides whether this is a video or a
    compilation. "Editing that tells a story" is the policy's own phrase for what
    makes reuse monetisable, and a story needs a claim.
    """

    name = "thesis"
    title = "Thesis"
    depends_on = ("segment",)
    estimated_cost_usd = 0.05
    editable = True
    editable_type = str

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        cleared: ClearedClips = ctx.get("rights")
        captions = await _captions(cleared.source_ids)

        thesis, completion = await narration_writer.write_thesis(
            topic=str(ctx.inputs.get("topic") or ""),
            captions=list(captions.values()),
        )
        if not thesis:
            raise WorkflowError("no thesis was produced; the clips may share nothing")

        return StageOutput(
            value=thesis,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class NarrationStage(Stage[narration_writer.Narration]):
    """The commentary, written to the cut timings.

    **This is the stage that makes the gate passable.** Without it every source
    segment is bare source and `authored_share` is zero by construction — which is
    the correct verdict on a video with nothing added, and the reason a repurpose
    pipeline missing this stage can only ever produce refusals.
    """

    name = "narration"
    title = "Commentary"
    depends_on = ("thesis", "segment")
    estimated_cost_usd = 0.08

    async def run(self, ctx: WorkflowContext) -> StageOutput[narration_writer.Narration]:
        cuts: Cuts = ctx.get("segment")
        cleared: ClearedClips = ctx.get("rights")

        result, completion = await narration_writer.write_commentary(
            thesis=ctx.get("thesis"),
            topic=str(ctx.inputs.get("topic") or ""),
            segments=cuts.segments,
            captions=await _captions(cleared.source_ids),
        )
        if not result.lines:
            raise WorkflowError(
                "no commentary was written, so every clip would be bare source. "
                "The video would be refused at the originality gate."
            )

        return StageOutput(
            value=result,
            cost_usd=completion.cost_usd,
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


class VoiceoverStage(Stage[dict]):
    """Speak the commentary.

    Its own stage rather than `media.VoiceoverStage` because that one reads
    `revision`/`draft` from the script chain, which does not exist here. The TTS
    call underneath is the same one.
    """

    name = "voiceover"
    title = "Voiceover"
    depends_on = ("narration",)
    timeout_s = 600.0
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[dict]:
        from engine.settings import get_settings
        from engine.storage import store
        from engine.workflows.media import _synthesize

        result: narration_writer.Narration = ctx.get("narration")
        settings = get_settings()
        voice = ctx.inputs.get("voice") or settings.tts_voice

        await ctx.progress("synthesising commentary")
        audio_path, cues = await _synthesize(
            result.full_text, voice, original_text=result.full_text
        )
        key = await store.put_file(audio_path, f"repurpose/voiceover-{ctx.job_id}.mp3")

        return StageOutput(
            value={
                "audio_key": key,
                "duration_s": cues[-1]["end"] if cues else 0.0,
                "cues": cues,
                "voice": voice,
            },
            artifacts={"audio": key},
            provenance=Provenance(params={"voice": voice, "cue_count": len(cues)}),
        )


class AssembleStage(Stage[assembly.Assembly]):
    """Cut it together into a file.

    The audio-bed replacement lives here and is not optional — TikTok's music
    licences cover TikTok, so a source bed on YouTube is unlicensed however solid
    the video rights are.
    """

    name = "assemble"
    title = "Assemble"
    depends_on = ("segment", "voiceover", "subtitles")
    #: Renders are long and the framework's default would abandon one mid-encode.
    timeout_s = None
    max_attempts = 1

    async def run(self, ctx: WorkflowContext) -> StageOutput[assembly.Assembly]:
        from engine.storage import store

        cuts: Cuts = ctx.get("segment")
        acquired: AcquiredClips = ctx.get("acquire")
        voiceover = ctx.get("voiceover")

        sources = {
            source_id: await store.local_path(asset["storage_key"])
            for source_id, asset in acquired.assets.items()
            if asset.get("storage_key")
        }

        result = await assembly.assemble(
            segments=cuts.segments,
            sources=sources,
            narration_path=await store.local_path(voiceover["audio_key"]),
            job_id=ctx.job_id,
            aspect=str(ctx.inputs.get("aspect") or "9:16"),
            hook=cuts.hook,
            bed_path=_bed_path(str(ctx.inputs.get("bgm_track") or "")),
            keep_source_audio=set(ctx.inputs.get("keep_source_audio") or []),
            cues=ctx.try_get("subtitles") or [],
            credits=await _credits(ctx.get("rights")),
            on_progress=lambda fraction, message: ctx.progress(message, fraction),
        )

        return StageOutput(
            value=result,
            artifacts={"video": result.output_key},
            provenance=Provenance(
                params={
                    "aspect": result.aspect,
                    "cuts": result.cuts,
                    "audio_bed_replaced": result.audio_bed_replaced,
                }
            ),
        )


class SubtitlesStage(Stage[list]):
    """Cue timings from the TTS boundaries, which come free with Edge."""

    name = "subtitles"
    title = "Subtitles"
    depends_on = ("voiceover",)
    timeout_s = 600.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        voiceover = ctx.get("voiceover")
        cues = voiceover.get("cues") or []
        method = "tts_boundaries"

        if not cues:
            from engine.render import compose
            from engine.storage import store

            await ctx.progress("transcribing (no TTS timings available)")
            cues = await compose.transcribe(await store.local_path(voiceover["audio_key"]))
            method = "whisper"

        return StageOutput(
            value=cues,
            provenance=Provenance(params={"method": method, "cue_count": len(cues)}),
        )


async def _captions(source_ids: list[str]) -> dict[str, str]:
    """Original captions, by source id.

    Untrusted. They are fenced in `narrate.py` at the point of interpolation
    rather than here, so the raw text stays available to anything that needs it
    verbatim and the fencing happens once, next to the prompt.
    """
    try:
        rows = await repository.clip_sources(channel_key="", status="selected", limit=200)
        rows += await repository.clip_sources(channel_key="", status="discovered", limit=200)
    except Exception as exc:  # noqa: BLE001 — captions are a nicety, not a dependency
        logger.warning("could not read clip captions: {}", exc)
        return {}
    return {r["id"]: r.get("caption", "") for r in rows if r["id"] in set(source_ids)}


async def _handles(source_ids: list[str]) -> dict[str, str]:
    """Creator handle per source id, best effort.

    Never fatal: a missing handle costs a credit line and one dimension in the
    feedback loop, and neither is worth failing a run over.
    """
    try:
        rows = await repository.clip_sources(channel_key="", status="selected", limit=200)
        rows += await repository.clip_sources(channel_key="", status="discovered", limit=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read creator handles: {}", exc)
        return {}
    wanted = set(source_ids)
    return {r["id"]: r.get("creator_handle", "") for r in rows if r["id"] in wanted}


async def _credits(cleared: ClearedClips) -> dict[str, str]:
    """Who to credit on screen, by source id.

    Only the lanes with a counterparty. Crediting your own footage is noise, and
    `gate.attribution` asks for credit on exactly the lanes `rights.py` marks as
    needing it — so the two cannot disagree about which clips those are.

    Falls back to the grantor when the handle is unknown, because a credit naming
    the campaign is still a credit and an empty one is a blank box on screen.
    """
    handles = cleared.handles
    out: dict[str, str] = {}
    for source_id, grant in cleared.grants.items():
        if not grant.needs_attribution:
            continue
        label = handles.get(source_id) or grant.grantor
        if label:
            out[source_id] = label if label.startswith("@") else f"@{label}"
    return out


def _bed_path(track: str = ""):
    """A licensed music bed, or None.

    None is a perfectly good answer and the safe default: commentary over silence
    is a legitimate edit, and an unlicensed bed is the exact problem this whole
    module exists to avoid. `services/bgm.py` owns what counts as available —
    `resolve("")` picks from the configured directory and returns None when there
    is nothing there.
    """
    try:
        from engine.services import bgm

        return bgm.resolve(track)
    except Exception as exc:  # noqa: BLE001
        logger.debug("no music bed available: {}", exc)
        return None


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
    depends_on = ("segment", "assemble")
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

    **Reads the finished file's own facts wherever they exist.** `assemble` reports
    what it actually produced — how many cuts, which segments landed where, whether
    the bed was replaced — and those are used in preference to anything in
    `ctx.inputs`. That preference is the whole point: for a while `audio_bed_replaced`
    and `cut_count` were booleans and integers the *caller* asserted, which made the
    gate's evidence a claim by the thing being judged. It could be passed by typing
    `"audio_bed_replaced": true` into a request.

    The input fallbacks survive only for the pre-assembly path — `POST
    /v1/repurpose/evaluate` scores a proposed edit before a file exists, and there
    the caller genuinely is describing an intention.

    Separate from the stage so the same construction is testable, and so the publish
    gate can rebuild it from a finished job's states without re-running anything.
    """
    segments: list[TimelineSegment] = []
    cursor = 0.0

    narration = ctx.try_get("narration")
    narrated_ids = set(getattr(narration, "narrated_source_ids", []) or [])
    assembled: assembly.Assembly | None = ctx.try_get("assemble")

    # The placements the render actually made, when there was one. They differ from
    # the cut list in two ways that matter: a teased hook adds a segment that is not
    # in `cuts`, and an unusable clip is dropped from it.
    placements = (
        [(p.source_id, p.duration_s) for p in assembled.placed]
        if assembled is not None
        else [
            (
                cut.get("source_id"),
                float(
                    cut.get("duration_s") or max(0.0, cut.get("end_s", 0) - cut.get("start_s", 0))
                ),
            )
            for cut in cuts.segments
        ]
    )

    for source_id, length in placements:
        if length <= 0:
            continue
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
        # Measured, then asserted, then counted — in that order of trust.
        cuts=(
            assembled.cuts
            if assembled is not None
            else int(ctx.inputs.get("cut_count") or len(segments))
        ),
        audio_bed_replaced=(
            assembled.audio_bed_replaced
            if assembled is not None
            else bool(ctx.inputs.get("audio_bed_replaced"))
        ),
        watermarked_sources=tuple(acquired.watermarked),
        # Measured too, for the same reason. `assemble` burns the credit in and
        # reports which clips got one; asserting it in the inputs would leave the
        # attribution hard block satisfiable by typing `true`, which is the hole
        # `audio_bed_replaced` used to have.
        attribution_on_screen=(
            bool(assembled.credited_source_ids)
            if assembled is not None
            else bool(ctx.inputs.get("attribution_on_screen"))
        ),
        # Still an input, and honestly so: the description is written by the SEO
        # stage *after* the gate runs, so at this point it is a commitment rather
        # than a fact. `DescriptionStage` is what has to keep it.
        attribution_in_description=bool(ctx.inputs.get("attribution_in_description")),
        is_compilation=len({s for s, _ in placements if s}) > 1,
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


# The SEO stages read `revision`/`draft` through `try_get`, so in this workflow
# they see None and write a title with no script behind it. `_Script` supplies one:
# the commentary *is* this video's script, it is just written to cuts rather than
# from scratch. Named "draft" because that is the name the SEO chain reads, and a
# second name for the same thing would mean editing four stages that are otherwise
# reused verbatim — the pattern `video.py` already uses for `_Titles`.
class _Script(Stage):
    name = "draft"
    title = "Script"
    depends_on = ("narration",)
    timeout_s = 30.0

    async def run(self, ctx: WorkflowContext) -> StageOutput:
        from engine.workflows.script import Script

        result: narration_writer.Narration = ctx.get("narration")
        lines = [line.text for line in result.lines]
        return StageOutput(
            value=Script(
                hook=lines[0] if lines else "",
                body=" ".join(lines[1:]),
                beats=[],
                sources=[],
            ),
            provenance=Provenance(params={"derived_from": "narration"}),
        )


class _Titles(seo.TitlesStage):
    depends_on = ("grounding", "draft")


class _Description(seo.DescriptionStage):
    depends_on = ("titles", "grounding", "draft")


class _Chapters(seo.ChaptersStage):
    depends_on = ("titles", "subtitles")


class _Thumbnail(media.ThumbnailStage):
    """Same stage, pointed at this workflow's script.

    Not optional trimming: `publish.ThumbnailSetStage` depends on "thumbnail", so a
    repurpose-publish workflow without it fails `Workflow._validate` at import —
    which is exactly the check that caught it.
    """

    depends_on = ("titles", "draft")


def repurpose_stages() -> list[Stage]:
    """The stages that produce a finished, unpublished repurposed video.

    A function rather than a module-level list because `Stage` instances carry
    per-run state, and the publish workflow needs its own.

    Order, and why:

        rights      refuse before a byte is fetched or a cent spent
        acquire     media, only for cleared clips
        segment     where to cut, and which moment opens
        thesis      what this is about — a compilation with no claim is the
                    failure the policy names
        narration   the commentary. Without it every clip is bare source
        voiceover   speak it
        assemble    cut it together; replace the audio bed
        subtitles   cues from the TTS boundaries
        originality THE GATE — after the file exists so it judges what was made,
                    before the SEO stages so a blocked video pays for no title
        grounding…  packaging, only ever reached by a video that passed
    """
    return [
        RightsStage(),
        AcquireStage(),
        SegmentStage(),
        ThesisStage(),
        NarrationStage(),
        _Script(),
        VoiceoverStage(),
        # Before `assemble`, because the captions are burnt into the picture and
        # the cues are what they are drawn from. It used to sit after, from when
        # captions were a separate later concern.
        SubtitlesStage(),
        AssembleStage(),
        OriginalityStage(),
        # Everything below is only reached by a video the gate passed.
        seo.GroundingStage(),
        _Titles(),
        _Description(),
        seo.TagsStage(),
        _Chapters(),
        _Thumbnail(),
    ]


REPURPOSE_WORKFLOW = Workflow("repurpose", repurpose_stages())


#: Publishing a repurposed video. Extends the workflow rather than standing alone,
#: for the reason `video.py` documents: `UploadStage.depends_on` names stages from
#: the producing workflow, and `Workflow._validate` requires every dependency to be
#: defined earlier in the *same* workflow.
#:
#: `UploadStage` expects a "render" stage. Here the finished file comes from
#: `assemble`, so `_Render` republishes it under the name the publish stages read —
#: the alternative is a second copy of four publish stages differing in one string.
class _Render(Stage[str]):
    name = "render"
    title = "Video"
    depends_on = ("assemble",)
    timeout_s = 30.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        result: assembly.Assembly = ctx.get("assemble")
        return StageOutput(
            value=result.output_key,
            artifacts={"video": result.output_key},
            provenance=Provenance(params={"from": "assemble"}),
        )


REPURPOSE_PUBLISH_WORKFLOW = Workflow(
    "repurpose-publish",
    [*repurpose_stages(), _Render(), *publish.publish_stages()],
)
