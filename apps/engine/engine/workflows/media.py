"""Production stages: voiceover, subtitles, materials, render, thumbnail.

This is the part derived from MoneyPrinterTurbo, restructured into stages. The two
places we deliberately diverge from upstream:

  * **Per-beat material matching.** Upstream matches one global keyword set to every
    clip, so a video covering three different things shows footage about the average
    of them. Here each beat searches on its own `visual_direction`.
  * **Clip pacing from beat energy.** Upstream cuts on a fixed interval. Here a
    high-energy beat cuts fast and a low-energy one holds, and cuts land on sentence
    boundaries taken from the subtitle cues.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
from loguru import logger

from engine.render import compose
from engine.services import stock
from engine.settings import get_settings
from engine.storage import store
from engine.workflows.base import Provenance, Stage, StageOutput, WorkflowContext

# Clip length by beat energy, in seconds. High energy cuts fast.
PACING = {"high": 2.2, "medium": 3.5, "low": 5.0}


@dataclass
class Voiceover:
    audio_key: str
    duration_s: float
    cues: list[dict] = field(default_factory=list)  # {start, end, text}
    voice: str = ""

    def summary(self) -> str:
        return f"{int(self.duration_s) // 60}:{int(self.duration_s) % 60:02d} · {self.voice}"


@dataclass
class Materials:
    clips: list[dict] = field(default_factory=list)  # {beat_index, url, key, duration}

    def summary(self) -> str:
        beats = len({c["beat_index"] for c in self.clips})
        return f"{len(self.clips)} clips across {beats} beats"


class VoiceoverStage(Stage[Voiceover]):
    name = "voiceover"
    title = "Voiceover"
    depends_on = ("revision",)
    timeout_s = 600.0
    estimated_cost_usd = 0.05

    async def run(self, ctx: WorkflowContext) -> StageOutput[Voiceover]:
        script = ctx.try_get("revision") or ctx.get("draft")
        settings = get_settings()
        voice = ctx.inputs.get("voice") or settings.tts_voice

        await ctx.progress("synthesising speech")
        audio_path, cues = await _synthesize(
            script.full_text, voice, original_text=script.full_text
        )

        key = await store.put_file(audio_path, f"voiceover/{ctx.job_id}.mp3")
        duration = cues[-1]["end"] if cues else 0.0

        return StageOutput(
            value=Voiceover(audio_key=key, duration_s=duration, cues=cues, voice=voice),
            artifacts={"audio": key},
            provenance=Provenance(params={"voice": voice, "provider": settings.tts_provider}),
        )


class SubtitlesStage(Stage[list]):
    """Cue timings.

    Edge TTS returns word boundaries, so the cues come free. Other backends do not,
    and those fall back to Whisper. Switching TTS provider therefore silently changes
    subtitle quality — any provider change needs a real render test, not a unit test.
    """

    name = "subtitles"
    title = "Subtitles"
    depends_on = ("voiceover",)
    timeout_s = 600.0

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        voiceover: Voiceover = ctx.get("voiceover")

        if voiceover.cues:
            cues = voiceover.cues
            method = "tts_boundaries"
        else:
            await ctx.progress("transcribing (no TTS timings available)")
            cues = await compose.transcribe(await store.local_path(voiceover.audio_key))
            method = "whisper"

        return StageOutput(
            value=cues,
            provenance=Provenance(params={"method": method, "cue_count": len(cues)}),
        )


class MaterialsStage(Stage[Materials]):
    name = "materials"
    title = "Materials"
    depends_on = ("beats", "voiceover")
    timeout_s = 900.0
    estimated_cost_usd = 0.0  # stock is free; generative B-roll is priced per beat

    async def run(self, ctx: WorkflowContext) -> StageOutput[Materials]:
        beats = ctx.get("beats")
        aspect = ctx.inputs.get("aspect", "9:16")
        settings = get_settings()

        if not (settings.pexels_api_key or settings.pixabay_api_key):
            raise RuntimeError(
                "no stock provider configured; set PEXELS_API_KEY or PIXABAY_API_KEY"
            )

        materials = Materials()
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30.0) as client:
            for index, beat in enumerate(beats):
                await ctx.progress(
                    f"sourcing beat {index + 1}/{len(beats)}", (index + 1) / len(beats)
                )
                needed = max(1, round(beat.est_seconds / PACING.get(beat.energy, 3.5)))
                clips = await stock.search(
                    beat.visual_direction,
                    aspect=aspect,
                    count=needed,
                    exclude=seen_ids,
                    client=client,
                )
                for clip in clips:
                    clip["beat_index"] = index
                    seen_ids.add(clip["id"])
                materials.clips.extend(clips)

        if not materials.clips:
            raise RuntimeError("no footage found for any beat")

        return StageOutput(
            value=materials,
            provenance=Provenance(
                params={
                    "aspect": aspect,
                    "unique_clips": len(seen_ids),
                    "pacing": PACING,
                    "providers": sorted({c["provider"] for c in materials.clips}),
                }
            ),
        )


@lru_cache(maxsize=1)
def _render_slots() -> asyncio.Semaphore:
    """Cap concurrent renders at STUDIO_MAX_CONCURRENT_RENDERS.

    The setting existed and was enforced by nothing, so N simultaneous jobs meant
    N simultaneous MoviePy encodes — each one CPU-saturating — and the box simply
    fell over. `CLAUDE.md` calls the guardrails load-bearing for exactly this.
    """
    return asyncio.Semaphore(get_settings().max_concurrent_renders)


#: The abort switch for each render currently in flight, by job id.
#:
#: A registry rather than something threaded through `WorkflowContext` because the
#: only writer is `main.cancel_job`, which has a job id and nothing else — the
#: context belongs to the run it is cancelling and is not reachable from outside it.
#: Process-local by nature: a render in the arq worker is not cancellable from the
#: API process at all, which is what `cancel_job` already says out loud.
_ABORTS: dict[str, threading.Event] = {}


def abort_render(job_id: str) -> bool:
    """Ask this job's render thread to stop. True if there was one to ask.

    Cancelling the coroutine does not touch the thread it is waiting on, and that
    thread is inside ffmpeg for most of a render's life. Without this, Cancel
    stopped the *reporting* and left the encode running to completion — still
    burning a CPU, still holding a slot in `_render_slots`, so
    `STUDIO_MAX_CONCURRENT_RENDERS` no longer bounded live encodes.
    """
    event = _ABORTS.get(job_id)
    if event is None:
        return False
    event.set()
    return True


class RenderStage(Stage[str]):
    name = "render"
    title = "Render"
    depends_on = ("materials", "voiceover", "subtitles")
    timeout_s = None  # long-form renders take minutes; a timeout here is a foot-gun
    max_attempts = 1  # a failed render is deterministic, retrying wastes minutes

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        materials: Materials = ctx.get("materials")
        voiceover: Voiceover = ctx.get("voiceover")
        cues = ctx.get("subtitles")
        beats = ctx.get("beats")

        async def on_progress(fraction: float, message: str) -> None:
            await ctx.progress(message, fraction)

        slots = _render_slots()
        if slots.locked():
            # Say so rather than showing a stage that sits at 0% for ten minutes.
            await ctx.progress("waiting for a render slot")

        abort = threading.Event()
        _ABORTS[ctx.job_id] = abort
        try:
            async with slots:
                return await self._render(
                    ctx, materials, voiceover, cues, beats, on_progress, abort
                )
        except compose.RenderAborted as exc:
            # A cancel, not a failure. Translated to the outcome the rest of the
            # system already understands: `Workflow.run` lets CancelledError through
            # untouched, so the job ends as cancelled rather than as a failed render
            # with a mystifying error string.
            logger.info("render for job {} stopped on request", ctx.job_id)
            raise asyncio.CancelledError(str(exc)) from exc
        finally:
            # After the `async with`, so the slot is released only once the thread
            # has actually exited — `compose_video` does not return until it has.
            _ABORTS.pop(ctx.job_id, None)

    async def _render(self, ctx, materials, voiceover, cues, beats, on_progress, abort=None):
        output_path = await compose.compose_video(
            clips=materials.clips,
            beats=beats,
            audio_path=await store.local_path(voiceover.audio_key),
            cues=cues,
            aspect=ctx.inputs.get("aspect", "9:16"),
            job_id=ctx.job_id,
            on_progress=on_progress,
            abort=abort,
        )
        key = await store.put_file(output_path, f"renders/{ctx.job_id}.mp4")

        return StageOutput(
            value=key,
            artifacts={"video": key},
            provenance=Provenance(
                params={"duration_s": voiceover.duration_s, "cue_count": len(cues)}
            ),
        )


class ThumbnailStage(Stage[list]):
    """Three concepts, generated image, text composed by us in code.

    Text is never baked into the generated image: generated typography is unreliable,
    and keeping the overlay separate is what makes A/B variant swapping possible in
    Phase 8.
    """

    name = "thumbnail"
    title = "Thumbnail"
    depends_on = ("titles", "revision")
    optional = True
    timeout_s = 300.0

    @property
    def estimated_cost_usd(self) -> float:  # type: ignore[override]
        """The concept call plus three backgrounds.

        Computed rather than fixed because the image half swings between $0 and
        $0.57 depending on which provider is configured, and `Workflow.run` refuses
        a stage whose estimate would breach the budget — a flat 0.25 would either
        block a run that costs nothing or wave through one that costs twice that.
        """
        from engine.providers import images

        spec = images.selected()
        return 0.06 + (3 * spec.cost_per_image if spec else 0.0)

    def _brief(self, titles, script) -> str:
        """What the video actually is.

        The concept call used to see one title and one hook — enough to write a
        plausible thumbnail for a video it had not been told anything about. The
        beats carry the visual direction and the energy curve, which is precisely
        what decides whether an archetype fits: a build has a before/after, an
        explainer has a reveal, and nothing in a title says which.
        """
        lines = [f"Title: {titles[0].text}", f"Hook: {script.hook}"]

        alternatives = [t.text for t in titles[1:3]]
        if alternatives:
            # Where the titles disagree is where the promise is still unsettled, and
            # the thumbnail is the other half of that promise.
            lines.append("Other titles under consideration: " + " / ".join(alternatives))

        beats = getattr(script, "beats", None) or []
        if beats:
            lines.append("\nBeats, in order:")
            for i, beat in enumerate(beats[:10], 1):
                bits = [getattr(beat, "purpose", "") or ""]
                visual = getattr(beat, "visual_direction", "")
                if visual:
                    bits.append(f"visual: {visual}")
                energy = getattr(beat, "energy", "")
                if energy:
                    bits.append(f"energy: {energy}")
                lines.append(f"  {i}. " + " — ".join(b for b in bits if b))

        sources = getattr(script, "sources", None) or []
        if sources:
            lines.append(f"\nGrounded in {len(sources)} source(s) — this is not a listicle.")

        return "\n".join(lines)

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        from engine.providers import llm
        from engine.render import templates

        titles = ctx.get("titles")
        script = ctx.try_get("revision") or ctx.get("draft")
        model = llm.for_task("thumbnail")

        concepts, completion = await model.json(
            f"""{self._brief(titles, script)}

