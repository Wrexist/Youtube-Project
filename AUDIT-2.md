# Audit — 59 verified findings

A multi-agent audit run on 2026-07-28. Seven auditors, one per dimension of the
system, each finding then handed to a second agent told to **refute** it against the
real code. Only what survived that pass is here — 59 of a larger raw set.

| Severity | Count |
|---|---|
| Critical | 1 |
| High | 16 |
| Medium | 28 |
| Low | 14 |

By area: data 7, engine-api 11, engine-workflows 7, publishing 10, render 4, tests-docs 8, web 12

## Already fixed

Three were fixed immediately because they were either the worst one or were mine
from earlier the same day. Each was reproduced before being believed:

- **Timeline concatenated instead of placed on beat spans** (critical) — measured
  five seconds of black in a ten-second render, with beat 2 playing under beat 1's
  narration. Fixed in `compose.py`; re-measured clean.
- **`GET /v1/jobs` 500s on mixed naive/aware `created_at`** (high) — the state
  after any restart on SQLite. Introduced in this session; fixed at both ends.
- **`next.config.ts` `env` block froze `ENGINE_URL` at build time** (high) —
  which silently defeated the runtime docker-compose value, and with it the
  server-side wiring fix made earlier the same day.

---

## Phase 1 — Make a live run produce a correct video and a UI that reflects it

**Goal:** someone who clones the repo, starts the stack, and runs one job gets a correct MP4, a truthful screen, and an API that survives a restart.

Throughout, `PY = apps/engine/.venv/bin/python`.

### 1.1 Place footage on beat spans instead of concatenating it (`apps/engine/engine/render/compose.py`)
The single defect that makes the product's output wrong. `_beat_spans` returns `(start, end)` but `start` is discarded; segments are butted end-to-end by `concatenate_videoclips`, so any beat whose sourced footage is shorter than its span shifts every later beat earlier and leaves black under the tail of the narration (measured: 6s of black in a 10s render).

- In `_render_sync`, build each beat's clips into a group and lay it down with `.with_start(start)` from `_beat_spans`; composite all groups over an **opaque** base clip (`ColorClip(size, color=(0,0,0), duration=total)`) rather than relying on `CompositeVideoClip`'s transparent default.
- Fill the span exactly: when a beat's footage is shorter than `span`, loop or freeze-extend that beat's last clip until the span is covered. Replace the `continue` at line 93 (beat with zero clips) with a freeze of the previous beat's last frame.
- Keep `concat_padding` / `CrossFadeIn` semantics *inside* each group.
- Add `assert abs(video.duration - total) < 0.1` immediately before `write_videofile`.

**Verify:** `$PY -m pytest apps/engine/tests/test_trimming.py apps/engine/tests/test_effects.py -q`, then a targeted regression test: 10.0s narration, two 5s beats, one 2s source per beat → assert output duration ≈ 10.0 **and** sample frame luma at t=5.0/7.0/9.0 is non-black. That test is the deliverable of this step, not an optional extra — it is the only thing that stops this regressing.

### 1.2 Delete the `env` block in `apps/web/next.config.ts` (lines 5-7)
`env.ENGINE_URL` is folded in at build time by DefinePlugin, so the `ENGINE_URL: http://engine:8080` that `docker-compose.yml` sets on the web container is unreadable — every server-side read and Server Action in the only shipped deployment talks to `localhost:8080`. `apps/web/lib/engine.ts:47-52` already does the correct runtime lookup.

- Remove the three lines. Leave the Dockerfile's `NEXT_PUBLIC_ENGINE_URL` ARG alone (that one is genuinely build-time).
- Add a one-line `console.log` of the resolved `BASE` in `lib/engine.ts` module scope so a future fold is visible in the container log.

**Verify:** `rm -rf apps/web/.next && npm run build -w apps/web`, then `grep -rc "process.env" apps/web/.next/server/chunks/ssr/*.js` returns non-zero counts. Then `docker compose --profile full up -d` and confirm the web log prints `engine base: http://engine:8080`.

### 1.3 Normalise datetimes on load (`apps/engine/engine/repository.py`)
Two findings, one root cause: SQLite (the default per `settings.py:52`) returns `DateTime(timezone=True)` columns naive. After one restart plus one new job, `GET /v1/jobs` 500s on the sort tuple (`main.py:382`), and the first Google or Analytics call raises `TypeError: can't compare offset-naive and offset-aware datetimes` inside `Credentials.is_fresh` (`providers/youtube.py:58-63`).

- Add `def _aware(dt): return dt if (dt is None or dt.tzinfo) else dt.replace(tzinfo=UTC)`.
- Apply in `load_jobs` (repository.py:352-353) to `created_at`/`updated_at`, and in `load_channels` (repository.py:400-405) to `expires_at`. `load_schedule` (:436) and `QuotaLedger.load` (quota.py:200) already do this by hand — route them through the helper too.
- Belt and braces: make the sort key in `main.py:382` total-order-safe rather than trusting every producer.

**Verify:** new test in `apps/engine/tests/test_persistence.py` that writes a job and a channel, reloads, and asserts `tzinfo is not None`; plus a test putting one naive and one aware job in `JOBS` and asserting `GET /v1/jobs` is 200. Manually: start engine, create a job, restart, create another, `curl localhost:8080/v1/jobs`.

