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
import os
import re
import ssl
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
from loguru import logger

from engine.providers import images
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
        generated_cost = 0.0
        generated: list[dict] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for index, beat in enumerate(beats):
                await ctx.progress(
                    f"sourcing beat {index + 1}/{len(beats)}", (index + 1) / len(beats)
                )
                needed = max(1, round(beat.est_seconds / PACING.get(beat.energy, 3.5)))
                clips = await stock.search(
                    # The short query, not the cinematic one. `visual_direction` is
                    # written as a prompt for an image model and is a hopeless
                    # search string; `stock_query` is the two-to-four plain words
                    # for the same beat. Falls back to the old behaviour for beats
                    # generated before the field existed.
                    beat.stock_query or beat.visual_direction,
                    aspect=aspect,
                    count=needed,
                    exclude=seen_ids,
                    client=client,
                    # Deliberately no topic-wide fallback any more. Widening the
                    # query until *something* came back is what filled a beat about
                    # a subscriber counter with coloured paper clips: the search
                    # succeeded, the footage was unrelated, and nothing downstream
                    # could tell. Generation below is the better answer.
                )
                for clip in clips:
                    clip["beat_index"] = index
                    seen_ids.add(clip["id"])
                materials.clips.extend(clips)

                if clips:
                    continue

                # Nothing in the libraries matches this beat. That is the normal
                # case for anything about a named person, product or event — no
                # stock library has footage of a specific creator — and it is
                # exactly where CLAUDE.md says to generate instead of shipping
                # stock-only. `visual_direction` is already an image prompt.
                clip = await _generate_broll(beat, index, aspect)
                if clip:
                    materials.clips.append(clip)
                    seen_ids.add(clip["id"])
                    generated_cost += clip.pop("_cost_usd", 0.0)
                    # The model and the prompt, not just a count. These clips are
                    # generated artifacts and CLAUDE.md #2 wants both recorded —
                    # a line in the log is not provenance, because nothing reads
                    # the log when attributing a video's performance later.
                    generated.append(
                        {
                            "beat": index,
                            "model": clip.pop("_model", ""),
                            "prompt": clip.get("query", ""),
                        }
                    )

        if not materials.clips:
            raise RuntimeError("no footage found for any beat")

        return StageOutput(
            value=materials,
            # Metered, per CLAUDE.md #5. Stock is free and generation is not, so a
            # video whose beats all had to be generated costs real money here and
            # the per-video ceiling has to see it.
            cost_usd=round(generated_cost, 4),
            provenance=Provenance(
                params={
                    "aspect": aspect,
                    "unique_clips": len(seen_ids),
                    "pacing": PACING,
                    "providers": sorted({c["provider"] for c in materials.clips}),
                    "generated": generated,
                }
            ),
        )


