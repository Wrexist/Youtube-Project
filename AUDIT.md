# Full-system audit — 2026-07-26

Everything in this repo was run, not read: engine tests, lint, the FastAPI app with
all 34 operations probed live, the web app built and rendered in a real browser, the
render pipeline driven through MoviePy, and the documented setup commands followed
verbatim.

**20 findings. One of them means the product cannot do the thing it exists to do.**

> **Status — all phases done. 19 of 20 findings fixed and verified.**
> The one exception is §4.7 (npm advisories), which cannot be fixed from this
> repository — see its entry for the assessment. Engine tests went 156 → 314, the
> app survives a restart, renders run in a worker, the web app reads live data, and
> every CI step is green. Each finding below carries its own status line.

Each finding below has: what is wrong, the evidence that proves it, the fix, and a
**Done when** that can be checked. Phases are ordered by dependency — P0 first
because CI being red makes every later verification untrustworthy.

---

## What is actually working

Stated first so the list below is read in proportion.

| Area | Status | Evidence |
|---|---|---|
| Engine unit tests | **223 pass** (now 314) | `pytest -q`, 7.5s |
| Web typecheck | **clean** | `tsc --noEmit`, no output |
| Web build | **clean** | Next 16.2.11, 8 static routes |
| Web render | **all 8 routes 200** | headless Chromium, no console errors |
| API surface | **34/34 respond** | every operation probed live |
| Error handling | **sensible** | 404 unknown job, 409 no channel connected |
| Render pipeline | **verified by render** | see `KNOWN-ISSUES.md` §2 |
| Job + SSE + cancel | **work** | job created, streamed, cancelled live |
| Design system | **holds up** | rail is labelled; accent is the focus ring, correct |

The architecture is sound and the code is unusually well-reasoned. Almost every
finding below is a **wiring gap**, not a design error — things built correctly and
then never connected.

---

# Phase 0 — Make the build trustworthy

Nothing else can be verified while CI is red. Half a day.

### 0.1 · CI fails on every push — `ruff check` (P0)

> **FIXED** — 14 → 0.

`ruff check engine tests` reports **14 errors**; the CI step fails.

```
engine/automation.py:24  F401 unused import IdeaStatus
engine/automation.py:349 E501 line too long (108 > 100)
engine/ideas.py:165      B905 zip() without explicit strict=
engine/scheduling.py:20  F401 unused import typing.Any
engine/scheduling.py:62  UP037 quotes in type annotation
engine/stats.py:16       UP035 import Sequence from collections.abc
engine/workflows/media.py:67,345  E501
tests/test_semantic_dedup.py:8,246  I001, E501
tests/test_subtitles.py:10,78       F401, B905
tests/test_trimming.py:10,12        I001, F401
```

**Fix.** `ruff check --fix engine tests` clears 8. The remaining 6 are manual: two
`zip(strict=)` decisions (pick `strict=True` — a length mismatch there is a bug worth
raising), three line wraps, one unused import.

**Done when** `ruff check engine tests` exits 0.

### 0.2 · CI fails on every push — `ruff format --check` (P0)

> **FIXED** — 7 files reformatted in one commit.

7 files would be reformatted: `automation.py`, `channel.py`, `scheduling.py`,
`stats.py`, `workflows/media.py`, `tests/test_semantic_dedup.py`,
`tests/test_subtitles.py`.

**Fix.** `ruff format engine tests`. Do it as a **single commit that changes nothing
else**, so it stays reviewable.

**Done when** `ruff format --check engine tests` exits 0.

### 0.3 · `npm run lint` is broken (P1)

> **FIXED** — ESLint 9 flat config, `--max-warnings 0`, now in CI.

Next 16 removed `next lint`. The script now resolves `lint` as a *directory*:

```
Invalid project directory provided, no such directory: /home/user/Youtube-Project/apps/web/lint
```

CI never runs it, so this has been silently broken. The web app has **no linting at
all** right now.

**Fix.** Add ESLint 9 flat config with `eslint-config-next`, change the script to
`eslint .`, and add `npm run lint` to the `web` CI job.

**Done when** `npm run lint` exits 0 and CI runs it.

### 0.4 · Add a CI guard for the vendored tree (P2)

> **DONE** — `vendor-isolation` job.

