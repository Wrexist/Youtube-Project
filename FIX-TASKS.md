# Fix tasks

Each task below is a **self-contained prompt**. Paste one into Cowork (or Claude Code)
and it should be actionable without any of this conversation's context.

They are ordered by dependency. Tasks marked **[HUMAN]** cannot be done by an agent at
all — do those yourself, or the agent tasks that depend on them will stall.

Every task ends with a **Done when** that can be checked by running something. If a
task can't be verified by a command, it isn't finished.

---

## How to work in this repo

Give the agent this preamble with any task:

> This is Studio, a YouTube automation system. Read `CLAUDE.md` first — it has the
> architecture, conventions, and the hard external API limits. Read `KNOWN-ISSUES.md`
> for what's already known to be broken.
>
> - Engine: `apps/engine` — Python 3.11+, FastAPI, ruff, pytest. Venv at
>   `apps/engine/.venv`.
> - Web: `apps/web` — Next.js 16, React 19, Tailwind 4. Design rules in
>   `docs/UI-DESIGN.md`, enforced by the `studio-ui` skill.
> - Before claiming done: `ruff check engine tests && ruff format --check engine tests
>   && pytest -q` in `apps/engine`, and `npm run typecheck && npm run build` at root.
> - Add tests for logic you add. There are 104; keep them passing.

### Guardrails — do not "fix" these, they are deliberate

The agent will be tempted to remove these because they look like obstacles. They are
the opposite:

1. **The statistical gate in `engine/insights.py`** (8+ videos per group, p<0.05, ≥8%
   lift). Loosening it makes the feedback loop train on noise and degrade the system
   invisibly.
2. **Stages that fail rather than proceed ungrounded** — `ResearchStage` with no
   sources, `GroundingStage` with no keyword evidence. Those failures are the
   inauthentic-content defence, not bugs.
3. **The manual-steps list in `engine/channel.py`.** YouTube genuinely has no channel
   creation API. Do not add code that pretends otherwise.
4. **Approval gates default to on.** Auto-publish skips the waiting, never the checks.
5. **`Provenance` being required on every `StageOutput`.** The whole analytics loop
   depends on it.

---

# Phase A — prerequisites

## A1. [HUMAN] Google Cloud OAuth credentials

**Nothing publishes and no analytics work until this exists. Approval can take days —
start it before anything else.**

1. <https://console.cloud.google.com> → new project, e.g. `studio-youtube`.
2. APIs & Services → Library → enable **YouTube Data API v3** *and* **YouTube
   Analytics API**.
3. OAuth consent screen → External → fill in app name, support email, developer email.
4. Add scopes: `youtube.upload`, `youtube.readonly`, `youtube.force-ssl`,
   `yt-analytics.readonly`.
5. Add your own Google account under **Test users** (unverified apps are capped at
   100 users).
6. Credentials → Create → OAuth client ID → **Web application**. Authorised redirect
   URI: `http://localhost:8080/v1/auth/google/callback`.
7. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` /
   `GOOGLE_CLIENT_SECRET`.

**Done when:** `GET http://localhost:8080/v1/auth/google` returns a URL that leads to
a real Google consent screen.

## A2. [HUMAN] The other keys

```bash
cp .env.example .env
```

- `ANTHROPIC_API_KEY` — <https://console.anthropic.com>. Or skip it and route
  everything to Ollama (task B3).
- `PEXELS_API_KEY` — <https://www.pexels.com/api/>. Free, instant.
- `STUDIO_SECRET_KEY` — any 32+ random characters. Encrypts stored refresh tokens.

**Done when:** `GET http://localhost:8080/health` returns `ok: true` and `pytest -q`
still passes.

---

# Phase B — make it real

## B1. Wire the web app to the engine

