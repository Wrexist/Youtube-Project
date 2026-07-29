# Studio

Idea → researched script → narrated, subtitled, rendered video → grounded SEO package
→ scheduled to YouTube → measured → fed back into the next video.

- [SETUP.md](SETUP.md) — **start here.** One command, then two API keys.
- [PLAN.md](PLAN.md) — phases and what is built
- [CLAUDE.md](CLAUDE.md) — architecture, conventions, hard API limits
- [docs/UI-DESIGN.md](docs/UI-DESIGN.md) — design system and screens
- [KNOWN-ISSUES.md](KNOWN-ISSUES.md) — what's unverified, broken, or needs a human
- [AUDIT.md](AUDIT.md) — **full-system audit, 20 findings, phased plan with fixes**
- [FIX-TASKS.md](FIX-TASKS.md) — paste-able agent prompts to fix all of it

## What you need

Two pieces of software, and two free API keys. That is the whole list.

| | | |
|---|---|---|
| **Python 3.11+** | runs the render engine | <https://www.python.org/downloads/> |
| **Node.js 20+** | runs the web app | <https://nodejs.org> |

On Windows the installer offers to fetch both for you via `winget`; on
macOS and Linux it prints the one command for your package manager. **Nothing
else is required** — no Docker, no database server, no ffmpeg install (one ships
with the engine's dependencies). Postgres and Redis are optional upgrades, not
prerequisites.

The two keys — one model provider, one stock-footage provider — are asked for
inside the app on first run, and both are free to obtain. See
[SETUP.md](SETUP.md).

## Run it

**Windows — no terminal needed.** Double-click these, in this order:

| | |
|---|---|
| **`Install Studio.cmd`** | Once. Installs everything, a couple of minutes. |
| **`Studio.cmd`** | Every time after. Or the **Studio** shortcut it puts on your Desktop. |

**macOS / Linux**

```bash
./scripts/setup.sh
```

Then open **Studio** from Applications (macOS) or the Studio launcher on your
Desktop (Linux). `npm start` does the same thing from a terminal, on any platform.

Either way your browser opens by itself once the app is ready. On a fresh install
it lands on a short setup flow that asks for your keys — it says what each one
unlocks, links to where to get it, and turns green when you have enough to make a
video. No file to edit, nothing to restart.

If something else on your machine already holds port 3000 or 8080 — another dev
server, usually — Studio moves to the next free pair and prints where it went. It
does not stop and ask you to fix it. Double-clicking the launcher while Studio is
already running just brings the browser back to it.

Setup does the whole install: venv, both toolchains, `.env`, database schema,
tests, and the desktop launcher. **No Docker needed** — the engine defaults to
SQLite and runs renders in-process.

Two keys is the whole list: one LLM provider, one stock-footage provider. Both are
free to obtain and together take about five minutes. Publishing to YouTube needs a
Google OAuth client on top of that; everything up to a finished MP4 does not.
See [SETUP.md](SETUP.md).

Check what is configured at any time:

```bash
apps/engine/.venv/bin/python apps/engine/scripts/doctor.py
```

Upgrades, both optional: `docker compose up -d` for Postgres and Redis, then
`apps/engine/.venv/bin/python -m arq engine.worker.WorkerSettings` to run renders
in a worker so restarting the API cannot kill one. Or
`docker compose --profile full up -d` for the whole stack in containers.

On Windows the interpreter is at `.venv/Scripts/python`.

## Layout

```
apps/web/      Next.js 16 · Tailwind 4 · dark and light
apps/engine/   FastAPI · arq workers · owns generation, rendering, publishing
docs/          design spec
vendor/        MoneyPrinterTurbo, read-only reference. Never imported.
```

## The parts that carry the weight

| | |
|---|---|
| [workflows/base.py](apps/engine/engine/workflows/base.py) | Stage framework: resume, staleness propagation, enforced provenance, budget ceiling |
| [workflows/script.py](apps/engine/engine/workflows/script.py) | research → angle → hook → beats → draft → critique → revision |
| [workflows/seo.py](apps/engine/engine/workflows/seo.py) | Keyword-grounded titles with deterministic scoring in code, not in the prompt |
| [scheduling.py](apps/engine/engine/scheduling.py) | Publish-time optimisation against audience, spacing, cadence and quota |
| [quota.py](apps/engine/engine/quota.py) | The 10,000 units/day ceiling, recorded rather than estimated |
| [stats.py](apps/engine/engine/stats.py) | Welch's t-test, so the feedback loop never learns from noise |
| [insights.py](apps/engine/engine/insights.py) | Metrics attributed back to the title strategy and hook device that produced them |
| [automation.py](apps/engine/engine/automation.py) | Series cadence, approval gates, spend ceilings |
| [ideas.py](apps/engine/engine/ideas.py) | Backlog scoring and duplicate detection |
| [services/stock.py](apps/engine/engine/services/stock.py) | Pexels then Pixabay, orientation enforced, no clip reused across a video |
| [services/effects.py](apps/engine/engine/services/effects.py) | Ken Burns and fades — what stops stock footage looking like stock footage |
| [services/bgm.py](apps/engine/engine/services/bgm.py) | Music bed, off by default because nothing here ships licensed music |

## Three things worth knowing before relying on it

1. **YouTube's Data API allows ~6 uploads a day.** An upload costs 1,600 of 10,000
   daily units. Every scheduling decision in the system is downstream of that.
2. **Nothing publishes unattended** unless a series has auto-publish enabled *and*
   the video passes a checklist — sources cited, SEO grounded, thumbnail present.
   Auto-publish skips the waiting, not the checks.
3. **The feedback loop refuses to learn from small samples.** A finding needs 8+
   videos per group, p<0.05 and ≥8% lift before it may change a prompt. Everything
   else is displayed and withheld.

## Status

Phases 0–10 are code-complete. The test count is deliberately not written down here
— every figure this file has carried went stale within a week. Ask the suite:

```bash
apps/engine/.venv/bin/python -m pytest apps/engine/tests -q | tail -1
```

**Neither Google API has been exercised against a live account** — upload, captions
and analytics are reviewed code, not proven code. That needs OAuth credentials from
a Google Cloud project.

Motion, crossfades and the music bed were verified by a real MoviePy render with
measurements, not by eye — see [KNOWN-ISSUES.md](KNOWN-ISSUES.md) §2. That render
used synthetic clips and tones; the pipeline has still not been run end to end
against live Pexels footage and edge-tts audio.

State survives a restart (Postgres) and renders run in an arq worker.

The web app is **seven of ten screens live, three not wired at all** — a distinction
"reads live engine data with a labelled demo fallback" glossed over, so it is worth
stating plainly:

| | |
|---|---|
| Create, Queue, Library, Models | live, with `lib/demo.ts` as the fallback when the engine is unreachable |
| Setup, Welcome | live only — they refuse to fake it and say the engine is down |
| Calendar | quota and bookings live; the video tray it drags from is always demo |
| Analytics, Series, New channel | demo only, **no network call** — the endpoints behind them do not exist yet |

Every screen showing fixtures carries a "demo data" badge, and Calendar declines to
persist a drag it cannot save rather than appearing to succeed. See
[AUDIT.md](AUDIT.md) for what was found, fixed and measured.