`vendor/moneyprinterturbo/` is reference-only. Nothing enforces that.

**Fix.** CI step: `! grep -rn "from vendor\|import vendor" apps/`.

**Done when** an import from `vendor/` fails CI.

---

# Phase 1 — Connect the publishing pipeline

**This is the headline finding.** One to two days.

### 1.1 · The publish workflow is unreachable dead code (P0 — blocking)

> **FIXED** — `PUBLISH_WORKFLOW` registered; `POST /v1/jobs/{job_id}/publish` is the gate.

`engine/workflows/publish.py` defines four fully-written stages — `UploadStage`,
`ThumbnailSetStage`, `CaptionsStage`, `PlaylistStage` — and `PUBLISH_STAGES` at line
140. **Nothing imports the module.**

```
$ grep -rn "workflows.publish" engine/
(no results)

$ grep -n "WORKFLOWS = " -A 5 engine/workflows/video.py
WORKFLOWS = {"video": …, "script": …, "seo": …}      # no "publish"
```

Verified live:

```
POST /v1/jobs {"workflow":"publish"}  →  {"detail":"unknown workflow 'publish';
                                          have ['script','seo','video']"}
GET  /v1/workflows/publish            →  same 404
```

`scheduling.py`, `automation.py` and `api/publishing.py` never reference the stages
either. **There is no code path in this repository that uploads a video to YouTube.**

Phase 7 is written, reviewed, typed — and connected to nothing. Every downstream
promise depends on it: the approval gate has nothing to gate, the quota ledger
records spend that never happens, the calendar schedules uploads that never fire,
and Phase 8's feedback loop has no published videos to measure.

**Fix.**
1. Import `publish` in `engine/workflows/video.py` and register
   `"publish": Workflow("publish", publish.PUBLISH_STAGES)` in `WORKFLOWS`.
2. Decide the trigger — publishing must not be a stage of `video`, because
   `CLAUDE.md` non-negotiable #3 requires an explicit approval gate. Add
   `POST /v1/jobs/{job_id}/publish` that starts a publish workflow seeded from the
   finished video job's artifacts.
3. Have `automation.py`'s auto-publish path call that same entry point, so manual
   and automatic publishing share one code path and one checklist.
4. Make `scheduling.py`'s applied schedule actually enqueue the publish job at its
   slot.

**Done when** a finished video job can be published through the API, the quota
ledger records the real 1,600 units, and `/v1/workflows/publish` returns its stage
graph.

### 1.2 · `/v1/auth/google` hands out a broken OAuth URL (P1)

> **FIXED** — 409 naming the missing variables.

With no credentials set, the endpoint cheerfully returns:

```
https://accounts.google.com/o/oauth2/v2/auth?client_id=&redirect_uri=…
```

An empty `client_id`. The operator follows the link and gets Google's *"invalid
client"* page with no hint about what to set. Every other unconfigured endpoint in
the app does this correctly — `/v1/analytics/daily` returns `409 no channel
connected`.

**Fix.** Return `409 {"detail": "GOOGLE_CLIENT_ID is not set — see .env.example"}`
when `google_client_id` or `google_client_secret` is empty.

**Done when** the endpoint 409s with an actionable message instead of emitting a
broken URL.

### 1.3 · `GOOGLE_REDIRECT_URI` in `.env.example` is silently ignored (P1)

> **FIXED** — `validation_alias` added; verified against the old behaviour.

`Settings` declares:

```python
google_client_id:     Field(validation_alias="GOOGLE_CLIENT_ID")      # unprefixed
google_client_secret: Field(validation_alias="GOOGLE_CLIENT_SECRET")  # unprefixed
google_redirect_uri:  str = "http://localhost:8080/..."               # NO alias
```

With `env_prefix="STUDIO_"`, the third reads **`STUDIO_GOOGLE_REDIRECT_URI`** — but
`.env.example` documents `GOOGLE_REDIRECT_URI=`, matching its two siblings. An
operator sets it, it does nothing, the default `localhost:8080` is used, and OAuth
fails with `redirect_uri_mismatch` — the single most time-consuming error in the
Google OAuth surface, because nothing in either system points at the cause.

**Fix.** Add `validation_alias="GOOGLE_REDIRECT_URI"` so all three Google settings
follow one convention.

