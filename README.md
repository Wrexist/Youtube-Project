# Studio

Idea → researched script → narrated, subtitled, rendered video → grounded SEO package
→ scheduled to YouTube → measured → fed back into the next video.

- [PLAN.md](PLAN.md) — phases and what is built
- [CLAUDE.md](CLAUDE.md) — architecture, conventions, hard API limits
- [docs/UI-DESIGN.md](docs/UI-DESIGN.md) — design system and screens
- [KNOWN-ISSUES.md](KNOWN-ISSUES.md) — what's unverified, broken, or needs a human
- [FIX-TASKS.md](FIX-TASKS.md) — paste-able agent prompts to fix all of it

## Run it

The web app runs on demo data with **no engine and no API keys**. That is
deliberate — a design you can't look at is a design you can't judge.

```bash
npm install
npm run dev
```

Engine and infrastructure:

```bash
docker compose up -d
```

```bash
cd apps/engine && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
```

```bash
apps/engine/.venv/Scripts/python -m uvicorn engine.main:app --reload --port 8080
```

```bash
apps/engine/.venv/Scripts/python -m pytest apps/engine/tests -q
```

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

Phases 0–10 are code-complete; 217 engine tests pass. **Neither Google API has been
exercised against a live account** — upload, captions and analytics are reviewed
code, not proven code. That needs OAuth credentials from a Google Cloud project.

The render additions above (motion, fades, music, Pixabay) have unit tests but have
not been through a real MoviePy render. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md) §2.