> **The single biggest gap in this project.** Every screen in `apps/web` renders from
> `apps/web/lib/demo.ts`. There is no `fetch` and no SSE subscription anywhere. The UI
> currently proves the design, not the integration.
>
> Replace the demo data with live engine calls:
>
> 1. Create `apps/web/lib/api.ts` with a typed client for the engine at
>    `process.env.NEXT_PUBLIC_ENGINE_URL` (default `http://localhost:8080`). Cover:
>    `POST /v1/jobs`, `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/edit`,
>    `GET /v1/models`, `PUT /v1/models/route`, `GET /v1/calendar`,
>    `POST /v1/calendar/schedule`, `POST /v1/calendar/auto`, `GET /v1/insights`,
>    `POST /v1/channels/launch`.
> 2. Create `apps/web/lib/useJobStream.ts` — a hook subscribing to
>    `GET /v1/jobs/{id}/events` via `EventSource`. The engine replays past events on
>    connect, so a mid-render page reload must show the full pipeline, not a blank
>    screen. Handle reconnection.
> 3. Rewrite `apps/web/app/page.tsx` (Create) to POST a real job and drive the
>    pipeline from the stream. "Re-run from here" calls the edit endpoint.
> 4. Convert Queue, Calendar, Analytics, Series, Models and New channel to Server
>    Components fetching real data. Keep `demo.ts` and fall back to it when the engine
>    is unreachable, showing a clear "engine offline — showing sample data" banner.
>    Being able to explore the UI without a backend is worth keeping.
> 5. Types come from the engine's responses. Update `apps/web/lib/types.ts` to match
>    `_serialize_stages` in `apps/engine/engine/main.py` exactly.
>
> Do not change any visual design. `docs/UI-DESIGN.md` still governs.

**Done when:** with the engine running, typing a topic on `/` creates a real job whose
stages update live, and reloading mid-run restores the full pipeline state.

## B2. First real end-to-end run

> Generate one **short** video end to end and fix whatever breaks. Requires A2.
>
> ```bash
> curl -X POST http://localhost:8080/v1/jobs \
>   -H 'Content-Type: application/json' \
>   -d '{"topic":"why bridges collapse","format":"short","aspect":"9:16"}'
> ```
>
> Then poll `GET /v1/jobs/{id}` until it completes or fails.
>
> Expect breakage in this order — it is the least-exercised path in the system:
> research → angle → hook → beats → draft → critique → revision → voiceover →
> subtitles → materials → render.
>
> For each failure: fix the root cause, add a regression test, and append it to
> `KNOWN-ISSUES.md` §4 so it is not reintroduced. Do not paper over a failure by
> loosening a validation.
>
> Watch specifically for: Pexels returning nothing for abstract `visual_direction`
> strings; MoviePy choking on a clip with an unusual codec; the beat-to-timeline
> mapping in `_beat_spans` drifting on a real audio duration.

**Done when:** a playable MP4 exists in `storage/renders/`, subtitles are legible and
in sync, and the footage visibly relates to what is being said.

## B3. Verify Ollama against a real daemon

> The routing table, `/api/chat` transport and cost model in
> `apps/engine/engine/providers/llm.py` are implemented and unit-tested, but **no
> actual Ollama server has ever been called.** `probe_ollama` and the
> `/v1/models/ollama/register` endpoint are entirely unverified.
>
> ```bash
> ollama serve
> ollama pull qwen2.5:14b
> ```
>
> 1. `GET /v1/models/ollama` — confirm it lists installed models with sizes.
> 2. `POST /v1/models/ollama/register` — confirm they enter the catalogue.
> 3. `POST /v1/models/test` with `{"model":"ollama:qwen2.5:14b"}` — confirm a real
>    round trip returning parsed JSON and `cost_usd: 0`.
> 4. Route `tags` and `chapters` to it, run task B2, confirm those stages work.
> 5. Then route **everything** local and run B2 again. Record honestly in
>    `KNOWN-ISSUES.md` which stages produce acceptable output locally and which do
>    not. This is the information a user actually needs to make the trade.
>
> Fix any transport bugs found. Pay attention to `num_predict` — Ollama's default
> max output is small and long-form drafts will truncate silently.

**Done when:** a full video generates with every stage on local models, and
`KNOWN-ISSUES.md` states plainly which stages degrade.

---

# Phase C — durability

## C1. Move state to Postgres

