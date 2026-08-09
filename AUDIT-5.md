# Audit 5 — after the first end-to-end video

Written after the first video actually rendered on a real machine, so unlike the
earlier audits this one is grounded in a run rather than in a reading. Every claim
below was checked against the code or against a log from that run; where something
is a judgement rather than a measurement, it says so.

The machine matters: Windows 11, AVG antivirus with HTTPS scanning on, no Redis, no
YouTube channel connected. That is an ordinary configuration and it broke four
separate things, none of which mentioned the cause.

---

## Part 1 — What the run exposed

### 1.1 One cause, four symptoms

AVG's "Web/Mail Shield" re-signs every TLS connection with a root it installs into
the Windows store. Browsers read that store; Python reads `certifi`, which has never
heard of it. So the OAuth callback 500'd, keyword research reported "no keyword
evidence", Pexels returned nothing, and voiceover would have died at stage nine —
four unrelated-looking failures, on a machine whose browser reached all four
services perfectly well.

Nothing in any of those errors said "certificate". `keywords._describe` collapsed the
exception to its class name, so the log read `ConnectError` and pointed nowhere.

**Fixed.** `engine/tls.py` uses the platform verifier. The lesson generalises: an
error message that names the exception type instead of the condition costs hours.

### 1.2 Failures that were reported as the wrong thing

Four in one run, all now fixed, listed because the shape recurs:

| Reported as | Actually |
|---|---|
| `duckduckgo: parsed 0 results` | HTTP 202 bot-challenge page — never searched |
| `no JSON found in response` | Valid JSON truncated at `max_tokens` |
| `no keyword evidence … check outbound network access` | Network fine; no channel connected |
| Wikipedia `403` | Our own User-Agent violated Wikimedia's policy |

Each one sent the reader to investigate something that was working. This is the
single most expensive class of defect in the codebase and it is worth a standing
rule: **a diagnostic must distinguish "it refused us" from "it had nothing".**

### 1.3 Silent wrongness

Worse than the above, because nothing appears in a log at all.

- `_extract_json` salvaged any `{…}` span out of a failed parse, so a truncated
  beats array returned **its first element** — a one-beat script, parsed cleanly.
- `RenderStage` wrote its artifact under `"video"` while every other reference used
  `"render"`, so `render_key` was `null` on every completed job and the Library
  could never link to a finished video.
- Stock search widened the query until *something* came back, so a beat about a
  subscriber counter was filled with footage of paper clips — a successful search
  for the wrong thing.
- The test suite read the developer's real `.env`, so `test_not_ready_when_there_is_no_footage_source`
  passed only on a machine with no keys. Saving a Pexels key would have started
  failing `Install Studio.cmd`.

All fixed. The pattern to watch for: **a fallback that always succeeds cannot be
distinguished from a success.**

### 1.4 Cost and time, measured

| | Value |
|---|---|
| First completed video (2:41 vertical) | **$1.02**, render **62 min** |
| Second run, interrupted at 16/17 | **$1.27** spent, render lost |
| Thumbnail stage (3 variants) | **$0.44** — 43% of the first video's cost |
| Script stage (Opus 5) | **$0.32** |
| Per-frame render cost, before → after | 78ms → **34ms** (2.3×) |

Two things stand out. **The thumbnail is the most expensive single stage**, at three
generations per video. And **the render dominates wall-clock by an order of
magnitude** — everything else together is about four minutes.

### 1.5 Durability

The interrupted run is the important finding. Redis was not reachable, so the render
ran *in-process* (`could not enqueue … running in-process`). When the engine
restarted, 16 completed stages and $1.27 of work were stranded behind a stage stuck
at `running` — which the UI will not let you expand, so "Re-run from here" was
unreachable. The job was unrecoverable through the product.

Half fixed: an interrupted stage is now marked failed and is actionable. The other
half is feature #1 below.

---

## Part 2 — Ten features, ranked

Ranked by **impact on a video getting made, watched and published, divided by
effort**, with risk as a tie-breaker. Numbers 1–4 are the ones I would actually do
next; 8–10 are real but can wait.

---

### 1. Run the worker by default, so renders survive a restart

`npm start` starts the API and the web app. It does not start `arq`, and nothing
starts Redis, so every render runs inside the API process and dies with it. That is
not a hypothetical — it happened, and cost 16 stages of completed work.

**Pros** — Renders survive a restart, a crash, and a code edit. It is the difference
between a 40-minute job being safe and being a gamble. It also fixes two things that
are currently silently dead: the weekly review cron never fires without a worker, and
`STUDIO_MAX_CONCURRENT_RENDERS` cannot bound anything in a single process.