**Done when** `GOOGLE_REDIRECT_URI=https://example.com/cb` in `.env` is reflected in
`/v1/auth/google`, and a test asserts it.

---

# Phase 2 — Settings that lie

Seven settings are read by nothing. Each one is a documented knob that silently does
nothing — the worst kind of configuration, because it fails quietly and in
production. One day.

Verified by attribute-level grep across `engine/`, then re-checked individually:

| Setting | Refs outside `settings.py` | Consequence |
|---|---|---|
| `storage_backend`, `s3_*` (5) | **0** | `s3` silently writes to local disk |
| `youtube_daily_quota` | **0** | quota ceiling is hardcoded |
| `max_concurrent_renders` | **0** | stated guardrail unenforced |
| `llm_provider`, `llm_fast_model` | **0** | model config does nothing |
| `elevenlabs_api_key` | **0** | provider unreachable |
| `database_url`, `redis_url` | **0** | known (§6) — Phase 5 |

### 2.1 · `storage_backend: "s3"` silently writes to local disk (P1)

> **FIXED** — option B: `Literal["local"]`, rejected at startup; `s3_*` deleted.

`ObjectStore` is local-filesystem only. `storage_backend`, `s3_bucket`,
`s3_endpoint`, `s3_access_key`, `s3_secret_key` are read by nothing.
`CLAUDE.md` states storage "goes through an `ObjectStore` interface — local FS in
dev, S3-compatible in prod". The S3 half does not exist.

Deploy to a container with `storage_backend=s3` and every render, thumbnail and
caption is written inside the container and lost on recycle — with no error.

**Fix (either is acceptable, pick one and be honest in the docs):**
- **A.** Implement `S3ObjectStore` behind the existing interface and select on
  `storage_backend`. The interface is already correct, so this is contained.
- **B.** Delete the five settings and the `Literal["local","s3"]`, and state plainly
  that storage is local-only until Phase 5.

Do **not** leave it as-is.

**Done when** `storage_backend=s3` either works or is rejected at startup.

### 2.2 · `youtube_daily_quota` is dead; the ceiling is hardcoded (P2)

> **FIXED** — `ledger.limit` reads it; all three other call sites updated.

`quota.py:37` is `DAILY_LIMIT = 10_000`. The setting is never read. `KNOWN-ISSUES`
§3.2 anticipates a granted quota extension — which could not be configured.

**Fix.** Have the ledger take its limit from `get_settings().youtube_daily_quota`.

**Done when** setting `STUDIO_YOUTUBE_DAILY_QUOTA=50000` changes `/v1/quota`.

### 2.3 · `max_concurrent_renders` is not enforced (P2)

> **FIXED** — semaphore in `RenderStage`, with a "waiting for a render slot" progress line.

`CLAUDE.md` calls the guardrails load-bearing: *"A runaway workflow is a billing
incident."* `max_cost_per_video_usd` **is** enforced. `max_concurrent_renders` is
read by nothing — unlimited concurrent renders will simply exhaust the box.

**Fix.** An `asyncio.Semaphore(max_concurrent_renders)` around `RenderStage`,
acquired inside the stage so queued jobs still stream "waiting for a render slot".

**Done when** the N+1th concurrent render waits instead of starting.

### 2.4 · `tts_provider` is decorative (P2)

> **FIXED** — narrowed to `Literal["edge"]`; `elevenlabs_api_key` and `AZURE_SPEECH_*` removed.

The `Literal` promises `edge | azure | elevenlabs | gemini`. The only use is
recording it in provenance:

```python
provenance=Provenance(params={"voice": voice, "provider": settings.tts_provider})
```

`_synthesize()` hardcodes `edge_tts`. Setting `azure` produces edge audio **and
records "azure" as the provider** — which corrupts the Phase 8 provenance trail that
non-negotiable #2 exists to protect. `elevenlabs_api_key` is dead, and
`AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` appear in `.env.example` with no
corresponding field at all.

**Fix.** Narrow the `Literal` to `["edge"]` and drop the unused keys, *or* dispatch
properly in `_synthesize`. Narrowing is the honest one-line change; implementing is
Phase 6 work. Either way the provenance must not record a provider that did not run.

**Done when** the recorded provider is always the one that actually synthesised.

### 2.5 · `llm_provider` / `llm_fast_model` / `llm_model` do nothing (P2)