> All job and channel state is in module-level dicts. A restart loses everything:
> `JOBS` (`engine/main.py`), `CHANNELS` / `_STATES` / `SCHEDULE`
> (`engine/api/publishing.py`), `RECORDS` (`engine/api/insights.py`), `LAUNCHES`
> (`engine/api/channels.py`).
>
> 1. `docker compose up -d` already starts Postgres.
> 2. Add SQLAlchemy 2.0 async models in `apps/engine/engine/db/models.py` following
>    the schema sketched in `PLAN.md` (Channel, Series, Idea, Script, Asset, Render,
>    SeoPackage, Publication, Metric). Add the quota ledger and spend ledger tables
>    too.
> 3. Alembic migrations in `apps/engine/migrations/`.
> 4. Replace each dict with a repository module. **Keep the return shapes identical** —
>    the swap should not touch the API layer or the web app.
> 5. Persist stage outputs so `Workflow.run(states=...)` can resume a job after a
>    process restart, not just within one. This is the point of the whole exercise.
> 6. Encrypt the refresh token column at rest — `engine/crypto.py` already does this;
>    make sure the plaintext never reaches the database.
>
> Add a test that a workflow resumes correctly from persisted state after simulated
> restart.

**Done when:** a job survives `docker compose restart` and resumes from its last
completed stage rather than starting over.

## C2. Real workers with arq

> Jobs currently run as in-process `asyncio.create_task` calls in
> `engine/main.py:create_job`. One slow render blocks nothing, but nothing scales and
> a crashed process loses every running job.
>
> 1. Create `apps/engine/engine/worker.py` with an arq `WorkerSettings`.
> 2. `POST /v1/jobs` enqueues instead of spawning.
> 3. Progress events go through Redis pub/sub so the SSE endpoint works regardless of
>    which worker picked the job up.
> 4. Concurrency capped by `settings.max_concurrent_renders` — renders are CPU-bound
>    and oversubscribing makes everything slower.
> 5. Dead-letter handling: a job failing all retries lands in a queryable failed state
>    with the failing stage name preserved, so the Queue screen's "Retry from here"
>    works.
>
> Requires C1.

**Done when:** two workers process jobs concurrently, killing one mid-render does not
lose the job, and SSE still streams to the browser.

## C3. Authentication

> The engine is completely unauthenticated. Every endpoint — including ones that spend
> money and publish videos — is open to anyone who can reach the port.
>
> Simplest thing that is actually safe for a single-user tool: a bearer token in
> `STUDIO_API_TOKEN`, required by a FastAPI dependency on every route except
> `/health`. Web app sends it from a server-side env var, never exposed to the client.
>
> Do not build user accounts. This is a single-user tool; a login system is scope you
> do not need.

**Done when:** every route except `/health` returns 401 without a token, and the web
app still works.

---

# Phase D — quality gaps

## D1. Restore punctuation in subtitles

> edge-tts word boundaries strip punctuation, so cues read `On purpose Here is why`
> instead of `On purpose. Here is why`. Cosmetic when burned in, **materially worse
> for the SRT uploaded as a caption track** — which is a real ranking signal.
>
> In `apps/engine/engine/workflows/media.py`, `_group_cues` receives only the stripped
> word list. Thread the original script text through and realign: walk the cue words
> against the source, restoring punctuation and capitalisation. MoneyPrinterTurbo does
> a version of this in
> `C:\Users\IsacC\Downloads\MoneyPrinterTurbo-Portable-Windows-1.3.2\MoneyPrinterTurbo\app\services\voice.py:_match_script_line`
> — read it, but the alignment there is fragile; a proper sequence alignment is better.
>
> Once punctuation is restored, the sentence-break rule in `_group_cues` (which checks
> for `.`/`!`/`?`) will finally fire, so cues should break on clauses instead of purely
> on the character budget. Verify that actually happens.

**Done when:** a test synthesises a multi-sentence script and asserts the cues carry
punctuation and break on sentence ends.

## D2. Wire a real thumbnail image provider

