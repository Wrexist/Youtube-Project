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
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from loguru import logger

from engine.render import compose
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
        audio_path, cues = await _synthesize(script.full_text, voice)

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

        if not settings.pexels_api_key:
            raise RuntimeError("PEXELS_API_KEY is not set; cannot source footage")

        materials = Materials()
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(
            timeout=30.0, headers={"Authorization": settings.pexels_api_key}
        ) as client:
            for index, beat in enumerate(beats):
                await ctx.progress(
                    f"sourcing beat {index + 1}/{len(beats)}", (index + 1) / len(beats)
                )
                needed = max(1, round(beat.est_seconds / PACING.get(beat.energy, 3.5)))
                clips = await _pexels_search(
                    client, beat.visual_direction, aspect, needed, seen_ids
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
                params={"aspect": aspect, "unique_clips": len(seen_ids), "pacing": PACING}
            ),
        )


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

        output_path = await compose.compose_video(
            clips=materials.clips,
            beats=beats,
            audio_path=await store.local_path(voiceover.audio_key),
            cues=cues,
            aspect=ctx.inputs.get("aspect", "9:16"),
            job_id=ctx.job_id,
            on_progress=on_progress,
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
    estimated_cost_usd = 0.25

    async def run(self, ctx: WorkflowContext) -> StageOutput[list]:
        from engine.providers import llm

        titles = ctx.get("titles")
        script = ctx.try_get("revision") or ctx.get("draft")
        model = llm.for_task("thumbnail")

        concepts, completion = await model.json(
            f"""Title: {titles[0].text}
Hook: {script.hook}

Design 3 thumbnail concepts. The thumbnail is judged at 168 pixels wide on a phone —
anything that only reads at full size has failed.

Each concept: one focal point, and 3-5 words of overlay text that do NOT repeat the
title. If the title asks a question, the thumbnail shows the stakes.

The image prompt describes the image only. Never ask for text in the image; we
compose type separately so variants can be swapped later.

Return: {{"concepts": [{{"image_prompt": str, "overlay_text": str,
                        "focal_point": str, "rationale": str}}]}}""",
            max_tokens=1500,
        )

        variants = []
        for i, concept in enumerate(concepts["concepts"][:3]):
            await ctx.progress(f"rendering concept {i + 1}/3", (i + 1) / 3)
            key = await compose.make_thumbnail(concept, job_id=ctx.job_id, index=i)
            variants.append({**concept, "key": key})

        return StageOutput(
            value=variants,
            cost_usd=completion.cost_usd,
            artifacts={f"thumbnail_{i}": v["key"] for i, v in enumerate(variants)},
            provenance=Provenance(model=completion.model, prompt=completion.prompt),
        )


# ── providers ───────────────────────────────────────────────────────────────


async def _synthesize(text: str, voice: str) -> tuple[Path, list[dict]]:
    """Edge TTS with boundary capture.

    Boundary events are what give us subtitle cues without a transcription pass.

    edge-tts 7 defaults `boundary` to "SentenceBoundary", which yields one cue per
    sentence — a wall of text on screen. We ask for word boundaries explicitly, and
    still handle sentence boundaries, because not every voice honours the request.
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
        return path, _group_cues(words)
    if sentences:
        logger.info("voice {} returned sentence boundaries only; splitting", voice)
        return path, _split_sentence_cues(sentences)
    # No timings at all — SubtitlesStage falls back to Whisper.
    logger.warning("voice {} returned no boundary events", voice)
    return path, []


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


async def _pexels_search(
    client: httpx.AsyncClient,
    query: str,
    aspect: str,
    count: int,
    exclude: set[str],
) -> list[dict]:
    orientation = "portrait" if aspect == "9:16" else "landscape"
    try:
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": orientation, "per_page": count * 3},
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pexels search failed for {!r}: {}", query, exc)
        return []

    picked: list[dict] = []
    for video in resp.json().get("videos", []):
        vid = str(video["id"])
        if vid in exclude:
            continue  # upstream repeats clips across a video; we don't
        files = sorted(
            (f for f in video["video_files"] if f.get("width")),
            key=lambda f: f["width"],
            reverse=True,
        )
        if not files:
            continue
        picked.append(
            {
                "id": vid,
                "url": files[0]["link"],
                "duration": video.get("duration", 0),
                "query": query,
            }
        )
        if len(picked) >= count:
            break
    return picked


async def download_all(clips: list[dict]) -> None:
    """Fetch clip files in parallel. Called by the compose step."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        await asyncio.gather(*(_download(client, c) for c in clips))


async def _download(client: httpx.AsyncClient, clip: dict) -> None:
    key = f"materials/{clip['id']}.mp4"
    if await store.exists(key):
        clip["path"] = str(await store.local_path(key))
        return
    resp = await client.get(clip["url"])
    resp.raise_for_status()
    clip["path"] = str(await store.put_bytes(resp.content, key))
