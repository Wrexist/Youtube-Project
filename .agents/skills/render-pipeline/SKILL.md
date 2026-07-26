---
name: render-pipeline
description: Map of the video render engine derived from MoneyPrinterTurbo — TTS, stock material sourcing, subtitles, MoviePy composition, and the job/queue model. Use when working in apps/engine/engine/services, debugging a render, porting or refactoring upstream MPT code, or adding a provider (TTS, stock footage, LLM).
---

# Render Pipeline

## Upstream reference

`C:\Users\IsacC\Downloads\MoneyPrinterTurbo-Portable-Windows-1.3.2\MoneyPrinterTurbo`

Our `apps/engine/engine/services/` is derived from `app/services/` there. When behavior is unclear, read the original — but do not copy new code across without adapting it to our conventions (Settings object, ObjectStore, metering wrapper, Postgres job state).

## Stage map

| Stage | Origin | Notes |
|---|---|---|
| Script | `llm.generate_script` | **Replaced** by our chain — see `script-architecture` skill |
| Search terms | `llm.generate_terms` | Keep, but generate **per beat**, not globally |
| Voiceover | `voice.tts` | ~8 provider backends; returns audio + `SubMaker` timing data |
| Subtitles | `voice.create_subtitle` / `subtitle.py` | From TTS timings; Whisper is the fallback when timings are absent |
| Materials | `material.py` | Pexels / Pixabay / Coverr search + download |
| Composition | `video.py` | MoviePy: concat, transitions, subtitle burn-in, BGM mix |
| Orchestration | `task.py` | The end-to-end sequence — our `RenderJob` replaces it |

## Provider interfaces — add new providers here, not inline

- **TTS**: Edge (free, default), Azure v1/v2, ElevenLabs, Gemini, SiliconFlow, MiMo, Chatterbox. `voice.parse_voice_name` + the `is_*_voice` predicates dispatch. Adding one means: a `*_tts` function, a predicate, and a branch in `tts()`.
- **Stock**: Pexels, Pixabay, Coverr. Keyed by search term + aspect + min duration.
- **LLM**: ~20 adapters in `llm.py`. All OpenAI-compatible except Gemini/Ollama/Cloudflare.

Every provider call goes through the metering wrapper. A provider added without metering breaks per-video cost tracking.

## Subtitle timing — the fragile part

Edge TTS returns word boundary cues; the other backends mostly don't. `voice.py` has two paths (`_build_subtitle_items_from_edge_cues` vs `_build_subtitle_items_from_legacy_submaker`) and a Whisper fallback in `subtitle.py`.

Consequences:
- Chapter timestamps must be derived from the **final** subtitle file, never from estimates.
- Switching TTS provider can silently change subtitle quality. Any provider change needs a render test.
- Non-Latin scripts have special handling (`_normalize_arabic`). Don't remove it.

## Materials — the biggest quality lever

MPT's default matches one global keyword set to all clips, so a video about three different things shows footage about the average of them. `match_materials_to_script` exists upstream and is off by default.

**In our system, per-beat matching is the default.** Each beat's `visual_direction` drives its own search. Improve on upstream by:
- Deduplicating clips across the video (upstream repeats them)
- Respecting `energy` for clip length — high-energy beats cut faster
- Cutting on sentence boundaries from the subtitle timings, not on fixed intervals
- Falling back to generative video (Higgsfield `generate_video`) for hero beats where stock is inadequate

## Rendering

MoviePy + FFmpeg. It is CPU-bound and slow.

- Run in a thread executor; never block the async event loop.
- `n_threads` matters. Hardware encode (`h264_nvenc` / `h264_qsv`) where available.
- Prepare clips in parallel, compose serially.
- Long-form renders take minutes. The job must be resumable and must report progress — the Create screen's pipeline view depends on it.

## Job model

Upstream `state.py` is an in-memory dict (or Redis) with `max_concurrent_tasks`. Ours is Postgres-backed:

- One row per job, one row per stage, with status, timing, cost, and the produced artifact reference.
- Stage outputs are addressable so the UI can show and edit them.
- Editing a stage marks downstream stages stale — re-run from that point, don't restart the job.
- `arq` workers pull from Redis. Concurrency capped by render cost, not job count.

## Gotchas carried over from upstream

- Global `config.toml` reads via `config.app.get(...)` — any occurrence in our code is a bug; use `Settings`.
- Chinese comments and log strings throughout. Translate files you're editing anyway; no blanket passes.
- `file_security.py` guards path traversal on the download/stream endpoints. Keep it if those endpoints survive.
- The bundled BGM in `resource/songs` has unclear licensing. Do not publish anything using it.
- Pydantic v2 with a `warnings.filterwarnings` suppressing a field-shadowing warning in `schema.py` — a smell worth cleaning when `VideoParams` becomes an internal adapter.