Design 3 thumbnail concepts for this video. The thumbnail is judged at 168 pixels
wide on a phone, in a feed, against everything else competing for that tap —
anything that only reads at full size has already failed.

Rules that are not negotiable:
- One idea per thumbnail. Not two. A viewer gives it a fifth of a second.
- The overlay text does NOT repeat the title. The title and thumbnail are two halves
  of one promise; repeating wastes half of it.
- If the title asks a question, the thumbnail shows the stakes, never the answer.
- The image prompt describes the image ONLY. Never ask for text, words, numbers,
  letters or logos in the image — we compose type separately so variants stay
  swappable, and generated typography is unreliable anyway.
- Prefer one dominant subject, saturated colour and hard light. Tasteful loses.

Pick a different template for each of the 3 concepts:

{templates.catalogue_for_prompt()}

Accent colours available: {", ".join(templates.ACCENTS)}. Pick one per concept that
will contrast with its own image, not one that blends into it.

Return: {{"concepts": [{{"template": str, "image_prompt": str, "overlay_text": str,
                        "accent": str, "focal_point": str, "rationale": str}}]}}""",
            max_tokens=2000,
        )

        chosen = concepts["concepts"][:3]
        # Enforced here rather than hoped for in the prompt: asked for variety, models
        # still return the same archetype three times often enough to matter, and three
        # variants in one layout is the same thumbnail three times.
        spread = templates.distinct([c.get("template") for c in chosen])

        variants = []
        image_cost = 0.0
        for i, (concept, template_key) in enumerate(zip(chosen, spread, strict=False)):
            await ctx.progress(f"rendering concept {i + 1}/{len(chosen)}", (i + 1) / len(chosen))
            concept = {**concept, "template": template_key}
            thumb = await compose.make_thumbnail(concept, job_id=ctx.job_id, index=i)
            image_cost += thumb.cost_usd
            variants.append(
                {
                    **concept,
                    "key": thumb.key,
                    "template": thumb.template,
                    "image_model": thumb.image_model,
                }
            )

        # Two models produce a thumbnail — one designs the concept, one paints the
        # background — so recording only the first would attribute a winning
        # thumbnail to the wrong half of the work in Phase 8.
        image_model = next((v["image_model"] for v in variants if v["image_model"]), "")
        return StageOutput(
            value=variants,
            cost_usd=completion.cost_usd + image_cost,
            artifacts={f"thumbnail_{i}": v["key"] for i, v in enumerate(variants)},
            provenance=Provenance(
                model=completion.model,
                prompt=completion.prompt,
                params={"image_model": image_model} if image_model else {},
            ),
        )


# ── providers ───────────────────────────────────────────────────────────────


async def _synthesize(
    text: str, voice: str, *, original_text: str | None = None
) -> tuple[Path, list[dict]]:
    """Edge TTS with boundary capture.

    Boundary events are what give us subtitle cues without a transcription pass.

    edge-tts 7 defaults `boundary` to "SentenceBoundary", which yields one cue per
    sentence — a wall of text on screen. We ask for word boundaries explicitly, and
    still handle sentence boundaries, because not every voice honours the request.

    ``original_text`` is the pre-synthesis source before edge-tts strips punctuation
    from WordBoundary events.  Passing it enables _restore_punctuation(), which makes
    the sentence-break logic in _group_cues() actually fire.
    """
    import edge_tts

    out = Path(get_settings().storage_root) / "tmp"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{hashlib.sha1(text.encode()).hexdigest()[:16]}.mp3"

    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    words: list[dict] = []
    sentences: list[dict] = []

    with path.open("wb") as fh:
        async for chunk in communicate.stream():
            kind = chunk["type"]
            if kind == "audio":
                fh.write(chunk["data"])
            elif kind in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 10_000_000
                cue = {
                    "start": start,
                    "end": start + chunk["duration"] / 10_000_000,
                    "text": chunk["text"],
                }
                (words if kind == "WordBoundary" else sentences).append(cue)

    if words:
        src = original_text or text
        return path, _group_cues(_restore_punctuation(words, src))
    if sentences:
        logger.info("voice {} returned sentence boundaries only; splitting", voice)
        return path, _split_sentence_cues(sentences)
    # No timings at all — SubtitlesStage falls back to Whisper.
    logger.warning("voice {} returned no boundary events", voice)
    return path, []


_TRAILING_SENT = re.compile(r"([.!?]+)$")
_LEADING_STRIP = re.compile(r"^[^\w']+")  # strip leading punct; keep apostrophe


def _restore_punctuation(word_cues: list[dict], original_text: str) -> list[dict]:
    """Re-attach sentence-ending punctuation that edge-tts strips from WordBoundary events.

    edge-tts produces WordBoundary events with the bare word — "bridge." becomes
    "bridge" — so the sentence-break check in _group_cues() tests for terminal
    punctuation that is never there.

    We walk the original text sequentially, with a small forward look-ahead to
    survive minor alignment drift (contractions, hyphenated compounds), and copy
    any trailing [.!?]+ back onto the matching cue.  The cue list is returned as
    a new list; input cues are not mutated.
    """
    orig_tokens: list[tuple[str, str]] = []
    for raw in re.split(r"\s+", original_text.strip()):
        if not raw:
            continue
        m = _TRAILING_SENT.search(raw)
        punct = m.group(1) if m else ""
        word = _TRAILING_SENT.sub("", raw)
        word = _LEADING_STRIP.sub("", word)
        if word:
            orig_tokens.append((word.lower(), punct))

    out: list[dict] = []
    orig_idx = 0

    for cue in word_cues:
        cue_lower = cue["text"].lower().strip()
        matched_punct = ""

        # Scan a small window to stay aligned despite minor drift.
        window = min(orig_idx + 6, len(orig_tokens))
        for i in range(orig_idx, window):
            orig_word, orig_punct = orig_tokens[i]
            # Match: exact, or one is a prefix of the other (handles truncated cues).
            if (
                orig_word == cue_lower
                or orig_word.startswith(cue_lower)
                or cue_lower.startswith(orig_word)
            ):
                matched_punct = orig_punct
                orig_idx = i + 1
                break
        else:
            orig_idx = min(orig_idx + 1, len(orig_tokens))

        out.append({**cue, "text": cue["text"] + matched_punct} if matched_punct else cue)

    return out


def _group_cues(word_cues: list[dict], max_chars: int = 42) -> list[dict]:
    """Group word boundaries into readable subtitle lines.

    Breaks on sentence endings first, then on the character budget — a line that
    splits mid-clause reads badly at any font size.
    """
    grouped: list[dict] = []
    buffer: list[dict] = []

    def flush() -> None:
        if not buffer:
            return
        grouped.append(
            {
                "start": buffer[0]["start"],
                "end": buffer[-1]["end"],
                "text": " ".join(w["text"] for w in buffer),
            }
        )
        buffer.clear()

    for word in word_cues:
        buffer.append(word)
        line = " ".join(w["text"] for w in buffer)
        if word["text"].rstrip().endswith((".", "!", "?")) or len(line) >= max_chars:
            flush()
    flush()
    return grouped


def _split_sentence_cues(sentences: list[dict], max_chars: int = 42) -> list[dict]:
    """Split sentence-level cues into readable lines.

    Timing within a sentence is apportioned by character count. That is an
    approximation — speech rate is not uniform — but a whole sentence held on screen
    at once is worse, and it stays within the sentence's true bounds so drift cannot
    accumulate across the video.
    """
    out: list[dict] = []
    for sentence in sentences:
        text = sentence["text"].strip()
        span = max(sentence["end"] - sentence["start"], 0.1)
        if len(text) <= max_chars:
            out.append({**sentence, "text": text})
            continue

        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) > max_chars and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)

        total = sum(len(line) for line in lines) or 1
        cursor = sentence["start"]
        for line in lines:
            duration = span * (len(line) / total)
            out.append({"start": cursor, "end": cursor + duration, "text": line})
            cursor += duration
    return out


# Stock search and download moved to engine.services.stock when Pixabay was added
# as a fallback provider. Re-exported so `from engine.workflows.media import
# download_all` keeps working.
download_all = stock.download_all