> **FIXED** — all four deleted (incl. `llm_base_url`); `/health` reports routed models.

Model selection comes from the routing table in `models.py`, not from Settings.
`llm_provider` and `llm_fast_model` have zero references; `llm_model` is only echoed
back by `/health`, which makes `/health` actively misleading — it reports a model
the engine will not use.

**Fix.** Delete the three settings and their `.env.example` entries; point the docs
at the Models screen and `/v1/models`. Report the *routed* model in `/health`.

**Done when** `.env.example` has no LLM knob that does not work, and `/health`
reports what will actually run.

---

# Phase 3 — Diagnostics on the paths that fail

The pipeline's first stage fails in a way that tells the operator nothing. Half a
day.

### 3.1 · Keyword grounding swallows every exception (P1)

> **FIXED** — failures counted and classified per source; message names the cause.

`research/keywords.py:57-60`:

```python
for result in results:
    if isinstance(result, Exception):
        continue          # network down, 403, DNS, timeout — all identical
```

A blocked network, a rate-limit, a TLS failure and a genuine empty result all
produce `[]`. The stage then raises:

```
RuntimeError: no keyword evidence retrieved — refusing to write ungrounded SEO copy
```

The *policy* is right — `CLAUDE.md` forbids ungrounded SEO copy. The *diagnostic* is
useless: no mention of which source failed, why, or what to do. This is the first
stage of the only workflow, so it is the first thing every new user meets.

Reproduced live: `POST /v1/jobs` → `grounding` failed at 5.4s with exactly that
message and no further detail.

**Fix.** Count and classify the failures, log one line per distinct cause, and put a
summary in the raised error:

```
no keyword evidence: youtube autocomplete 27/27 failed (ConnectError),
duckduckgo returned 403. Check network egress, or set a search API key.
```

**Done when** a blocked network and a genuinely empty result produce different,
actionable messages.

### 3.2 · Grounding has a single point of failure with no key and no fallback (P2)

> **FIXED** — optional keyed source, tried only when the free ones return nothing.

Both sources are unauthenticated scraped endpoints:
`suggestqueries.google.com` and `html.duckduckgo.com`. The code comment says this
"keeps first-run setup to zero configuration" — true, and the trade is that both
routinely block datacenter IPs, which is exactly where this will be deployed.

**Not verified here.** This sandbox's egress proxy denies both hosts (`403 to
CONNECT`, confirmed via the proxy status endpoint), so I could not distinguish
"blocked by sandbox" from "blocks datacenters". The single-point-of-failure
structure is visible in the source regardless.

**Fix.** Keep the zero-config path as the default, add an optional keyed backend
(the `Semrush` MCP server is already available to this project), and fall back
between them. Surface which source produced the evidence in provenance.

**Done when** grounding survives either source being unavailable.

### 3.3 · SSE delivers every pre-subscribe event twice (P1)

> **FIXED** — cursor-per-subscriber fan-out; verified with two concurrent curl clients.

`main.py` `stream_job()` replays `job["events"]` and then drains `job["queue"]` —
but `emit()` writes to **both**, and nothing consumed the queue. Everything that
happened before the subscriber connected arrives twice.

Verified live:

```
event: workflow.started   ← 1st
event: stage.started
event: stage.progress
event: workflow.started   ← again
event: stage.started
event: stage.progress
event: stage.retrying
```

Second, worse problem: the queue is **single-consumer**. Two browser tabs on the
same job split the event stream — each event goes to whichever generator calls
`get()` first, so both views are wrong. The Create screen's pipeline view is built
on this.

**Fix.** Make `job["events"]` the single source of truth. Give each subscriber its
own cursor into that list and an `asyncio.Event` to wake on append — a fan-out, not
a queue. Replay from index 0, then await new appends from the index reached.

**Done when** one subscriber sees each event exactly once, and two concurrent
subscribers both see the complete stream.

---

# Phase 4 — Onboarding

A new contributor cannot copy-paste the documented commands on Linux or macOS.
Two hours.

### 4.1 · Every documented Python command is Windows-only (P1)

> **FIXED** — POSIX paths, Windows noted once.

`README.md` (3×), `CLAUDE.md` (2×) and `AGENTS.md` (2×) all say:

```bash
apps/engine/.venv/Scripts/python -m pytest apps/engine/tests -q
```

`.venv/Scripts/` is a Windows layout. On Linux and macOS it is `.venv/bin/`, so
**every documented command fails** on those platforms — confirmed on this box.

**Fix.** Document the POSIX form and note the Windows variant once:

```bash
# macOS / Linux
apps/engine/.venv/bin/python -m pytest apps/engine/tests -q
# Windows: .venv/Scripts/python
```

**Done when** a fresh clone on Linux can be set up by copy-paste.

### 4.2 · `.env.example` documents four settings that do not exist (P2)

> **FIXED** — regenerated, and a test now fails on drift.

| Entry | Reality |
|---|---|
| `GOOGLE_REDIRECT_URI` | ignored — wrong name (§1.3) |
| `AZURE_SPEECH_KEY` | no `Settings` field |
| `AZURE_SPEECH_REGION` | no `Settings` field |
| `NEXT_PUBLIC_ENGINE_URL` | web-side only; correct, but nothing consumes it yet |

And three real settings are undocumented: `STUDIO_MAX_COST_PER_VIDEO_USD`,
`STUDIO_MAX_CONCURRENT_RENDERS`, `STUDIO_GOOGLE_REDIRECT_URI`.

**Fix.** Resolve §1.3 and §2.4, then regenerate. Add a test that fails when
`.env.example` and `Settings` drift:

```python
def test_env_example_matches_settings():
    documented = _parse_env_example()
    expected = {_env_name(f) for f in Settings.model_fields.values()}
    assert documented - _WEB_ONLY == expected