> `make_thumbnail` in `apps/engine/engine/render/compose.py` composes real typography
> with correct safe zones onto a **solid colour placeholder**. No image model is
> connected.
>
> 1. Add an `ImageProvider` interface in `apps/engine/engine/providers/images.py` with
>    at least one implementation. Route it through `engine/models.py` like the LLMs so
>    the provider is user-selectable and metered.
> 2. `make_thumbnail` generates the background from `concept["image_prompt"]`, then
>    composes type over it exactly as now.
> 3. **Keep text composition in code.** Never ask the image model for text — generated
>    typography is unreliable and a separate text layer is what makes Phase 8's A/B
>    variant swapping possible.
> 4. Enforce the real limits: 1280×720, JPEG, ≤2MB. Compress if over.
> 5. Reject and regenerate images with visible artefacts in faces or hands rather than
>    shipping them.
>
> Read the `thumbnail-design` skill first — it has the rules that matter (3–5 words,
> readable at 168px, don't repeat the title).

**Done when:** three visually distinct thumbnails are produced for one video, all
under 2MB, and the true-scale preview component shows them legibly at 168px.

## D3. Semantic duplicate detection

> `similarity()` in `apps/engine/engine/ideas.py` uses Jaccard overlap on content
> words. It correctly catches "why bridges collapse" vs "the reason bridges collapse".
> It will **not** catch "why bridges collapse" vs "the physics of structural failure in
> suspension spans" — the same video with no shared words.
>
> Add an optional embedding-based check **layered on top of** the lexical one, not
> replacing it:
>
> - Lexical first: cheap, explainable, catches the common case.
> - Embedding second, only for pairs the lexical check rated 0.2–0.45 (the ambiguous
>   band). This keeps the cost bounded.
> - Local embeddings via Ollama (`/api/embeddings`) so this stays free.
> - The idea card must still explain *why* something was rejected. "87% semantically
>   similar to X" is fine; an unexplained rejection is not.
>
> Keep `DUPLICATE_THRESHOLD` and the existing tests passing.

**Done when:** a test proves a semantically-identical but lexically-distinct topic
pair is caught, and all existing dedup tests still pass.

## D4. Value-aware keyword and tag trimming

> `trim_keywords` in `engine/channel.py` and `validate_tags` in `engine/workflows/seo.py`
> both enforce the 500-character budget by **keeping the earliest entries and dropping
> the rest**. They should drop the lowest-value ones.
>
> Rank by the evidence already available — `KeywordEvidence.suggestions` carries
> autocomplete rank, which is a real signal of search volume. Keep the highest-ranked
> within budget. Preserve the exact-title tag unconditionally.

**Done when:** a test shows a high-value keyword listed last survives trimming while a
low-value one listed first is dropped.

---

# Phase E — completeness

## E1. Command palette

> `docs/UI-DESIGN.md` leans on ⌘K to keep every screen sparse, and it was never built.
> Its absence is why some screens are creeping toward having more buttons than the
> design allows.
>
> Build it in `apps/web/components/command-palette.tsx`: new video, jump to any video
> by title, run a series, open any screen, toggle theme. Radix Dialog, keyboard-only
> operable, respects `prefers-reduced-motion`.

**Done when:** ⌘K opens from any screen and every action works by keyboard alone.

## E2. Thumbnail A/B swapping

> Phase 8's attribution is already built for this and nothing uses it. `ThumbnailStage`
> stores all three variants; `Finding` can attribute CTR to `thumbnail_concept`.
>
> Add a job that, for a published video underperforming its channel median CTR after a
> set period, swaps in the next thumbnail variant via `thumbnails.set` (50 quota units
> — cheap) and records the swap date so the analysis can compare before and after.
>
> Guardrails: never swap within the first 48 hours (data is provisional and CTR is
> unstable early); never swap more than once per video per 14 days; log every swap so
> the attribution can segment on it.

**Done when:** a test drives the decision logic across the timing and frequency rules,
and the swap is recorded in a form the insights module can read.

## E3. Trend monitoring

> `build_backlog` in `engine/ideas.py` accepts a `trending_terms` argument that
> **nothing currently supplies**, so the `freshness` score component is always zero.
>
> Add a source. YouTube's trending endpoint (`videos.list` with `chart=mostPopular`)
> costs 1 unit and is category-filterable — cheap enough to poll daily. Combine with
> rising autocomplete queries (compare today's `suggest()` output to a stored snapshot
> and surface what is new).
>
> Feed the result into the series run planner so a genuinely trending topic can jump
> the backlog queue.

**Done when:** `freshness` is non-zero for a real trending topic and the run planner
demonstrably prioritises it.

---

## Suggested order

**A1 today** — it is the only item with multi-day external latency and it blocks the
most.

Then **A2 → B2** (prove the pipeline works at all) → **B1** (make the UI real) →
**C1 → C2** (make it survivable) → **D1, D2** (quality) → the rest as you want them.

`B3` can happen any time after A2 and is worth doing early if you would rather not pay
per video while debugging.
