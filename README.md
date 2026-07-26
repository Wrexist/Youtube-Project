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

## Run it

```bash
./scripts/setup.sh
```

That is the whole setup: venv, both toolchains, `.env`, database schema, tests.
**No Docker needed** — the engine defaults to SQLite and runs renders in-process.
It finishes by listing anything still missing, which will be two API keys.
See [SETUP.md](SETUP.md).

```bash
npm run dev                                                              # :3000
apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080
```

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

Phases 0–10 are code-complete; 314 engine tests pass. **Neither Google API has been
exercised against a live account** — upload, captions and analytics are reviewed
code, not proven code. That needs OAuth credentials from a Google Cloud project.

Motion, crossfades and the music bed were verified by a real MoviePy render with
measurements, not by eye — see [KNOWN-ISSUES.md](KNOWN-ISSUES.md) §2. That render
used synthetic clips and tones; the pipeline has still not been run end to end
against live Pexels footage and edge-tts audio.

State survives a restart (Postgres), renders run in an arq worker, and the web app
reads live engine data with a labelled demo fallback. See [AUDIT.md](AUDIT.md) for
what was found, fixed and measured.