**Cons** — Redis becomes part of the default path rather than optional, which is a
step away from "clone and run with no Docker". Either bundle a Redis, or detect its
absence and say plainly that renders are not durable. More processes in one console
window.

**Effort** — Small for the wiring, medium for doing the "no Redis" path honestly.

---

### 2. Show render progress, not just elapsed time

`compose.py` already reports fractions all the way through — `0.25 + beat`, `0.72
placing beats`, `0.75 burning subtitles`, `0.85 encoding`. The stage row shows a
stopwatch and nothing else, which is why "is this failed?" is a reasonable question
after 17 minutes.

**Pros** — Almost free; the data is already emitted and already streamed. Removes the
single most common source of doubt in the product. Makes a 40-minute stage feel
supervised rather than hung.

**Cons** — The fractions are coarse and beat-weighted, so the bar will not move
smoothly; a beat with heavy footage will sit still. Better than nothing, but it will
need a caveat or a smoothing pass.

**Effort** — Very small.

---

### 3. Edit a stage's output before the next one runs

`POST /v1/jobs/{id}/edit` exists. `editStage` exists in `actions.ts`. **No UI calls
either.** So the only way to change a hook you dislike is to re-run the model and
hope, at full cost, for something different.

**Pros** — The largest single lever on output quality, and most of the machinery is
built: the endpoint, the staleness rules that invalidate downstream stages, and the
expandable stage rows that already display the value. Turns the product from a slot
machine into an editor. Cheap in tokens — editing is free where re-rolling is not.

**Cons** — Needs a different editor per stage shape (prose for the draft, JSON for
beats, a picker for variants), which is real UI work. The invalidation cascade has to
be explained in the interface or an edit will feel destructive.

**Effort** — Medium.

---

### 4. Authenticate the engine

Anything that can reach `127.0.0.1:8080` can read and write every credential on the
machine through `PUT /v1/setup/keys`. CORS trusts `http://localhost:3000`, so any
*other* dev server on that port is a trusted origin. This is already recorded in
KNOWN-ISSUES §6 as the largest gap and it is still open.

**Pros** — Closes the one issue that makes the product unsafe to run anywhere but a
trusted single-user machine. Precondition for a hosted version, a LAN deployment, or
a shared box.

**Cons** — Friction in local development, where the current design deliberately has
none. Needs a token in both halves and a story for the SSE stream, which cannot send
custom headers from `EventSource` without a polyfill.

**Effort** — Medium. Low value *today* if this only ever runs on one laptop — which
is why it is fourth rather than first.

---

### 5. A visible cost ceiling per video

Cost is metered per stage and `budget_usd` is enforced, but nothing shows the ceiling
or how close a run is to it until a stage refuses to start. A thumbnail alone is
$0.44.

**Pros** — Cost is the constraint that decides whether this is usable at volume, and
right now it is invisible until it bites. A running total against a ceiling turns
model routing from a guess into a decision. Ties directly into the Models screen,
which already estimates $/month.

**Cons** — Needs a product decision about what happens at the ceiling: refuse, drop
optional stages, or ask. Getting that wrong is worse than not showing it.

**Effort** — Small to medium.

---

### 6. A real trend source for `freshness`

`score_idea` takes `trending_terms` and **nothing anywhere supplies it**, so
`freshness` is always zero and 15% of every idea score is dead weight.

**Pros** — Makes the scoring honest — a weight that is always zero is a lie in the
formula. Timing is a genuine driver of performance, and the decay curve is already
written and tested.

**Cons** — There is no free, reliable, official trends API. Google Trends is
unofficial and rate-limited; YouTube's trending feed is regional and broad. A noisy
source would be worse than an absent one, because it would look authoritative.

**Effort** — Medium, and the risk is in the source rather than the code.

---

### 7. Thumbnail A/B swapping

Three variants are generated at $0.44, one is published, the other two are discarded.
Phase 8's attribution is described as ready for this.

**Pros** — CTR is the highest-leverage measurable number on YouTube, and this is the
only feature here that produces evidence rather than opinion. The generation cost is
already being paid.

**Cons** — Needs a scheduler, quota per swap against a 10,000/day budget, and enough
views for a comparison to mean anything — on a small channel that can be weeks, and a
premature read is actively misleading. Easy to fool yourself with.

**Effort** — Medium. Real value only once there is traffic.

---

