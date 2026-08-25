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

`apps/engine/engine/services/` and `engine/render/compose.py` are derived from **MoneyPrinterTurbo**. The upstream source is vendored at [`vendor/moneyprinterturbo/`](vendor/moneyprinterturbo/README.md) — consult it when behavior is unclear, especially `app/services/{video,voice,material,subtitle}.py`. It is reference only: nothing imports it, ruff and pytest never see it, and it is not edited.

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
- **Web tests live next to the code** — `components/weekly-review.test.tsx` beside
  `components/weekly-review.tsx`. **Engine tests live in `apps/engine/tests/`**, all
  fifty of them, mirroring the module name: `tests/test_review.py` for
  `engine/review.py`.

  This line used to say "tests live next to the code" without qualification, which
  was true of the three web tests and false of every Python one. A reviewer read the
  rule, correctly observed that no engine test follows it, and filed it four times
  across two pull requests. Describing what is actually there is cheaper than moving
  fifty files, and far cheaper than leaving a rule nobody obeys.

  `pytest` and `vitest` — both installed, both run in CI (`npm run test`).
- Conventional commits.

## Commands

```bash
npm start                                   # both halves, one command — what users run
docker compose up -d                        # postgres + redis
npm run dev                                 # web on :3000 (one half only)
apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080
apps/engine/.venv/bin/python -m pytest apps/engine/tests -q   # SQLite — not what CI runs
cd apps/engine && .venv/bin/python -m alembic upgrade head   # schema — see below
apps/engine/.venv/bin/python -m arq engine.worker.WorkerSettings   # render worker + weekly review cron
```

**A green SQLite run is not a green CI run.** CI runs the suite against **Postgres**,
and the two disagree about things that matter: SQLite ignores foreign keys unless the
pragma is set, and aiosqlite tolerates a connection pool shared across two event loops
where asyncpg refuses. Both differences have shipped red CI on work that passed
locally — see `KNOWN-ISSUES.md` §4.9 and §4.10. Before pushing anything that touches
persistence or adds an endpoint test:

```bash
initdb -D /var/tmp/pgdata -A trust -U postgres          # once
pg_ctl -D /var/tmp/pgdata -o '-p 55432 -h 127.0.0.1' start
createdb -h 127.0.0.1 -p 55432 -U postgres studio_test
STUDIO_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/studio_test \
  apps/engine/.venv/bin/python -m pytest apps/engine/tests -q
```

Postgres refuses to run as root, so `su postgres -c '...'` each command if you are.
Seven minutes, and it is the only way to see what CI sees.

**Migrate from `apps/engine`, not from the repo root.** `database_url` defaults to
`sqlite+aiosqlite:///./storage/studio.db` — a *relative* path — and `npm start` runs
the engine with its working directory set to `apps/engine`. So the engine's database
is `apps/engine/storage/studio.db`, while

```bash
apps/engine/.venv/bin/python -m alembic -c apps/engine/alembic.ini upgrade head
```

run from the root silently creates and migrates a **second, empty** database at
`./storage/studio.db` and reports success. The app then 500s on the first query
touching anything the migration added, with a traceback that names a missing column
and nothing about which file it looked in. That command is what this file used to
recommend; it was wrong, and finding out cost a confused half hour.

`-c` is still needed if you do run it from the root — `alembic.ini` lives in
`apps/engine/`, and without it alembic exits with `No 'script_location' key found in
configuration`, which reads like a corrupt config rather than a wrong cwd. It is
just not sufficient.

On Windows the interpreter is at `.venv/Scripts/python` instead of `.venv/bin/python`.

The web app runs **without an engine and without any API keys** — start it alone and
every screen still renders, from `apps/web/lib/demo.ts`, so the design can be judged
before the plumbing exists. With the engine up, every screen reads it for real —
Series and New channel got their endpoints (`/v1/series`, the async launch flow) and
Analytics is wired per section, falling back to demo with a badge wherever the data
needs a connected channel to exist. `KNOWN-ISSUES.md` §5.5 has the per-screen
breakdown; if you change what a screen reads, that table is what needs updating.

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
