# YouTube Workflow Automation — Build Plan

**Working name:** Studio (rename freely)
**Goal:** One system that takes an idea → researched script → narrated, subtitled, rendered video → SEO-optimized title/description/tags/thumbnail → published to YouTube on a schedule → measured, with results fed back into the next video.

**Design principle:** the surface is quiet — a few clean screens, generous whitespace, one primary action per view. All the intelligence lives behind it.

---

## Decisions locked

| Decision | Choice |
|---|---|
| Repo | Fresh git repo at `C:\Users\IsacC\Downloads\Youtube-Project` |
| Content | Faceless AI **shorts (9:16)** + faceless **long-form (16:9)** |
| Stack | Next.js web UI + Python FastAPI engine (MoneyPrinterTurbo extracted as the render core) |

## What we inherit from MoneyPrinterTurbo

Confirmed present and reusable (`MoneyPrinterTurbo/app/services/`):

- `llm.py` — ~20 provider adapters (OpenAI, Gemini, DeepSeek, Qwen, Ollama, Groq, Azure…), script + search-term generation, and an existing `generate_social_metadata()` returning `{title, caption, hashtags}`
- `voice.py` — TTS across Edge, Azure v1/v2, ElevenLabs, Gemini, SiliconFlow, Chatterbox, MiMo; plus SRT generation from TTS timings
- `material.py` — Pexels / Pixabay / Coverr stock footage search + download
- `subtitle.py` — Whisper fallback transcription
- `video.py` — MoviePy render: clip concat, transitions, subtitle burn-in, BGM mixing
- `task.py` — the end-to-end orchestration
- `upload_post.py` — third-party cross-post to TikTok/IG/YouTube Shorts
- `state.py` — in-memory or Redis task state, concurrency caps

What is **missing entirely** and is the actual product we're building:

- Real YouTube Data API publishing (OAuth, scheduling, playlists, captions, chapters)
- SEO that's grounded in keyword data rather than one LLM guess
- Thumbnails
- Long-form structure (research, outline, chapters, retention pacing)
- Anything resembling a channel, a series, a calendar, or an analytics feedback loop
- A UI worth looking at (Streamlit today)

---

## Architecture

```
Youtube-Project/
  apps/
    web/          Next.js 15 (App Router) + Tailwind + Radix. Dark-first, minimal.
    engine/       FastAPI. Extracted + refactored MPT services. Owns rendering.
  packages/
    contracts/    OpenAPI-generated TS types — one source of truth for the API
  infra/
    docker-compose.yml   postgres + redis + engine + web
```

- **Web ↔ Engine**: REST + Server-Sent Events for live job progress.
- **Queue**: Redis + `arq` (async, Python-native — lighter than Celery, keeps the engine one process type).
- **DB**: Postgres + SQLAlchemy/Alembic in the engine (single writer; web reads through the API, no shared ORM).
- **Storage**: local `storage/` for dev, S3-compatible interface from day one so hosting is a config change.
- **Secrets**: `.env` only. Never in `config.toml` committed to git.

### Data model (core tables)

```
Channel        youtube_channel_id, oauth tokens (encrypted), niche, tone, brand kit
Series         a repeatable format: aspect, length, voice, visual style, cadence
Idea           topic, source (manual | trend | keyword gap), score, status
Script         hook, beats[], full_text, word_count, est_duration, model+prompt used
Asset          voiceover, clip, music, subtitle, thumbnail — typed, hashed, reusable
Render         params snapshot, job state, output path, duration, cost
SeoPackage     title variants[], description, tags[], chapters[], chosen_variant
Publication    youtube_video_id, scheduled_at, privacy, playlist, publish result
Metric         daily CTR, AVD, retention curve, impressions — per video
```

Every generated artifact stores **the prompt and model that produced it**. Without that, the feedback loop in Phase 8 can't attribute wins.

---

## Phases

Each phase ends with something you can actually run and see.