async def _generate_broll(beat, index: int, aspect: str) -> dict | None:
    """An image-model shot for a beat the stock libraries cannot serve.

    Returns a clip in the same shape the stock providers return, with
    `kind="image"` so the compositor holds it for the beat's span rather than
    trying to open it as a video. `None` when generation is not configured, which
    is a supported state — the render then holds the previous shot, which is what
    it did for every unmatched beat before this existed.

    Never raises. A beat without footage is a small loss; a render that dies at
    stage eleven because an image API was busy is a large one.
    """
    prompt = beat.visual_direction or beat.stock_query
    if not prompt:
        return None

    try:
        image = await images.generate(
            # The negative is load-bearing and needs to be this emphatic: the
            # renderer burns captions over this shot, and image models still
            # render signage under a plain "no text" — an early attempt at a
            # subscriber-counter beat came back with a garbled "TICKER — TICKER"
            # label across it. Digits on a prop the prompt asked for are fine;
            # words are not.
            f"{prompt}. Cinematic, photographic, richly lit. "
            "No text, no words, no lettering, no signage, no labels, "
            "no captions, no watermark, no logo.",
            aspect=aspect,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("could not generate b-roll for beat {}: {}", index + 1, exc)
        return None

    if image is None:
        logger.info("beat {} has no stock match and image generation is off", index + 1)
        return None

    key = await store.put_bytes(image.data, f"broll/{index}-{abs(hash(prompt)) % 10**8}.png")
    logger.info("beat {} generated its own b-roll ({})", index + 1, image.model)
    return {
        "id": f"generated-{index}",
        "provider": "generated",
        "kind": "image",
        "path": str(await store.local_path(key)),
        "url": "",
        "duration": beat.est_seconds,
        "query": prompt,
        "beat_index": index,
        # Underscored keys are stripped by the caller once folded into the
        # stage's cost and provenance; they are not part of the clip contract.
        "_cost_usd": image.cost_usd,
        "_model": image.model,
    }


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
            # Keyed to match this stage's own name, which is what every other
            # reference to the artifact already uses: the publish stage's
            # dependency list and context read, the readiness check, and the job
            # summary in main.py. This one alone said "video", so `render_key`
            # came back null on every completed job and the Library could never
            # link to a finished video — the file was there, served, and
            # seekable, with nothing pointing at it.
            artifacts={"render": key},
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
        learned = ctx.inputs.get("insight_guidance", {}).get("thumbnail", "")

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
{learned}
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

    _trust_extra_cas()
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary", proxy=_tts_proxy())
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
        return path, _group_cues(_restore_written_forms(words, src))
    if sentences:
        logger.info("voice {} returned sentence boundaries only; splitting", voice)
        return path, _split_sentence_cues(sentences)
    # No timings at all — SubtitlesStage falls back to Whisper.
    logger.warning("voice {} returned no boundary events", voice)
    return path, []


#: Where a CA bundle is looked for, in order. These are the two names the rest of
#: the Python ecosystem already uses — `httpx` and `requests` read them, so every
#: other outbound call in this repo is already configured by whoever set them.
_CA_BUNDLE_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")

#: Proxy variables, most specific first. `ALL_PROXY` is the catch-all and is only
#: consulted when neither WebSocket-relevant one is set.
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


@lru_cache(maxsize=1)
def _trust_extra_cas() -> str | None:
    """Teach edge-tts to trust the CAs the rest of this repo already trusts.

    edge-tts reaches Azure over a WebSocket and verifies it against a *module-level*
    context built from `certifi` at import time, which it then passes explicitly to
    `ws_connect(ssl=...)`. That explicit argument beats anything set on a connector,
    so handing it a connector of our own does nothing — the only reachable seam is
    the context object itself.

    Behind a TLS-inspecting proxy (every corporate network, most CI runners) this is
    the one provider in the repo that fails, with `CERTIFICATE_VERIFY_FAILED`, while
    every other outbound call succeeds — the rest go through `httpx`, which reads
    `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`. Those are exactly the two variables
    read here, so one setting configures everything.

    `load_verify_locations` is **additive**: certifi's 120-odd public roots stay
    trusted and the corporate root joins them. Nothing is weakened, and no
    verification is disabled — which is the tempting shortcut here and the reason
    this docstring is longer than the function.

    Worth the reach into another package's globals because of where the failure
    lands: voiceover is the 9th of 17 stages, so it fails *after* the research and
    the entire script chain have been paid for.
    """
    bundle = next((os.environ[var] for var in _CA_BUNDLE_VARS if os.environ.get(var)), None)
    if not bundle or not Path(bundle).is_file():
        return None

    from edge_tts import communicate

    context = getattr(communicate, "_SSL_CTX", None)
    if not isinstance(context, ssl.SSLContext):
        # Upstream renamed or restructured it. Not fatal: without a proxy in the
        # way the default context works, and saying so beats raising here.
        logger.warning("edge-tts SSL context not found; {} will not be applied", bundle)
        return None

    context.load_verify_locations(cafile=bundle)
    logger.debug("added {} to the CAs edge-tts trusts", bundle)
    return bundle


def _tts_proxy() -> str | None:
    """The proxy to reach Azure through, from the standard environment variables.

    edge-tts takes a proxy argument but does not read the environment for one, so on
    a network where outbound traffic *must* go through a proxy the connection is
    simply refused. `NO_PROXY` is deliberately not honoured: it is a host-list
    format with wildcard and suffix rules that is worth getting exactly right or not
    implementing at all, and a half-parse that wrongly bypasses the proxy fails in
    the same invisible way this function exists to fix.
    """
    return next((os.environ[var] for var in _PROXY_VARS if os.environ.get(var)), None)


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


#: The words a TTS engine says when it reads a number, a currency amount or a
#: date. Used only to decide how many spoken words one written token swallowed —
#: never to produce text, so a gap here costs alignment, not correctness.
_SPOKEN_NUMBER = frozenset(
    """zero one two three four five six seven eight nine ten eleven twelve
    thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty
    forty fifty sixty seventy eighty ninety hundred thousand million billion
    trillion and point oh dollars dollar cents cent percent pounds pound euros
    euro first second third fourth fifth sixth seventh eighth ninth tenth
    eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth
    eighteenth nineteenth twentieth thirtieth fortieth fiftieth
    january february march april may june july august september october
    november december""".split()
)

#: A written token worth restoring: it contains a digit or a currency symbol, so
#: the spoken form and the written form differ.
_WRITTEN_FORM = re.compile(r"[\d$£€%]")


def _restore_written_forms(word_cues: list[dict], original_text: str) -> list[dict]:
    """Re-align spoken word boundaries onto the text as it was written.

    edge-tts reports what it *said*, not what it read. So a script saying

        He put up $50,000 on October 5, 2018.

    produced captions reading "fifty thousand dollars" and "October fifth twenty
    eighteen" — which is how a caption track ends up looking like a transcript of
    a phone call rather than like the script. Numerals are also shorter, and
    caption space is the scarcest thing on a 9:16 frame.

    Walks the written tokens as the authority and consumes cues to match. A token
    containing a digit or a currency symbol swallows the run of spoken
    number-words that follows, and the merged cue keeps the first word's start
    and the last word's end, so timing never drifts. Everything else matches one
    to one by prefix, which also re-attaches the punctuation edge-tts strips —
    the written token already has it.

    Falls back to the input untouched if alignment goes badly wrong (fewer than
    two thirds of the cues consumed). A caption track that is merely missing its
    numerals beats one that has drifted out of sync with the audio.
    """
    written = [w for w in re.split(r"\s+", original_text.strip()) if w]
    if not written or not word_cues:
        return word_cues

    out: list[dict] = []
    cue_i = 0
    for token in written:
        if cue_i >= len(word_cues):
            break

        bare = _LEADING_STRIP.sub("", _TRAILING_SENT.sub("", token)).lower()

        # A token that is only punctuation - an em dash, an ellipsis - is spoken
        # as nothing at all, so it must not take a cue away from the next real
        # word. It used to: `bare` is empty, `_WRITTEN_FORM` does not match, and
        # the drift guard below is skipped because `bare` is falsy, so the token
        # fell through and overwrote the next cue's text with itself. For "the
        # bridge - fell" that produced "the", "bridge", "-" and lost "fell"
        # entirely, with the alignment fallback unable to fire because the cue
        # list had been consumed.
        if not bare:
            if out:
                out[-1] = {**out[-1], "text": f"{out[-1]['text']} {token}"}
            continue

        first = word_cues[cue_i]
        consumed = 1

        if _WRITTEN_FORM.search(token):
            # Swallow the spoken expansion: "fifty", "thousand", "dollars".
            #
            # Capped at what this particular number is actually worth rather than
            # at "as many number-words as follow". Greedy consumption looked right
            # and silently ate the next token: in "October 5, 2018", the "5,"
            # took "fifth twenty eighteen", and "2018." then consumed the word
            # after it — so "That was" lost its "That".
            budget = _spoken_word_count(token)
            while (
                consumed < budget
                and cue_i + consumed < len(word_cues)
                and word_cues[cue_i + consumed]["text"].strip(".,!?;:").lower() in _SPOKEN_NUMBER
            ):
                consumed += 1
        elif bare and not _matches(word_cues[cue_i]["text"], bare):
            # Drifted. Skip this written token rather than mislabelling a cue.
            continue

        last = word_cues[cue_i + consumed - 1]
        out.append({"start": first["start"], "end": last["end"], "text": token})
        cue_i += consumed

    if cue_i < len(word_cues) * 2 // 3:
        logger.warning(
            "subtitle alignment consumed only {}/{} cues; keeping the spoken forms",
            cue_i,
            len(word_cues),
        )
        return _restore_punctuation(word_cues, original_text)

    # Anything past the last written token still has to be shown.
    out.extend(word_cues[cue_i:])
    return out


def _spoken_word_count(token: str) -> int:
    """How many words a TTS engine spends saying this written token.

    Not a general number-to-words implementation — it only has to count, and only
    well enough to stop one number swallowing the next. Deliberately errs low: an
    under-count leaves a stray spoken word in the captions, an over-count eats a
    real word out of the script.
    """
    digits = re.sub(r"\D", "", token)
    if not digits:
        return 1

    words = 0
    value = int(digits)

    # A bare four-digit year is read as two pairs — "twenty eighteen" — not as
    # "two thousand and eighteen", which is why 2018 is two words and 2,018 is
    # three.
    if "," not in token and 1000 <= value <= 2099 and len(digits) == 4:
        words = 2
    else:
        for scale in (1_000_000_000, 1_000_000, 1_000):
            if value >= scale:
                words += _under_thousand(value // scale) + 1  # "...billion"
                value %= scale
        if value or not words:
            words += _under_thousand(value)

    # The unit is spoken too: "dollars", "percent".
    if re.search(r"[$£€%]", token):
        words += 1
    return max(1, words)


def _under_thousand(value: int) -> int:
    """Word count for 0-999: "three hundred and five" is four."""
    if value == 0:
        return 1  # "zero" - only reached for a literal 0
    words = 0
    if value >= 100:
        words += 2  # "three hundred"
        value %= 100
        if value:
            words += 1  # "and"
    if value >= 20:
        words += 1 + (1 if value % 10 else 0)  # "twenty" (+ "one")
    elif value:
        words += 1  # "nineteen"
    return words


def _matches(cue_text: str, bare: str) -> bool:
    spoken = cue_text.lower().strip()
    return bool(spoken) and (spoken == bare or bare.startswith(spoken) or spoken.startswith(bare))


def _group_cues(word_cues: list[dict], max_chars: int = 32) -> list[dict]:
    """Group word boundaries into readable subtitle lines.

    Breaks on sentence endings first, then on the character budget — a line that
    splits mid-clause reads badly at any font size.

    The budget is 32 rather than 42, and it is now checked *before* the word is
    added rather than after. Both came from looking at rendered frames: the old
    rule appended a word, noticed the line was over, and flushed — so 42 was a
    floor rather than a ceiling and a long word could carry a cue to 50-odd
    characters. At the caption font that is three lines on a 9:16 frame, which
    covers a third of the picture and is far more text than anyone reads off a
    Short. Two short lines is the target.
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
        candidate = " ".join([*(w["text"] for w in buffer), word["text"]])
        if buffer and len(candidate) > max_chars:
            flush()
        buffer.append(word)
        # A sentence ending still breaks immediately, whatever the length: the
        # full stop is a better place to cut than any character count.
        if word["text"].rstrip().endswith((".", "!", "?")):
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