### 1.4 Keep the live YouTube client off `job["inputs"]` (`apps/engine/engine/main.py:495`, `apps/engine/engine/workflows/publish.py`)
`publish_job` puts a live `youtube.YouTube(creds)` into `inputs`, and `get_job` returns `job["inputs"]` verbatim — so `GET /v1/jobs/{id}` 500s on serialization for **every** publish job, and the payload is one annotation change away from leaking OAuth tokens.

- Store it as `JOBS[publish_id]["youtube_client"]` and have `UploadStage`/`ThumbnailSetStage`/`CaptionsStage`/`PlaylistStage` (publish.py:30, 73, 94, 118) read it from a dedicated context field instead of `ctx.inputs["youtube_client"]`.
- As a second line of defence, run `job["inputs"]` through a promoted-public `repository.jsonable()` in `get_job`.

**Verify:** test asserting `GET /v1/jobs/{publish_id}` is 200 and the response body contains neither the access nor the refresh token substring. Depends on: nothing. Blocks: 1.5 (same file region).

### 1.5 Reject `workflow="publish"` at job creation (`apps/engine/engine/main.py:84`, `apps/engine/engine/workflows/video.py`)
`POST /v1/jobs {"workflow":"publish"}` currently returns 202, runs the full paid render, then dies on a bare `KeyError: 'youtube_client'` (`UploadStage` has `max_attempts = 1`). `/health` advertises it.

- Add `STARTABLE = {"video", "script", "seo"}` in `workflows/video.py`; in `create_job` raise `HTTPException(400, f"workflow must be one of {sorted(STARTABLE)}")` for anything outside it; report `sorted(video.STARTABLE)` from `/health` (main.py:128). `video.WORKFLOWS["publish"]` stays reachable only from `publish_job`.

**Verify:** `curl -X POST localhost:8080/v1/jobs -d '{"topic":"x","workflow":"publish"}'` → 400 naming the allowed set; `curl localhost:8080/health` no longer lists `publish`.

### 1.6 Terminate the SSE stream properly (`apps/engine/engine/main.py:428`, `apps/web/lib/use-job-stream.ts`, `packages/contracts/src/index.ts`)
`stream_job` returns with no terminal frame; a server-closed SSE stream is a *reconnect* signal for `EventSource`, so a finished job replays the whole log every ~3s, re-dispatching `workflow.started` and flipping the Publish button's enabled state on a loop.

- Engine: before returning, `yield {"event": "stream.closed", "data": _json({"type": "stream.closed", "status": job["status"]})}`.
- Contracts: add `stream.closed` to the `JobEvent` union in `packages/contracts/src/index.ts:82-92`.
- Client: register a listener for it in `useJobStream` that calls `source.close()`.

**Verify:** run a short job in a browser, watch the Network tab — the `events` request completes once and does not reopen; Publish stays enabled.