### Phase 0 — Foundations *(0.5 day)*
1. `git init` at `Youtube-Project`, `.gitignore`, `.env.example`, README.
2. Monorepo scaffold: `apps/web` (Next.js + Tailwind + Radix), `apps/engine` (FastAPI + uv).
3. `docker-compose.yml`: postgres, redis.
4. CI: lint + typecheck + test on push.
5. **Exit:** `docker compose up` boots; web at :3000 hits engine `/health` at :8080.

### Phase 1 — Engine extraction *(2–3 days)*
1. Copy MPT's `app/services/{llm,voice,material,subtitle,video,task}.py` into `apps/engine/engine/services/`.
2. Strip Streamlit/WebUI coupling; replace `config.toml` reads with a Pydantic `Settings` object over env vars.
3. Replace the in-memory `state.py` with Postgres-backed job records; wire `arq` workers.
4. Normalize the Chinese comments/log strings to English as we touch files (don't do a blanket rewrite).
5. Introduce a `RenderRequest` contract that is *ours*, not MPT's `VideoParams` — MPT's params become an internal adapter.
6. **Exit:** `POST /v1/renders` with a subject produces an MP4 in `storage/`, with progress readable via SSE.

### Phase 2 — UI shell & design system *(2 days)*

Full spec: **[docs/UI-DESIGN.md](docs/UI-DESIGN.md)** — tokens, the five screens, motion, accessibility.

1. Design tokens per the spec: one accent, neutral ramp, 4px spacing scale, two font weights. Dark **and** light ship together.
2. Core layout: 64px icon-only left rail (Create · Queue · Library · Calendar · Analytics · Settings), no top nav, no breadcrumbs.
3. Components: stage row, job card, variant picker, thumbnail true-scale preview, cost chip, ⌘K palette.
4. **The one screen that matters:** the Create composer — one input, then a live pipeline of collapsing stages, each editable inline, streaming over SSE and surviving a reload.
5. **Exit:** you can generate a short end-to-end from the browser and watch it build, on a screen you'd be happy to show someone.

### Phase 3 — Script intelligence *(3 days)*
1. Replace MPT's single-shot script prompt with a staged chain:
   `research → angle selection → hook (3 variants) → beat outline → full script → self-critique pass`.
2. Hook library: encode the patterns that hold retention in the first 3 seconds; score generated hooks against them.
3. Long-form mode: 800–2000 word scripts with explicit chapter beats and B-roll direction per beat.
4. Web research step (web search + fetch) so scripts contain facts, not LLM filler — with source list retained.
5. Duration targeting: words → estimated runtime via the actual TTS rate, iterate until within tolerance.
6. **Exit:** a 10-minute long-form script with chapters and per-beat visual direction, from one topic input.

### Phase 4 — SEO brain *(3 days)*
1. Keyword research service: YouTube autocomplete scraping + search-volume data (Semrush MCP is connected on this machine) + competitor title mining via YouTube Data API `search.list`.
2. Title generator: 8 variants across distinct strategies (curiosity gap, number, contrarian, outcome, question), each scored on length ≤60 chars, keyword-front-loading, and CTR heuristics.
3. Description generator: hook paragraph → keyword-rich body → timestamped chapters → links → hashtags. Respects the 5000-char limit and the fact that only the first ~150 chars show.
4. Tags: 15–25, mixing head + long-tail, under the 500-char total cap.
5. Chapters auto-derived from script beats + actual subtitle timings.
6. Replace MPT's `generate_social_metadata` with this; keep it as a fallback.
7. **Exit:** an SEO panel showing variants, scores, character counts, and a live YouTube-search-result preview.

### Phase 5 — Thumbnails *(2 days)*
1. Concept generation from the script's core tension (3 distinct concepts).
2. Image generation (the Higgsfield MCP `generate_image` is available; keep a provider interface so this is swappable).
3. Text overlay engine: 3–5 words max, huge weight, high contrast, safe zones for the duration badge.
4. Render 1280×720, under 2MB, and preview at actual feed sizes (small mobile thumb is the real test).
5. Store variants for later A/B swapping.
6. **Exit:** three finished thumbnails per video, previewed at true scale.

### Phase 6 — Long-form render pipeline *(3 days)*
1. Per-beat material matching instead of one global keyword set (MPT has `match_materials_to_script`; make it the default and improve it).
2. Pacing rules: clip length varies by beat energy; cut on sentence boundaries.
3. Music bed with ducking under narration; intro/outro stingers.
4. Subtitle styling presets that actually look good (the current defaults do not).
5. Render performance: parallel clip prep, hardware encode where available.
6. **Exit:** a 16:9 10-minute video that doesn't look auto-generated.

### Phase 7 — YouTube publishing *(2–3 days)*
1. Google OAuth 2.0 flow, encrypted refresh-token storage, multi-channel support.
2. `videos.insert` resumable upload with progress; set title, description, tags, category, language, `madeForKids`.
3. Scheduled publishing via `privacyStatus: private` + `publishAt`.
4. Caption upload (`captions.insert`) from our SRT — real captions, not just burned-in.
5. Thumbnail set (`thumbnails.set`), playlist insertion, localization if multi-language.
6. **Quota reality:** default is 10,000 units/day and an upload costs ~1,600 → **~6 uploads/day per project**. Build a quota ledger and surface remaining budget in the UI; plan a quota-extension application early if volume matters.
7. **Exit:** one-click publish or schedule, from the web UI, to a real channel.

### Phase 8 — Analytics feedback loop *(2 days)*
1. YouTube Analytics API: daily pull of impressions, CTR, average view duration, retention curve.
2. Attribute performance back to the title variant / thumbnail / hook / script prompt that produced it.
3. Surface: "titles using the curiosity-gap pattern average 6.2% CTR vs 4.1% for numbers, over 23 videos."
4. Feed winning patterns into the Phase 3/4 prompts automatically.
5. Retention drop-off markers mapped onto script beats — so you can see *which sentence* loses people.
6. **Exit:** a dashboard that changes what the generator does next.

### Phase 9 — Automation & scale *(2 days)*
1. Series scheduling: "3 shorts/week + 1 long-form/week from this niche" as a standing config.
2. Idea pipeline: trend monitoring + keyword-gap detection populating a backlog automatically.
3. Approval gates — nothing publishes without review unless you explicitly enable full auto.
4. Batch generation with cost caps and a hard spend ceiling.
5. Failure handling: retries, dead-letter queue, notification on failure.
6. **Exit:** the system produces a week of content while you approve from a phone.

### Phase 10 — Hardening *(2 days)*
1. Cost tracking per video (LLM tokens, TTS characters, image gen, storage).
2. Tests: unit on SEO scoring and duration estimation; integration on the render pipeline with a stub provider.
3. Rate limits, retry/backoff on every external API.
4. Observability: structured logs, error tracking.
5. Deployment: Docker images, one-command deploy.

---

## Risks worth naming now

1. **YouTube's inauthentic-content policy.** Mass-produced stock-footage-over-TTS content is explicitly targeted by YouTube's monetization rules. Volume alone will get a channel rejected. The mitigation is real in the design: research-grounded scripts with sources, original thumbnails, distinct per-series voice and visual identity — Phase 3's research step is a monetization requirement, not a nicety.
2. **API quota** — see 7.6. This is the hardest ceiling on throughput.
3. **Stock footage sameness.** Pexels clips recur across thousands of channels. Budget for generative B-roll (Higgsfield `generate_video` is available) on hero shots at minimum.
4. **Music licensing.** MPT ships BGM of unclear provenance. Replace with a licensed source before publishing anything.
5. **Cost per video.** Long-form with generative B-roll and premium TTS is realistically $2–8/video. Phase 10.1 makes this visible before it surprises you.

---

## Suggested order

Phases 0→2 give a working system fast. Then **4 (SEO) and 5 (thumbnails) before 6 (long-form render)** — title and thumbnail drive more of a video's outcome than production polish does, and they're cheaper to build.

## Immediate next steps

1. Confirm the phase order and cut anything you don't want.
2. Decide which LLM provider is primary (Claude via `claude-opus-4-8` recommended for the script/SEO chains; a cheap fast model for bulk work).
3. Get Google Cloud project + YouTube Data API credentials started — approval can take days, so it should be in flight before Phase 7.
4. Begin Phase 0.
