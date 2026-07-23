# Studio — YouTube Workflow Automation

Idea → researched script → narrated, subtitled, rendered video → SEO package → published to YouTube → measured → fed back into the next video.

Read [PLAN.md](PLAN.md) for phases. Read [docs/UI-DESIGN.md](docs/UI-DESIGN.md) before touching anything visual.

## Non-negotiables

1. **The UI stays quiet.** One primary action per screen. No dashboard that looks like a cockpit. If a screen needs a legend to understand, it's wrong. See `docs/UI-DESIGN.md`.
2. **Every generated artifact records the prompt + model that produced it.** The analytics feedback loop (Phase 8) is impossible otherwise. No exceptions, even for throwaway generations.
3. **Nothing publishes without an explicit approval gate** unless the user has turned on full-auto for that specific series.
4. **Secrets live in `.env`.** Never in `config.toml`, never committed, never logged. Refresh tokens are encrypted at rest.
5. **Cost is tracked per video** — LLM tokens, TTS characters, image gen, storage. Every provider call goes through the metering wrapper.

## Architecture

```
apps/web/       Next.js 15 App Router · Tailwind · Radix primitives. Dark-first.
apps/engine/    FastAPI + arq workers. Owns rendering, generation, publishing.
packages/contracts/  TS types generated from the engine's OpenAPI schema.
infra/          docker-compose: postgres, redis, engine, web.
```

- Web talks to the engine over REST; live job progress streams over SSE. The web app **never** touches the database directly.
- Queue is Redis + `arq`. Render jobs are long-running and must be resumable.
- Storage goes through an `ObjectStore` interface — local FS in dev, S3-compatible in prod. Never write a bare filesystem path in a service.

## The render core

`apps/engine/engine/services/` is derived from **MoneyPrinterTurbo** (`C:\Users\IsacC\Downloads\MoneyPrinterTurbo-Portable-Windows-1.3.2\MoneyPrinterTurbo`). Consult the original when behavior is unclear — especially `app/services/{video,voice,material,subtitle}.py`.

Rules for that code:
- Our public contract is `RenderRequest`, not MPT's `VideoParams`. `VideoParams` is an internal adapter detail and must not leak into API responses or the web app.
- The upstream code has Chinese comments and log strings. Translate them **in files you're already editing**. Do not do blanket translation passes — they produce unreviewable diffs.
- MPT's `state.py` (in-memory task dict) is replaced by Postgres-backed job records. If you see code reaching for `sm.state`, it needs porting.
- MPT reads `config.toml` globally. Ours reads a Pydantic `Settings` object. Any new `config.app.get(...)` call is a bug.

## Conventions

**Python (engine)**
- Python 3.11, `uv` for deps, `ruff` for lint+format, full type hints.
- Services are plain functions or small classes — no DI framework.
- External API calls: always through a client wrapper with retry/backoff and metering. Never `requests.get` inline in a service.
- Async by default; the MoviePy render work runs in a thread executor.

**TypeScript (web)**
- Server Components by default. `"use client"` only where interaction demands it.
- No component library dumped in wholesale — Radix primitives styled by us, per `docs/UI-DESIGN.md`.
- Types come from `packages/contracts`. Never hand-write a type that mirrors an API response.
- Data fetching in Server Components; mutations via Server Actions. TanStack Query only for the SSE-backed live job views.

**Both**
- Tests live next to the code. `pytest` and `vitest`.
- Conventional commits.

## Commands

```bash
docker compose up -d                        # postgres + redis
npm run dev                                 # web on :3000
apps/engine/.venv/Scripts/python -m uvicorn engine.main:app --reload --port 8080
apps/engine/.venv/Scripts/python -m pytest apps/engine/tests -q
```

The web app runs entirely on demo data (`apps/web/lib/demo.ts`) with no engine and no
API keys — that is deliberate, so the design can be judged before the plumbing exists.
Swapping to live data is a change to the data source, not to the views.

**Toolchain note:** this machine has neither `pnpm` nor `uv`, so the repo uses npm
workspaces and a plain venv at `apps/engine/.venv`. Switch to pnpm/uv if you install
them; nothing depends on the choice.

## Hard external limits — know these before designing around them

- **YouTube Data API quota: 10,000 units/day.** A `videos.insert` costs ~1,600 → **~6 uploads/day**. Maintain a quota ledger; surface remaining budget in the UI. Never design a feature that assumes unlimited uploads.
- **Title** ≤ 100 chars (aim ≤ 60 so it isn't truncated). **Description** ≤ 5000 chars, only ~150 visible before "more". **Tags** ≤ 500 chars total. **Thumbnail** ≤ 2MB, 1280×720.
- **YouTube's inauthentic-content policy** targets mass-produced templated content. Research-grounded scripts with real sources and original thumbnails are a monetization requirement, not a nicety.

## What not to do

- Don't add a settings page with 40 toggles. Opinionated defaults; expose the three things that actually vary.
- Don't let an LLM write SEO copy without keyword data behind it. Grounding is the whole point of Phase 4.
- Don't ship stock-footage-only long-form. Hero shots get generative B-roll.
- Don't cache API responses that contain user OAuth state.