### 1.7 Fix the cost chip and the stage rows (`apps/web/lib/use-job-stream.ts`, `packages/contracts/src/index.ts`)
Three trivial edits, all visible on the Create screen and one of them a stated non-negotiable (CLAUDE.md #5, always-visible per-video cost):

- Add `summary?: string` and `elapsed_ms?: number` to `JobEvent` in `packages/contracts/src/index.ts`.
- `use-job-stream.ts:74` → `patch({ status: "done", summary: event.summary ?? null, cost_usd: event.cost_usd ?? 0, elapsed_ms: event.elapsed_ms ?? 0 })`.
- Same case, return `{ ...state, stages, cost_usd: state.cost_usd + (event.cost_usd ?? 0) }` so the header total accumulates during the run; keep `workflow.completed`'s assignment as the authoritative reconciliation, and set `cost_usd` from the accumulated total on `workflow.failed` too.
- In `app/page.tsx`'s `emptyStages()` (lines 187-196), **strip `variants` and `detail`** when spreading `DEMO_JOB.stages` — right now a live job renders demo variants and demo detail in the expanded stage as if they were its own output.

**Verify:** start a job; cost chip climbs stage by stage; done rows show a real summary and a duration; expand a stage on a live job and confirm no demo variant text.

### 1.8 Don't lose the job id on navigation (`apps/web/app/page.tsx:26`, `:129-131`)
`jobId` lives in `useState` only, there is no `app/jobs/[id]` route, and the screen prints "This job keeps running if you close the tab. Progress is restored on return." — which is false.

- `router.replace(\`?job=${id}\`)` on successful start; read it back with `useSearchParams` on mount and seed `jobId`.
- If this slips, delete the sentence at lines 129-131 in the same commit rather than leaving it standing.

**Verify:** start a job, navigate to /calendar and back via the browser Back button, confirm the pipeline reattaches and streams.

### 1.9 An unaffordable *optional* stage must not fail the job (`apps/engine/engine/workflows/base.py:298-313`)
The pre-flight budget check raises `BudgetExceeded` regardless of `stage.optional`, while the retry-exhaustion path at :407 correctly honours it. `ThumbnailStage` is optional and last, so an unaffordable thumbnail marks the job `failed`, and `POST /v1/jobs/{id}/publish` refuses anything not `completed` — a finished, stored MP4 becomes unpublishable.

- Branch on `stage.optional`: set `state.status = StageStatus.SKIPPED`, put the budget message in `state.error`, emit `stage.skipped` with the reason, `continue`. Raise only for required stages.

**Verify:** `$PY -m pytest apps/engine/tests/test_workflow.py -q` plus a new test: budget of $0.01, workflow ending in an optional stage → job status `completed`, last stage `skipped`.

### 1.10 `AudienceProfile(weekday=...)` does not exist (`apps/engine/engine/providers/analytics.py:181`, `apps/engine/engine/api/insights.py:102`)
`GET /v1/analytics/audience` raises on every request — `TypeError` in the constructor, `AttributeError` in the reader.

- `analytics.py:181` → `AudienceProfile(daily=weekday)`; `insights.py:102` → `profile.daily`.
- Do **not** set `is_measured=True`: the hourly curve is still the heuristic, and `source="measured_weekday_only"` with `is_measured=False` is the intended labelling per KNOWN-ISSUES §5.2.

**Verify:** new test in `apps/engine/tests/test_insights.py` calling `audience_profile()` against a stubbed 30-row payload.

**Effort: 3-4 days.** 1.1 is ~1.5 days of that (the render rework plus its regression test); everything else is hours. 1.2-1.10 are independent of each other and can go in any order.

---

## Phase 2 — Stop money, quota, and job state going wrong

**Goal:** spend is counted, quota is booked, a publish can't run twice, and nothing accumulates unboundedly.

### 2.1 Book `videos.insert` quota when the session opens, not when it succeeds (`apps/engine/engine/providers/youtube.py:283-285`)
The 1,600-unit record sits inside `if resp.status_code in (200, 201)`. A crash mid-upload, a 4xx, or a hang books nothing — while Google has already charged it. `_call` (:171) deliberately records *before* checking status; the upload path is the outlier.

- Move the `await ledger.record("videos.insert", ...)` to immediately after the session-open POST returns 2xx (line 258), passing `note=title[:60]` at that call site. **Delete** the record at 283 — do not leave both, that double-counts.

**Verify:** test that a 500 from the upload PUT still leaves `ledger.spent()` at 1600 for the day.

### 2.2 Bound the upload retry loop and fix 308 offset handling (`apps/engine/engine/providers/youtube.py:288-300`)
Two defects in the same six lines. On 5xx/429 the code `continue`s with no sleep and no cap, re-sending the same chunk forever against a `timeout=None` client with `max_attempts = 1` above it — an unbreakable hang plus API hammering. And `offset = ... if rng else offset + len(chunk)` advances a full chunk when Google omits `Range` on a 308, which it does when it has committed zero bytes — producing a discontiguous `Content-Range` and a corrupt upload.

- Per-offset attempt counter, reset on forward progress. On a retryable status: `await asyncio.sleep(min(2 ** attempts, 60) + random.uniform(0, 1))`, honour `Retry-After` on 429, raise `YouTubeError` past ~6 attempts.
- Only advance `offset` on an explicit `Range` header. With no `Range` (and before any resend), issue a status query — `PUT session_url`, `Content-Range: bytes */{size}`, empty body — and resume from the server's confirmed offset.

**Verify:** `respx`-mocked test: 503 → 308-without-Range → 200 sequence completes with the correct byte ranges and a bounded number of requests; a permanent 503 raises `YouTubeError` rather than hanging. Depends on 2.1 (same function).

### 2.3 Populate `Credentials.channel_id` (`apps/engine/engine/api/publishing.py:141-146`, `apps/engine/engine/api/channels.py:174`)
`exchange_code` never sets it, so it round-trips as `""`: `channels.update` PUTs a blank id, and **every ledger row is written with `channel_id=""`**, killing per-channel quota attribution.

- Add `YouTube.my_channel_id()` wrapping `channels.list?part=id&mine=true` (1 unit); call it in `finish_auth` after `exchange_code`, set `creds.channel_id` before `save_channel`.
- Guard `apply` in channels.py with a 409 if `channel_id` is still empty rather than PUTting a blank id.

**Verify:** mocked OAuth test asserting the saved channel row has a non-empty `channel_id`; `apply` on a channel with an empty id returns 409.

### 2.4 Use a real Pacific timezone for the quota day (`apps/engine/engine/quota.py:44`, `apps/engine/engine/scheduling.py:228,309`)
`PACIFIC = timezone(timedelta(hours=-8))` is a fixed offset, so for the ~8 months of PDT the reset boundary is an hour off Google's — spend in the 07:00-08:00 UTC hour is booked to the wrong day, exactly the "mysterious quotaExceeded" failure the module docstring warns about.

- `from zoneinfo import ZoneInfo; PACIFIC = ZoneInfo("America/Los_Angeles")`. Add `tzdata` to `pyproject.toml` for slim containers and Windows.
- Free rider while you're here: `scheduling.py:228` and `:309` index `usage_by_day` with `at.date()` while the ledger keys it by `quota_day()`. Use `quota_day(slot.at)` / `quota_day(at)`. Keep the `MAX_PUBLISHES_PER_DAY` per-day counter on a separately named variable — it is a different calendar question.

**Verify:** regression test asserting `quota_day()` for `2026-07-15T07:30Z` (PDT) and `2026-01-15T07:30Z` (PST) land on different local dates.

### 2.5 Charge retried LLM attempts (`apps/engine/engine/providers/llm.py:216-271`, `apps/engine/engine/workflows/base.py:357-409`)
`LLM.json` loops full `complete()` calls on a JSON parse failure and returns only the successful one, so discarded attempts are never charged and the per-video budget ceiling under-counts real spend.

- In `LLM.json`, accumulate `input_tokens`/`output_tokens` across attempts and return a `Completion` carrying the totals, so `StageOutput.cost_usd` reflects everything spent.
- In `Workflow._run_stage`, keep a per-stage accumulator so a stage that fails partway still contributes its spend to `spent_usd`.
- Skip the "re-check the budget between retries" suggestion — marginal value, extra branching in the hot path.

**Verify:** test with a transport that returns unparseable JSON once then valid JSON, asserting the returned `cost_usd` is the sum of both completions.

### 2.6 Delete temp files after they are copied into the store (`apps/engine/engine/render/compose.py:45`, `apps/engine/engine/workflows/media.py:382`)
`store.put_file` is `shutil.copy2`, and nothing in the engine ever unlinks — `storage/tmp` keeps a full duplicate of every render and every narration track forever.

- Add `async def move_file(self, source, key)` to the `ObjectStore` interface using `shutil.move`; call it from `RenderStage._render` for the mp4 and `VoiceoverStage.run` for the mp3. (Minimal alternative: wrap each `put_file` in `try/finally: Path(output_path).unlink(missing_ok=True)`.)
- Note the mp3 filename is a sha1 of the script text; if you want dedup-on-identical-text back later, key it off the store, not off `tmp`.

**Verify:** run a job end to end, assert `storage/tmp` is empty afterwards and `storage/renders/` has the file.

### 2.7 Make the publish gate idempotent (`apps/engine/engine/main.py:436-506`)
Nothing checks for an existing publish job for the same source; the source job stays `completed` and the web Publish button re-enables, so a second click uploads the video to YouTube twice at 1,600 quota units each.

- Before creating the publish job: `if any(j.get("inputs", {}).get("source_job_id") == job_id and j["status"] in ("running", "completed") for j in JOBS.values()): raise HTTPException(409, "already published; pass force=true to retry")`, with a `?force=true` escape for a genuine retry after a failed publish.
- Web side: once a publish job id comes back, disable the Publish button in `app/page.tsx:97` regardless of `stream.status`.

**Verify:** test that two consecutive `POST /v1/jobs/{id}/publish` calls return 202 then 409, and that 409 flips to 202 with `?force=true` after the first is marked failed.

### 2.8 Make cancel actually cancel (`apps/engine/engine/worker.py:172`, `apps/engine/engine/main.py:569-573`)
`cancel_job` cancels only the local `_relay` coroutine; the worker keeps rendering and its `finally` overwrites `cancelled` with `completed`. And in the in-process path, cancel sets the status without `_wake`, so **every SSE subscriber hangs open forever** and the DB row still says `running` (relabelled `interrupted` on restart).

- Enqueue with `_job_id=job_id`, record `job["enqueued"] = True` in `_dispatch`, set `allow_abort_jobs = True` on `WorkerSettings`, and have `cancel_job` open a pool and `arq.jobs.Job(job_id, pool).abort()` for enqueued jobs. Check the abort flag between stages so a long render stops mid-run.
- In `cancel_job`, after setting the status: append a terminal event to `job["events"]`, call `_wake(job)`, `await _persist(job)`. **The `_wake` is the load-bearing line** — without it the generator's `while True` never re-evaluates the status.

**Verify:** `$PY -m pytest apps/engine/tests/test_sse_stream.py -q` plus a new test that attaches a subscriber, cancels, and asserts the generator terminates within a second and the persisted row reads `cancelled`. Depends on 1.6 (share the terminal-frame mechanism).

### 2.9 Declare the script→SEO dependency (`apps/engine/engine/workflows/video.py`)
`TitlesStage`/`DescriptionStage` read `revision`/`draft` but declare `depends_on = ('grounding',)`, so editing the script re-runs the voiceover, render and thumbnail while shipping the **old** title and description to YouTube verbatim.

- Do **not** edit the shared stage classes — `Workflow('seo', SEO_STAGES)` raises at import if `TitlesStage` depends on `revision` (verified). Define video-workflow-only subclasses inside `_video_stages()`: `_Titles(seo.TitlesStage)` with `depends_on = ('grounding','draft','revision')`, `_Description` with `('titles','grounding','draft','revision')`. Include both `draft` and `revision` since `RevisionStage` is skippable. `tags` is covered transitively.

**Verify:** `$PY -c "from engine.workflows import video; print(video.VIDEO_WORKFLOW.dependents_of('revision'))"` now includes `titles`, `description`, `tags`. Add that as a test.

### 2.10 Fix the weak-script blocker wiring (`apps/engine/engine/main.py:533`, `apps/engine/engine/automation.py:37,288`)
`getattr(critique, "severity", 0)` on a dict is always 0, so the `weak_script` publish blocker can never fire. The existing test passes because it constructs `VideoState` directly.

- `critique_severity=int((critique or {}).get("severity", 0) or 0)`.
- Reconcile the scale: the prompt asks for 1-5 (`script.py:354`), the threshold is 5, and the message says "/10". Set `_WEAK_SCRIPT_THRESHOLD = 4` and correct the message text.
- Add a test that drives `publish_blockers` through `_video_state` with a real dict critique.

**Verify:** `$PY -m pytest apps/engine/tests/test_automation.py apps/engine/tests/test_publish_endpoint.py -q`.

**Effort: 3-4 days.** 2.1→2.2→2.3 are one sitting in `youtube.py` and should be done together. 2.8 depends on 1.6. Everything else is independent.

---

## Phase 3 — Wire the dead UI

**Goal:** no button on screen lies, and no fabricated number ships unlabelled.

### 3.0 One-pass honesty fix (do this first, it is 30 minutes)
Add `<LiveBadge live={false} />` to the Header meta of `app/analytics/page.tsx`, `app/queue/page.tsx`, `app/library/page.tsx`, `app/series/page.tsx`, `app/models/page.tsx`, `app/new-channel/page.tsx`, exactly as `app/calendar/page.tsx:30` does. **Delete** any button below that is not wired in this phase rather than shipping it inert. This buys correctness while the wiring lands, and unblocks the doc fixes in Phase 5.

**Verify:** every screen shows either live data or a demo-data chip.

### 3.1 Persist calendar scheduling (`apps/web/components/calendar.tsx`, `apps/web/app/calendar/page.tsx`, `apps/web/app/actions.ts`)
`useState<Scheduled[]>([])` at calendar.tsx:42 is the entire store; `drop()`, `unschedule()` and `applyPlan()` call only `setScheduled`. `applyPlan` prints "N videos scheduled" having sent nothing. Meanwhile `GET /v1/calendar` returns `scheduled` and `calendar/page.tsx:19` destructures only `quota_by_day`, so already-booked uploads are invisible and can be double-booked against the daily ceiling.

- Pass `calendar.scheduled` down as the initial state.
- Add three Server Actions in `app/actions.ts` wrapping `POST /v1/calendar/schedule`, `DELETE /v1/calendar/schedule/{video_id}`, `POST /v1/calendar/auto/apply`. Call them from `drop`/`unschedule`/`applyPlan` with optimistic update and revert-plus-notice on failure — `POST /calendar/schedule` 409s with a reason string the existing notice element can render verbatim.
- While in this file, fix the hydration mismatch: pass a reference instant down from the Server Component as an ISO string prop and use it in the `weeks` useMemo (lines 55-67) and the `past` computation (line 227) instead of `new Date()`. Prefer this over the null-then-`useEffect` option, which costs a skeleton frame on every load.
- Accessibility (docs/UI-DESIGN.md:122 makes this non-optional): `tabIndex={0}` + button semantics + Enter on tray cards to select, `tabIndex={0}` + Enter on day cells to call `drop(day)`, `aria-label` per cell with date and remaining budget, Escape to clear, outcome announced through the existing `role="status"` region at line 330.

**Verify:** schedule a video, hard-refresh, it is still there; `curl localhost:8080/v1/calendar` shows the slot. Tab to a tray card and place it with the keyboard alone. Depends on Phase 4.2 (the endpoint should validate before you point a UI at it) — or land 4.2 first if you prefer.

### 3.2 Wire the Models screen (`apps/web/app/models/page.tsx`)
`routes` is seeded from demo data and the three mutations call only `setRoutes`; `GET /v1/models` is never read, so the screen does not display the routing actually in force, and the header quotes a `~$X/month` computed from state that was never sent.

- Read `GET /v1/models` in a Server Component wrapper, pass tasks + catalogue as props, fall back to `MODEL_CATALOGUE`/`MODEL_TASKS` behind a `LiveBadge`.
- Server Actions for the three mutations — note the verbs: **`PUT /v1/models/route`** (models.py:77), **`PUT /v1/models/route/all`** (:87), `POST /v1/models/route/reset` (:103).
- Surface a save-failed message rather than keeping optimistic state.

**Verify:** change one task's model, restart the engine, the change is still shown; `apps/engine/routing.json` reflects it.

### 3.3 Wire the Queue screen (`apps/web/app/queue/page.tsx`)
Every button — "Approve N clear", "Open"/"Fix", "Approve", "Retry from here" — is rendered with no handler, and both this file and `components/ui.tsx` are Server Components, so adding `onClick` is a function-serialization failure, not a one-line edit.

- Keep the async Server Component for the read; add `getJobs(status?)` to `lib/engine.ts` and the contracts alias `export type Jobs = Ok<paths["/v1/jobs"]["get"]>` in `packages/contracts/src/index.ts` (match the existing pattern at :38-48, don't reach into `components["schemas"]`).
- Move the card list into a `"use client"` child that calls the existing `publish` action from `app/actions.ts:66` — it already unwraps the 409 blockers correctly. Render `result.blockers` as a list under the card the way `app/page.tsx:112-123` does.
- This is the screen CLAUDE.md non-negotiable #3 hangs on; a queue with an inert Approve button is worse than no queue.

**Verify:** run a job to completion, open /queue, approve it, watch the publish job appear. Depends on 1.3 (`GET /v1/jobs` 500s until the datetime fix lands) and 2.7 (idempotency, before you give people a second Approve button).

### 3.4 Wire "Re-run from here" and the variant picker (`apps/web/components/pipeline.tsx`, `apps/web/app/page.tsx:104`)
`onRerun` is `console.log("re-run from", name)`. `VariantPicker` (pipeline.tsx:140) takes no `onChoose`. `POST /v1/jobs/{id}/edit` exists, does exactly this, and its docstring calls it "the interaction the Create screen is built around". FIX-TASKS.md:109 already lists this as a done-when criterion that was never met.

- Add `editStage(jobId, stage, value)` to `lib/engine.ts` and an `editJob` Server Action; wire both `onRerun` and a new `onChoose` prop to it.
- The endpoint 409s while the job is running — gate both controls on a terminal `stream.status`. Same gating reason to finally wire the exported-but-uncalled `stopJob` (`actions.ts:50`) to a Stop control.

**Verify:** complete a job, pick a different title variant, confirm description/tags/render re-run and the earlier stages do not. Depends on 2.9 (without the dependency fix, re-running from the script leaves the SEO stale) and 4.1 (edit payload validation).

### 3.5 Render the stream error and offer reconnect (`apps/web/app/page.tsx`)
`stream.error` is produced by `use-job-stream.ts:150-155` and read by nothing; a dead stream freezes the pipeline on its skeleton with Publish permanently disabled, and the effect depends only on `[jobId]` so nothing can reopen a CLOSED `EventSource` without a reload.

- Render `stream.error` near the existing error block at page.tsx:125 (that block currently shows a *different*, local error from `startJob`/`publish`).
- Add a Reconnect button that bumps a counter included in `useJobStream`'s effect deps.

**Verify:** start a job, kill the engine, confirm the message appears; restart the engine, click Reconnect, the stream resumes without losing the job id.

**Effort: 5-7 days.** 3.0 first (30 min). 3.3 blocked on 1.3 and 2.7; 3.4 blocked on 2.9 and 4.1; 3.1 pairs with 4.2.

---

## Phase 4 — Input validation and error surfaces

**Goal:** malformed input gets a 422 naming the field, and expected failures get the right status code instead of a 500.

### 4.1 Scope the edit endpoint instead of building a typed-parser system (`apps/engine/engine/workflows/base.py:228-243`, `apps/engine/engine/main.py:600`)
`mark_edited` writes `new_value` (typed `Any`) straight over dataclass stage outputs with no validation. A plausible JSON payload for `titles` makes `DescriptionStage` raise `AttributeError` on `variants[0].text`, the retry loop burns three attempts, the job fails, and `encode_value` persists the plain dict so the corruption survives restart with no route back.

- **Don't** build the full per-stage `parse_edit` hook the finding proposes. Instead: whitelist the stages whose values are plain `str`/`list[str]`/`dict` and accept edits only for those, raising `WorkflowError` (→ existing HTTP 400) otherwise; report `editable: false` in `_serialize_stages` for every other stage so the UI never offers an edit it will reject.
- Revisit the typed hook only if operators actually need to hand-edit dataclass stages. Right now `GET /v1/jobs/{id}` exposes only `summarize()` strings, so a caller cannot even learn the expected shape — the hook would be building a door to a room with no floor.

**Verify:** `POST /v1/jobs/{id}/edit` with a dict payload for `titles` returns 400 with a message naming the stage; a string edit to `draft` succeeds and invalidates downstream. Blocks 3.4.

### 4.2 Pydantic models for the calendar endpoints (`apps/engine/engine/api/publishing.py:98,274`)
`AutoScheduleRequest.videos` is `list[dict]` and `apply_plan` takes `body: dict`; four ordinary malformed payloads reproduce as 500s. Worse, `apply_plan` calls `repository.save_slot` inside the loop, so a bad entry at position 3 leaves entries 1-2 booked — and it never calls `validate_move`, so it persists times in the past and double-books slots that `schedule_one` twenty lines above would 409.

- `PendingVideo(id: str, title: str = "", format: Literal["short","long"] = "short", ready_at: datetime | None = None)` for `AutoScheduleRequest.videos`; `ApplyRequest(assignments: list[Assignment])` with `Assignment(video_id: str, at: datetime)`.
- Validate the **whole** list — including `validate_move(at, existing=[t for vid, t in SCHEDULE.items() if vid != video_id])` per assignment — before writing the first slot. Return `{"applied": n, "skipped": [{video_id, reason}]}` rather than silently applying everything.
- Same file: `days: int = Query(14, ge=1, le=90)` on `GET /v1/calendar/slots` (:189). It is currently unbounded and synchronous — `days=5000` builds 120k slots in 0.75s on this machine, so `days=100000` blocks the event loop for tens of seconds, stalling every SSE stream and in-process render relay. The endpoint returns at most 40 slots, so a longer horizon is meaningless.

**Verify:** the four probes from the audit return 422 naming the field; `curl 'localhost:8080/v1/calendar/slots?days=100000'` returns 422 immediately. Pairs with 3.1.

### 4.3 Register the two exception handlers (`apps/engine/engine/main.py`)
`QuotaExceeded` and `ChannelDisconnected` are raised in the provider layer and caught nowhere — both surface as opaque 500s with no indication that re-auth is needed.

- `@app.exception_handler(QuotaExceeded)` → 429 with operation, cost and remaining. `@app.exception_handler(ChannelDisconnected)` → 409 with a reconnect prompt pointing at `/v1/auth/google`.
- Reorder `schedule_one` (publishing.py:211-217) to call `reschedule` **before** persisting to `SCHEDULE` and the DB — currently a failure there leaves the local schedule diverged from YouTube.
- Deriving `list_channels`' hardcoded `"connected": True` (publishing.py:155) from a persisted flag is a good follow-up but is a bigger change than the handlers; note it and move on.

**Verify:** test that a `QuotaExceeded` raised from a route returns 429 with the operation name.

### 4.4 Wrap the insights endpoints (`apps/engine/engine/api/insights.py`)
`Analytics._query` raises bare `RuntimeError` on any ≥400 and `refresh()` raises `ChannelDisconnected`; neither is wrapped in `daily`/`retention`/`audience`/`refresh_insights`.

- `ChannelDisconnected` → 409 "channel disconnected — reconnect at /v1/auth/google"; `RuntimeError`/`YouTubeError` from `_query` → 502 with the operation name and Google's status code but **not** its raw body.
- `days: int = Query(28, ge=1, le=365)` on `daily` (:61).

Much of this is subsumed by 4.3's handlers once `_query` raises a typed error instead of `RuntimeError` — do that conversion rather than four try/excepts.

### 4.5 Record the in-process failure message (`apps/engine/engine/main.py:278-284`)
`_run_job`'s `except WorkflowError` branch never sets `job["error"]`, so `GET /v1/jobs` reports `error: null` for every in-process failure (including budget aborts, since `BudgetExceeded` subclasses `WorkflowError`). The worker path already does this correctly.

- Set `job["error"] = str(exc)` in both handlers, and clear any stale value at the top of `_run_job` so a resumed-and-succeeded job doesn't keep the old message.

**Verify:** fail a job with no Redis running, `curl localhost:8080/v1/jobs` shows the reason.

### 4.6 Add the per-day publish cap to `validate_move` (`apps/engine/engine/scheduling.py`)
`MAX_PUBLISHES_PER_DAY = 3` is commented "Hard cap: more than this many uploads in one day harms the channel" but is enforced only in `auto_schedule` (:229), so manual drags are unbounded.

- Pass the count of publishes already scheduled for the target day into `validate_move` and hard-block at the cap, so one constant is enforced on both paths.
- **Skip** the projected-spend comparison the finding also proposes — see the "not worth fixing" list.

**Verify:** four manual drags onto the same day → the fourth 409s with the cap in the message.

**Effort: 2-3 days.** 4.1 blocks 3.4; 4.2 pairs with 3.1; 4.3 should land before 4.4.

---

## Phase 5 — Tests and documentation that currently overstate the truth

**Goal:** the suite tests what the docs claim it tests, and the docs describe the app that exists.

### 5.1 Stop stubbing `engine/providers/llm.py` for the entire suite (`apps/engine/tests/conftest.py:35-42`)
The conftest installs a `types.ModuleType` stub before collection, so the real 331-line module — four transports, the JSON-retry loop, `_extract_json` — is **never imported by any test**, while KNOWN-ISSUES §1.3 and SETUP.md both claim it is unit-tested. (`test_models.py` covers `engine/models.py`: the routing table and cost model only.)

- Narrow or drop the stub — the real module imports and constructs fine with no API key set (the anthropic SDK import is inside the transport function).
- Add tests for `_extract_json` (bare / fenced / embedded in prose / none), for `LLM.json`'s retry-with-error-fed-back loop and attempt arithmetic, and for the four transports via `respx`.
- Correct KNOWN-ISSUES §1.3 and SETUP.md: the transports and the `format: json` constraint are unexecuted and untested; only the routing table and cost model are covered.

**Verify:** `$PY -m pytest apps/engine/tests -q` stays green with the stub removed. Depends on 2.5 (test the accumulated cost while you are writing these).

### 5.2 Make the dedup tests assert something (`apps/engine/tests/test_semantic_dedup.py:50-92`)
`find_duplicate_async` returns `(None, score, "Jaccard")` on the no-duplicate path, so `assert "Jaccard" in method` holds in both branches, and `test_best_candidate_returned` puts its only assertion inside `if dup:`.

- Line 50 test: `assert dup == "why bridges collapse" and score >= DUPLICATE_THRESHOLD` (similarity 0.667 vs threshold 0.45).
- Line 75 test: `assert dup is not None`.
- Line 85 test: drop the `if dup:` guard, `assert dup == "cat grooming tips"` (0.5 vs 0.2).

### 5.3 Stamp `alembic_version` when the schema is created from metadata (`apps/engine/engine/db.py:43-57`)
`ensure_schema()` runs `create_all` without stamping, so the documented `alembic upgrade head` dies with `table channel_launches already exists` on any machine that has started the app once. Reproduced.

- In the `if not existing` branch only, create `alembic_version` and insert the head revision id (read via `ScriptDirectory.from_config(Config("apps/engine/alembic.ini")).get_current_head()`) in the same `engine().begin()` connection. Do **not** stamp in the partial `if existing` branch, where a real revision may genuinely be pending.
- `command.stamp` inside `conn.run_sync` opens its own connection unless you set `cfg.attributes["connection"]` — prefer the direct insert.

**Verify:** fresh DB → start app → `$PY -m alembic -c apps/engine/alembic.ini upgrade head` exits 0.

### 5.4 Add `alembic check` to CI (`.github/workflows/ci.yml:47-52`)
CI proves the migration *applies*; nothing proves it still matches `tables.py`. The boot-time `ensure_schema()` only creates absent **tables**, so a new column on an existing table is silently missing in production.

- Add `alembic check` immediately after the upgrade against the same `migrations_check` database. Verified exit 0 against the repo today, so it will not start red.
- Soften the step comment, which currently claims a completeness guarantee the upgrade alone does not give.

### 5.5 Don't swallow test failures in setup (`scripts/setup.sh:120`, `scripts/setup.ps1:147`)
`set -euo pipefail` plus a piped pytest means any test failure aborts the script silently before the doctor step, printing nothing — while SETUP.md:34-36 and README.md:22 promise the run finishes by naming what is missing. The PowerShell version prints "Setup complete." regardless.

- bash: `set +e; (cd apps/engine && STUDIO_PERSIST=false "$ROOT/$VENV_BIN/python" -m pytest -q 2>&1 | tail -3); TESTS=${PIPESTATUS[0]}; set -e`, warn if non-zero, continue to the doctor.
- PowerShell: read `$LASTEXITCODE` right after the pytest pipeline and print the same warning.

### 5.6 Platform-independent secret scan (`apps/engine/tests/test_settings_are_wired.py:209-213`)
`subprocess.run(["grep", ...])` with no `shutil.which` guard, on a documented first-class Windows path. Replace with `ENGINE.rglob("*.py")` + `re.search`, matching `test_nothing_reads_os_environ_directly` twenty lines above.

### 5.7 Correct the claims in README / AUDIT / KNOWN-ISSUES
Do this **after** Phase 3, so you are describing the finished state rather than chasing it.

- README.md:91-92, AUDIT.md:12 and the KNOWN-ISSUES.md:9 header all say "the web app reads live engine data with a labelled demo fallback"; reconcile with KNOWN-ISSUES §5.5, which says the opposite ("Every screen renders from demo.ts"). Name what is actually live after Phase 3.
- Replace the "314 engine tests" figure at README.md:82, KNOWN-ISSUES.md:14 and :76, AUDIT.md:12, :28 and :677 (the real count today is 431) with **the command that produces it** rather than a new number that will go stale in a week.
- CLAUDE.md Commands: `apps/engine/.venv/bin/python -m alembic -c apps/engine/alembic.ini upgrade head` — the documented form fails with "No 'script_location' key found" because `alembic.ini` lives in `apps/engine/`.

**Effort: 2-3 days**, of which 5.1 is over half.

---

## Not worth fixing

**Chapters (seo.py) — delete the stage, don't plumb it.** The output is generated, billed, and read by nothing; YouTube only renders chapters from description timestamps. Plumbing it properly means either appending the block to the description in `UploadStage` (with 5000-char guarding) or reordering the graph into `titles → chapters → description`, which drags `subtitles` into the SEO chain. That is real design work for a feature nobody has asked for, on a stage that costs ~$0.01 per run. Delete `ChaptersStage` and `SeoPackage.chapters` (which is never constructed anywhere) and reopen it as a feature request. If you do keep it, add `'subtitles'` to its `depends_on` — it currently reads `ctx.get('subtitles')` while declaring `('titles',)`.

**Analytics metering into the quota ledger (`providers/analytics.py`).** The Analytics API has a separate, far larger quota pool. Recording it into `COSTS` risks the ledger's `spent()`/`remaining()` wrongly blocking uploads, and doing it *correctly* means building a second pool — real work for a breakdown panel nobody reads. **Do the one-line fix instead: correct the module docstring**, which currently claims these calls "are still recorded in the ledger for visibility" and is affirmatively false about its own code. Note the CLAUDE.md #5 exception in KNOWN-ISSUES so it doesn't get re-flagged.

**`validate_move`'s "dead" quota branch.** The finding is literally right that `usage_by_day` never contains a future date, so the branch can't fire — but that is correct semantics, not a bug: a future day genuinely has no spend and quota resets daily. The projected-spend comparison proposed alongside it adds a speculative model of tomorrow's usage to guard a case that `ledger.can_afford` at `main.py:472` already refuses and explains at publish time. Take only the per-day cap (step 4.6) and drop the rest.

**The full `parse_edit` typed-hook system.** See 4.1 — the whitelist plus `editable: false` closes the corruption hole for a tenth of the effort, and building typed edit parsers for dataclass stages is premature while `GET /v1/jobs/{id}` exposes only summary strings and no caller can discover the expected shape.

**The 0.4s subtitle-overlap clamp and the Whisper cue-splitting bypass, as standalone tasks.** Both are real. The overlap is cosmetic, brief, and needs a sub-0.4s cue (rarer than the finding's example suggests). The Whisper path is fallback-only — `_synthesize` requests WordBoundary explicitly, so it fires only if a voice emits no boundary events at all. **Fold both into step 1.1** since you are already in `compose.py`: clamp the padded end against the next cue's start, and add the `min(int(height * 0.72), height - clip.h - margin)` position clamp so no future long cue can leave the frame. If 1.1 is running long, the cue splitter (`cues = _split_sentence_cues(await compose.transcribe(...))` in `media.py`) is the half worth keeping — it is one line.

**`quota_day` vs `at.date()` in scheduling, as its own ticket.** Inert today: `usage_by_day` seeds only the last 28 Pacific days, so future slots read 0 regardless of which calendar you index with. It rides along free with the ZoneInfo change in 2.4 — don't schedule it separately.

**Persisting channel launches (`api/channels.py`).** Genuinely broken — `save_launch`/`load_launches` have no application call site, and `repository.py:3` and AUDIT.md §5.1 both falsely claim otherwise. But `load_launches` returns a flattened dict that doesn't match the mirror shape `channels.py` reads (it needs `states`, `events`, `inputs`), so this is not "call the function that exists" — it is a loader rewrite. The loss is a regenerable LLM artifact, on a flow whose manual-channel-creation step is a documented gap anyway. **Cheapest honest resolution: delete `save_launch`, `load_launches`, the `ChannelLaunch` table and the AUDIT.md claim.** Revisit if launches ever become expensive enough to be worth persisting.