### 8. Cut Shorts from finished long-form

`GET /v1/analytics/shorts/{video_id}` already ranks the stretches worth clipping and
explains why. Nothing cuts them.

**Pros** — Multiplies output from work already paid for. The hard half — deciding
*which* thirty seconds — is done.

**Cons** — Two real blockers, both noted in KNOWN-ISSUES: `VideoRecord` carries no
path to the rendered master, so there is no file to cut from; and a 9:16 crop of 16:9
footage needs a subject to crop *around*. A centre crop of anything but a
talking head is bad, and this product is faceless by design.

**Effort** — Medium to large.

---

### 9. Series and recurring schedules

The Series screen makes **zero engine calls** — there are no endpoints behind it. Same
for New channel.

**Pros** — This is the "automation" half of the product's premise; without it Studio
is a very good one-video-at-a-time tool. Interacts well with the calendar and quota
ledger, which already exist.

**Cons** — An entire feature from scratch: scheduling, per-series defaults, the
auto-publish gate from CLAUDE.md #3, and quota arithmetic against ~6 uploads/day. The
approval-gate rule makes the genuinely useful version (unattended publishing) the
riskiest thing in the product.

**Effort** — Large.

---

### 10. ⌘K command palette

Specified in `docs/UI-DESIGN.md` as the escape hatch that lets every screen stay
sparse. Not built.

**Pros** — Cheap, self-contained, and it is the stated mechanism for adding
capability without adding buttons — which matters more with every feature above.

**Cons** — Changes nothing about what the product produces. Pure navigation
convenience on an app with ten screens and a left rail, where nothing is currently
hard to find.

**Effort** — Small. Last because the value is small too.

---

## Part 3 — Smaller things worth fixing

Not features; each is under an hour and each has bitten already.

- **Pixabay returns Cloudflare HTML with a 400.** Pexels covers for it, so a
  provider is silently dead. Detect and report it like the DuckDuckGo bot check.
- **The keyword seed keeps stopwords with no search value.** "how MrBeast became the
  biggest channel on YouTube" fell back to `mrbeast became`; `mrbeast youtube` would
  have been better. Widening the stopword list to common verbs is a one-liner.
- **`ChaptersStage` costs ~$0.01 per run and nothing reads its output.** Either
  append chapters to the description (with 5000-char guarding) or drop the stage.
- **Channel launches are generated and never persisted** — `save_launch` and
  `load_launches` both exist and nothing calls either.
- **`storage/tmp` keeps a full copy of every render.** 55MB per video, never cleaned.
- ~~**Secrets on Windows have no ACL**~~ — done, and it was not the theoretical
  finding it was filed as. `chmod(0o600)` is a no-op there, and the machine this was
  audited on had a sandbox tool granting a service group read on `Downloads` by
  inheritance, so `.env` was readable by two other local accounts. `secretfile.py`
  now sets an explicit DACL at both write sites. KNOWN-ISSUES §5.9.
- **No route declares its error responses.** Seven routers raise `HTTPException`
  with 404/409/502/503; the OpenAPI schema advertises only 200 and 422, so
  `packages/contracts` cannot type a failure. Worth fixing as one pass over every
  router — doing it for one endpoint would make that endpoint the odd one out.
- **`providers/llm.py` still opens `httpx.AsyncClient` inline in three places**, with
  no retry below the JSON-parse loop in `LLM.json` — so a connection reset fails the
  stage rather than being asked again. `providers/images.py` now has the wrapper
  CLAUDE.md's conventions ask for; lifting it into something both providers share is
  the actual fix, and it touches the most load-bearing module in the engine.

---

## Part 4 — What is genuinely good

Worth recording, because an audit that only lists faults gives a false picture.

- **The grounding refusal is right.** Refusing to write SEO copy without keyword
  evidence is the discipline that separates this from a slop generator, and it held
  under pressure — it failed the run rather than inventing keywords.
- **Provenance is real.** Every generated artifact records its model and prompt, and
  the retry loop folds discarded attempts into the cost rather than under-reporting.
- **The guards caught things during this very session.** `Workflow._validate` blocked
  a stage reorder that would have broken chapters; the servable-roots test forced an
  explicit decision about `broll/`; the stage-dependency test caught a comment
  claiming a dependency the code did not have.
- **The comments are unusually load-bearing.** Several bugs were diagnosed in minutes
  because a previous fix had written down *why* — the `NativeCommandError` note in
  `setup.ps1` and the `_SSL_CTX` note in `media.py` both paid for themselves here.