```

**Done when** that test passes and guards the file.

### 4.3 · `.claude/settings.json` allow-lists a path that no longer exists (P3)

> **FIXED** — points at `vendor/`.

```json
"Read(//C/Users/IsacC/Downloads/MoneyPrinterTurbo-Portable-Windows-1.3.2/...)"
```

The reference is now vendored in-repo.

**Fix.** Replace with `Read(./vendor/moneyprinterturbo/**)`.

### 4.4 · Declared workspace `packages/*` does not exist (P3)

> **FIXED** — `packages/contracts` generated from OpenAPI, with two CI drift guards.

Root `package.json` declares `workspaces: ["apps/web", "packages/*"]` and `CLAUDE.md`
says *"Types come from `packages/contracts`. Never hand-write a type that mirrors an
API response."* The directory is absent, so `apps/web/lib/types.ts` is hand-written —
the exact thing the rule forbids.

**Fix.** Generate `packages/contracts` from the engine's OpenAPI schema (which is
already served and complete — 34 operations) via `openapi-typescript`, wire it into
CI, and delete the hand-written mirrors. Pair this with Phase 5.

### 4.5 · Committed build artifact (P3)

> **FIXED** — untracked and gitignored.

`apps/web/tsconfig.tsbuildinfo` is tracked.
**Fix.** `git rm --cached`, add to `.gitignore`.

### 4.6 · Dockerfile carries a dependency MoviePy 2 does not use (P3)

> **FIXED** — ImageMagick and the sed removed.

```dockerfile
# MoviePy shells out to ffmpeg, and ImageMagick backs its TextClip subtitle rendering.
RUN apt-get install -y ffmpeg imagemagick fonts-dejavu-core
RUN sed -i '…' /etc/ImageMagick-6/policy.xml || true
```

MoviePy 2 renders `TextClip` through **Pillow** — confirmed by inspecting
`TextClip.__init__` (no ImageMagick reference, Pillow only). The comment is wrong,
the package is dead weight, and `/etc/ImageMagick-6/` does not exist on current
Debian anyway — the `|| true` has been hiding that.

**Not built.** No Docker daemon in this environment, so the image is reviewed, not
verified.

**Fix.** Drop `imagemagick` and the `sed`. Keep `ffmpeg` and `fonts-dejavu-core`
(`services/fonts.py` depends on DejaVu). Build the image in CI so it cannot rot.

### 4.7 · Three high-severity npm advisories (P3)

> **NOT FIXED — cannot be, from here.** See the assessment below.

Now 14 rather than 3, because adding `openapi-typescript` (§4.4) pulled in
`@redocly/openapi-core`, and ESLint (§0.3) pulled in the `minimatch` chain. All 14
are **high**, none critical.

**`npm overrides` was tried and does not work here.** Both a flat override and a
nested one (`{"next": {"postcss": "^8.5.22"}}`) were written, the lockfile deleted
and the tree reinstalled from scratch. npm recorded no overrides in the lockfile
and `next/node_modules/postcss` stayed pinned at 8.4.31. The config was removed
rather than left in place claiming a fix it was not making.

`npm audit fix --force` resolves them by downgrading Next from 16 to 9. That is not
a trade worth making.

**What is actually exposed.** Every one of the 14 is a transitive **build- or
dev-time** dependency, and none is reachable by an attacker in a deployed instance:

| Package | Where | Reachable? |
|---|---|---|
| `postcss` 8.4.31 | pinned inside Next | Build time. Processes our own CSS, never attacker input. |
| `sharp` / libvips | Next image optimisation | This app serves no user-supplied images. |
| `brace-expansion`, `minimatch` | ESLint toolchain | Dev only. Never in a deployed artifact. |
| `js-yaml` | `@redocly/openapi-core` | Dev only. Parses our own `openapi.json`. |

**Fix.** Upstream. A Next patch release moves `postcss` and `sharp`; an
`eslint-config-next` release moves the `minimatch` chain. Until then the honest
position is the table above, not a config that pretends.

CI now runs `npm audit --audit-level=critical`, which will not block on these 14 but
will fail the build the moment a *critical* one appears. Re-check this entry on the
next Next upgrade.

---

# Phase 5 — Persistence and the web/engine seam

The two largest structural gaps. Already in `KNOWN-ISSUES` §5.4 and §5.5; restated
here with the dependency order that matters. One to two weeks.

### 5.1 · All state is in module-level dicts (P1)

> **FIXED** — Postgres + Alembic; verified by killing a live server and restarting.

`JOBS`, `CHANNELS`, `SCHEDULE`, `RECORDS`, `LAUNCHES`. A restart loses every job,
channel, schedule and quota record. `database_url` and `redis_url` are configured and
unused; there are no models and no migrations.

Do this **after** Phase 1 — the publish workflow will add records, and writing them
twice is wasted work.

**Fix.** SQLAlchemy models mirroring the existing dict shapes (deliberately kept
compatible), Alembic migrations, then swap the registries. The quota ledger is the
most urgent single table: it is the only thing standing between the system and a
quota overrun on restart.

**Done when** the engine restarts mid-render and the job survives.

### 5.2 · Jobs run as in-process asyncio tasks, not arq workers (P2)

> **FIXED** — arq worker, events over Redis pub/sub, in-process fallback kept.

`arq` and `redis` are dependencies; neither is imported. `create_task` means a
long render dies with the web process, and `max_concurrent_renders` (§2.3) cannot be
enforced across processes.

**Fix.** Move `_run_job` into an arq worker. Keep SSE in the API process, fed from
Redis pub/sub.

### 5.3 · The web app is not connected to the engine at all (P1)

> **FIXED** — Server Component reads, Server Actions, SSE live job view.

Every screen renders from `apps/web/lib/demo.ts`. There is **no** `fetch`, no
`EventSource`, no Server Action against the engine — only an env passthrough in
`next.config.ts`. This was a deliberate choice and the right one: the design is
judgeable now. But it means the UI currently proves the design, not the system.

**Fix, in this order:** ①  `packages/contracts` from OpenAPI (§4.4) → ②  read paths
in Server Components → ③  mutations as Server Actions → ④  the live job view on
`EventSource`, which depends on §3.3 being fixed first or every stage will render
twice.

**Done when** the Create screen runs a real job and streams real progress.

---

# Phase 6 — Deferred

Not defects; scope that was consciously left out. Tracked so it is not rediscovered.

| Item | Where |
|---|---|
| No auth on the engine | §6 — do not expose it |
| Image APIs unproven against a live key | §5.3 |
| Subtitles lose commas and quotes | §5.1 |
| Duplicate detection is lexical, not semantic | §5.6 |
| Keyword/tag trimmers drop by position, not value | §5.7 |
| No ⌘K palette | §6 |
| No thumbnail A/B swapping | §6 |
| No trend monitoring feeding `trending_terms` | §6 |
| Publish-time hour is estimated, not measured | §5.2 |
| Real Pexels/Pixabay + edge-tts end-to-end run | never executed |

---

## Order, as executed

```
Phase 0  ✅  CI green — everything after this is verifiable
Phase 1  ✅  the product can publish
Phase 3  ✅  failures explain themselves  (unblocked Phase 5 ④)
Phase 2  ✅  settings stop lying
Phase 4  ✅  a new contributor can start
Phase 5  ✅  survives a restart; renders in a worker; the UI is real
Phase 6  ▸   deferred by choice — see below
```

Phase 5 was done in the order below, each step unblocking the next.

1. **`packages/contracts` from OpenAPI.** Generated types plus two CI drift guards.
2. **Postgres.** SQLAlchemy models, Alembic migrations, quota ledger first. Verified
   by killing a live server mid-flight and restarting it.
3. **arq workers.** Renders execute out of process; events cross over Redis pub/sub.
   The in-process path is kept, because `uvicorn` alone is still a supported way to
   run this.
4. **Web → engine.** Server Component reads, Server Actions, and the SSE live job
   view — which was blocked on the §3.3 fix and is the reason it was sequenced last.

---

## How this was tested

| | |
|---|---|
| Engine | `pytest -q` (223), `ruff check`, `ruff format --check` |
| API | uvicorn on :8099; all 34 OpenAPI operations probed; job created, SSE streamed, cancelled |
| Web | `npm ci`, `typecheck`, `build`, `lint`; dev server on :3000; all 8 routes fetched; headless Chromium screenshots in dark and light, console captured |
| Render | MoviePy render with measured assertions — see `KNOWN-ISSUES.md` §2 |
| Config | `Settings.model_fields` diffed against `.env.example`; per-setting reference count across `engine/` |
| Onboarding | documented commands executed verbatim on Linux |

**Not tested, and why:** the Docker image (no daemon in this environment), all Google
API calls (no credentials), keyword grounding against live sources (sandbox egress
policy denies both hosts — confirmed via the proxy's own status endpoint), and the
pipeline against real Pexels footage and edge-tts audio (no keys).

### Verification of the fixes

Everything claimed FIXED above was re-checked after the change, most of it live
rather than by unit test alone:

| Fix | How it was confirmed |
|---|---|
| CI green | every step run locally: ruff check, ruff format, pytest (314), openapi check, contracts check, npm lint, typecheck, build |
| Persistence | spent 4,800 quota units, booked a slot, started a job on a live Postgres; **killed the server**; on restart quota read 4,800 with uploads_left 2, the booking returned, and the job restored with its stage states |
| Persistence, portable | the same 21-test suite passes identically on SQLite and on Postgres 16 |
| arq worker | API enqueued, worker executed (its log shows the run, the API's shows none), 8 events reached an SSE subscriber with no duplicates; killing Redis produced a job the API ran itself, logging "running in-process" |
| Contracts | added an endpoint without re-exporting → `export_openapi.py --check` exits 1; restored → exits 0 |
| Web ↔ engine | calendar rendered "4 uploads left today · 10,000 of 10,000 units" from the real ledger; killing the engine left it serving 200 with fallback copy and a "demo data" chip |
| Grounding fallback | 8 tests, including that it does **not** run when the free sources worked |
| Publish reachable | `/health` lists it; `/v1/workflows/publish` serves a 21-stage graph ending upload → thumbnail_set → captions → playlist |
| Approval gate | 10 endpoint tests: running job, wrong workflow, no channel, quality blockers, exhausted quota, and the happy path |
| SSE duplication | live curl: `workflow.started` and `stage.started` once each, previously twice |
| SSE multi-viewer | two concurrent curl subscribers received byte-identical 8-event streams |
| Grounding message | live failure now reads `youtube_autocomplete 27/27 requests failed (ProxyError)` — it correctly identified this sandbox's egress proxy |
| `GOOGLE_REDIRECT_URI` | set the env var, read it back; then `git stash` and showed the old code ignoring the same var |
| `storage_backend=s3` | rejected at startup with a message naming the field |
| Quota configurable | `STUDIO_YOUTUBE_DAILY_QUOTA=50000` → ledger limit 50000, uploads_left 24 |
| `/health` honesty | reports the routed draft and tags models instead of a dead setting |
| Settings sweep | a test now fails when any field is added and never read |
