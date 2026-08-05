# Known issues

What is unverified, what is knowingly incomplete, and what will need a human.
Ordered by how likely it is to bite you.

> **A full-system audit on 2026-07-26 found 20 issues this file did not list.**
> **19 are now fixed.** Publishing is wired up and gated, CI is green, SSE no longer
> duplicates events, every setting either works or is gone, state survives a restart,
> renders run in a worker, and most of the web app reads live data (seven of ten
> screens — see §5.5, which is the honest version). The exception is the npm
> advisories, which need an upstream Next release — see [AUDIT.md](AUDIT.md) §4.7 for
> what each actually exposes. Several entries below are now out of date; AUDIT.md is
> the current record.

**Web builds, lints and typechecks clean.** The engine test count is not recorded
here on purpose — it went stale every time it was written down. Run
`apps/engine/.venv/bin/python -m pytest apps/engine/tests -q | tail -1`.

One unrelated fix came with it: `stats.two_tailed_p` fell back to `math.betainc`,
which **no released CPython has**. `scipy` was not declared anywhere, so a clean
`pip install -e ".[dev]"` produced 17 failures in `test_insights.py` and CI was red.
`scipy` is now a real dependency and the dead fallback is gone. See 4.6.

---

## 1. Blocking — nothing works end to end without these

### 1.1 No Google credentials → no publishing, no analytics
**Status:** every Google API call in this repo is unexecuted code.

`videos.insert`, `captions.insert`, `thumbnails.set`, the OAuth exchange, the whole
Analytics API client — reviewed, typed, and never once run against Google. The
resumable-upload chunk loop and the `308 Resume Incomplete` handling are the parts
most likely to be subtly wrong, because they are the parts that cannot be reasoned
about without a real response.

**To fix:** create a Google Cloud project, enable *YouTube Data API v3* and *YouTube
Analytics API*, create an OAuth 2.0 Client ID (Desktop or Web), put the id and secret
in `.env`. Approval can take days, so start it before you need it.

**Until then:** everything up to and including the render works; nothing publishes.

### 1.2 No `ANTHROPIC_API_KEY` → no script, no SEO
Every generation stage needs a model. Either set a key, or route everything to Ollama
on the Models screen (see 1.3). Without one of the two, the pipeline fails at the
first stage.

### 1.3 No LLM provider has been called for real
Unit coverage is now honest — the `conftest.py` stub that hid the whole of
`engine.providers.llm` from every test is gone, and `tests/test_llm.py` exercises
`_extract_json`, the JSON-retry loop's attempt arithmetic and error feedback, and all
four transports against mocked HTTP. What that proves is the request shape and the
response parsing.

What it does **not** prove: that any real provider accepts those requests. No
Anthropic, OpenAI-compatible, Gemini or Ollama endpoint has been called from this
repository. A wrong header name, a renamed usage field or a rejected parameter would
pass the suite and fail on first contact.

`probe_ollama` and `register_ollama` remain the two to distrust most — both are
mock-tested only, and `register_ollama` writes to the routing table.

**To fix:** `ollama serve`, `ollama pull qwen2.5:14b`, then
`POST /v1/models/ollama/register` and `POST /v1/models/test`.

### 1.4 No stock provider key → no footage
`MaterialsStage` raises immediately unless `PEXELS_API_KEY` or `PIXABAY_API_KEY`
is set. Both are free and instant. Pexels is searched first and Pixabay fills
whatever it could not — with only one key you get one shot per beat, and a beat
with no footage is a hole in the video.

Pixabay has no orientation parameter, so its results are filtered on the returned
dimensions. Upstream compares width only, which is how a landscape clip ends up
in a portrait render with the subject cropped out of frame.

---

## 2. Verified working — so you know where the floor is

These were actually executed on this machine, not assumed:

- **Render pipeline.** A real 480×854 MP4 was written with two scaled-and-cropped
  clips and two composited subtitle overlays. Fixed two real bugs to get there — see
  4.1.
- **Edge TTS + subtitle cues.** Real audio, real word-boundary timings, correctly
  grouped into readable lines. Fixed a real bug — see 4.2.
- **FastAPI app imports** with all routes registered.
- **The unit suite**, covering the workflow framework, scheduling, quota arithmetic,
  statistics, attribution, automation, model routing, the LLM client's transports and
  JSON-retry loop, stock-provider response parsing, Ken Burns ramps, font resolution
  and BGM path safety.
- **A real render of the new features**, measured rather than eyeballed:
  - 1080×1920 MP4, 9.04s against 9.0s of narration — the crossfade overlap does
    not shorten the timeline.
  - Ken Burns: mean frame delta 46 over 1.5s, against 16 for the same source with
    motion off. The zoom is doing something beyond the source's own movement.
  - BGM: 220 Hz bed measurable under a 440 Hz narration, still present at 6s from
    a 3s file (so the loop works), and absent when disabled.
  - Crossfade: seam luma 117 against 121 mid-clip — it blends, it does not blink.
  - A landscape source in a portrait render fills all four edges. No letterboxing.
  - 16:9 and 1:1 render at the right dimensions; thumbnail is 1280×720 and 12 KB.

**Still not executed:** every Google API call (1.1), and the pipeline end to end
with real Pexels/Pixabay footage and real edge-tts audio. The render was driven
with synthetic clips and tones.

---

## 3. Needs a human — no API exists

### 3.1 Creating a YouTube channel
**There is no `channels.insert`.** A channel can only be created through the YouTube
UI. No amount of engineering changes this, and any tool claiming otherwise is lying.

The launcher designs the entire identity — name, handle, About text, keywords,
visual direction, series, 30 de-duplicated video ideas — and validates it against the
real limits. Then you do five things by hand (they are listed on the New channel
screen and in `engine/channel.py:MANUAL_STEPS`):

1. Create the channel (use a **Brand Account** — ownership can be transferred later)
2. Claim the handle (first-come; do this first)
3. Set the channel name — **not settable via the Data API, ever**
4. Verify by phone — unlocks custom thumbnails and 15+ minute videos
5. Connect via OAuth

After step 5, `POST /v1/channels/launch/apply` pushes the description, keywords and
country. Name, handle, avatar and banner stay manual permanently.

### 3.2 Quota extension
Default quota is 10,000 units/day; an upload costs 1,600. That is **~6 uploads/day**,
and roughly 4 once thumbnails and captions are counted. More requires an audited
application to Google that takes weeks.

### 3.2b Analytics calls are not metered — the one exception to CLAUDE.md #5
"Cost is tracked per video" holds for every provider call except the YouTube
Analytics API, which has its own far larger quota pool. Recording it into the same
ledger would make `spent()`/`remaining()` refuse uploads there is budget for, and
modelling it properly means a second pool — real work for a breakdown panel nobody
reads. Recorded here so it stops being re-flagged as a bug: it is a decision, not an
oversight. `providers/analytics.py` used to claim in its own docstring that these
calls *were* recorded; that claim is gone.

### 3.2c edge-tts ignores the standard TLS and proxy environment variables
**Status:** worked around, but the workaround reaches into a private global.

Every other outbound call in this repo goes through `httpx`, which reads
`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and the proxy variables. edge-tts does not: it
reaches Azure over a WebSocket and verifies against a module-level context built
from `certifi` at import, which it passes explicitly to `ws_connect(ssl=...)`.
Behind a TLS-inspecting proxy — every corporate network, most CI runners — it is the
only provider that fails, with `CERTIFICATE_VERIFY_FAILED`, at **stage 9 of 17**,
after the research and the entire script chain have been paid for.

`_trust_extra_cas()` in `workflows/media.py` loads the configured bundle into that
context (additive — certifi's roots stay, nothing is disabled) and `_tts_proxy()`
passes the proxy edge-tts never reads for itself. `scripts/doctor.py` reports which
roots are in use.

The fragile part is `edge_tts.communicate._SSL_CTX`, a private name in someone
else's package. If upstream renames it the code logs a warning and carries on, which
is correct on every network that does not need the bundle. A first attempt at this
fix passed edge-tts a `TCPConnector` carrying our context; it looked right and did
nothing, because the explicit `ssl=` argument to `ws_connect` wins. The regression
test pins the context, not the connector.

### 3.2d Scraped pages reach the model as untrusted input
**Status:** fenced, not solved.

`ResearchStage` builds a script from pages the search backend returned, and a page
that ranks for the topic can write anything. Until now the digest was interpolated
into the prompt raw — no delimiter, no instruction, no size cap — and from there it
shaped the script, the title, the description and a published video.

`engine/untrusted.py:fence()` strips invisible control characters, defuses role
markers and closing tags, and caps the length; the prompt says in words that the
block is data and never instructions. `research/web.py` refuses private and
link-local addresses, checks the post-redirect host, and streams with a 2MB ceiling
instead of materialising `resp.text`.

None of that makes a model immune to persuasion. It removes the cheap version of the
attack. A model that summarises a page arguing for something will still reflect that
argument — which is what summarising is.

### 3.3 Music licensing
Nothing ships with licensed music. MoneyPrinterTurbo's bundled `resource/songs` has
unclear provenance and is deliberately **not** carried over — it is excluded from the
vendored snapshot too. Do not publish anything scored with it.

The mixing code exists (`engine/services/bgm.py`): looped, faded out over the last
three seconds, mixed under the narration at `STUDIO_BGM_VOLUME`. It is **off by
default** and the music directory is **empty by default**. Drop tracks you have the
right to publish into `./storage/bgm` and set `STUDIO_BGM_ENABLED=true`.

The mix is unverified against a real render — see 6, "not executed".

---

## 4. Bugs found and fixed (recorded so they aren't reintroduced)

### 4.1 MoviePy 2.x API
Written against 1.x; 2.1.2 installed. `moviepy.editor` is gone, and
`subclip`/`resize`/`crop`/`set_*` were all renamed. `TextClip` now requires an
explicit font **path** — a family name raises. Fixed, with a probe that fails loudly
with an actionable message rather than 90% into a render. That probe now lives in
`engine/services/fonts.py` and also honours `STUDIO_SUBTITLE_FONT` and a
`./storage/fonts` drop-in directory.

### 4.2 edge-tts emitted zero subtitle cues
edge-tts 7 changed the `boundary` default to `SentenceBoundary`; the handler only
knew `WordBoundary`, so every cue list came back empty and every video would have
silently fallen through to a Whisper transcription pass. Now requests word
boundaries explicitly and handles sentence boundaries as a fallback by splitting them
proportionally.

### 4.3 Blocking file reads in the async upload loop
8MB chunks were read synchronously inside the async upload, stalling every other
job's progress stream. Moved to a thread.

### 4.4 Retention mapping used indexing where it needed interpolation
Any beat shorter than one curve sample reported a drop of exactly zero — so short
beats, often the ones that lose people, could never be flagged.

### 4.7 Cross-clip fades dipped to black at every cut
The first cut of the transition code used `FadeOut` on the outgoing clip and
`FadeIn` on the incoming one. Sequential clips do not overlap, so the timeline went
to **luma 3.7 out of 255** at each seam — a blink on every cut, twenty of them in a
long-form video. Reviewing the code would never have caught it; a render measured
it. Now `CrossFadeIn` on the incoming clip plus negative `concatenate` padding, so
the two clips are on screen together while the dissolve runs.

The default is also now **0 (hard cuts)**. Fast-cut faceless video does not dissolve
between shots, and upstream defaults to no transition either.

### 4.8 The download error handler raised from inside the error handler
`stock._download` logged failures with `clip["url"]` — re-indexing the key whose
absence caused the failure. A clip without a `url` therefore raised `KeyError` from
the `except` block, escaped `asyncio.gather`, and killed the whole render instead of
skipping one clip. Also found by running it, not by reading it.

### 4.6 The p-value fallback could never have run
`two_tailed_p` claimed to fall back to `math.betainc` "for an exact result without
any third-party dependency". `math.betainc` does not exist — not in 3.11, not in
3.12, not in any released CPython. `scipy` was also absent from `pyproject.toml`,
so on a clean install every attribution comparison raised `AttributeError` and 17
tests failed. `scipy` is now a declared dependency and the branch is deleted rather
than approximated: a wrong p-value trains the feedback loop on noise, which is a
worse failure than a missing package.

### 4.5 Python 3.12-only f-string syntax
Nested same-quotes in an f-string. Ran fine on the 3.13 venv, would have crashed on
the 3.11 the project claims to support.

---

## 5. Known-imperfect, working as intended for now

### 5.1 Subtitles lose punctuation
edge-tts word boundaries strip punctuation, so cues read
`On purpose Here is why` instead of `On purpose. Here is why`. Cosmetic for burned-in
subtitles; **noticeably worse for the SRT uploaded as a caption track**, which is a
real ranking signal.

**Proper fix:** realign cue text against the original script — match each cue's words
back to the source sentence and restore the punctuation. MoneyPrinterTurbo does a
version of this in `vendor/moneyprinterturbo/app/services/voice.py:_match_script_line`.
`media._restore_punctuation` is our take on it; it recovers terminal `.!?` but not
commas or quotes.

### 5.2 Publish-time scheduling is a heuristic
YouTube exposes no hourly "when your viewers are online" dimension publicly. The
scheduler measures **weekday** from real data and **estimates hour-of-day** from a
built-in evening-weighted curve. The API labels this `measured_weekday_only` and the
UI says "estimated" rather than implying precision it does not have.

### 5.3 Thumbnail backgrounds are generated but the image APIs are unproven
Backgrounds now come from GPT Image (or Imagen), through
[providers/images.py](apps/engine/engine/providers/images.py), reusing
`OPENAI_API_KEY`/`GEMINI_API_KEY` rather than adding a key. With neither set the
composition falls back to a flat panel, so a keyless clone still gets a thumbnail.

**Neither transport has been called against a live API** — the request and response
shapes are covered by tests against recorded envelopes, not by a real key. Same
standing as the Google clients in §1.1.

Five archetypes with genuinely different layouts live in
[render/templates.py](apps/engine/engine/render/templates.py), and the three variants
are forced onto three different ones. Note what is *not* possible here: MrBeast-style
thumbnails are built on a human face at maximum expression, and this system is
faceless by design. What is ported is the machinery underneath — one idea readable at
168px, stakes made visible, saturation past tasteful, big numerals, reserved negative
space.

### 5.4 ~~Storage and job state are in-process~~ — fixed
Job state, channels, the schedule and the quota ledger are Postgres-backed. The
module-level dicts survive as a read cache that `repository.restore()` hydrates at
startup; a job that was mid-run at shutdown comes back marked `interrupted` and can
be resumed. `STUDIO_PERSIST=false` turns persistence off, which is what the test
suite uses.

### 5.5 ~~The web app runs entirely on demo data~~ — seven of ten screens fixed
This entry and the header above used to disagree with each other — one said "every
screen renders from demo.ts", the other "the web app reads live data". Neither was
right. Per screen, as of today:

| Screen | Data |
|---|---|
| Create | live; `POST /v1/jobs` then SSE, falling back to `DEMO_JOB` if the create fails |
| Queue, Library, Models | live, falling back to `demo.ts` when the engine is unreachable |
| Setup, Welcome | live only — no fallback, deliberately. They show "the engine is not running" instead of plausible fiction, because a setup screen that invents its own state is worse than one that admits it is blind |
| Calendar | mixed even when live: quota and bookings come from the engine, the draggable video tray is always `PENDING_VIDEOS` |
| Analytics | partly live: the monetisation card reads `GET /v1/analytics/monetisation` and hides itself when the engine is unreachable or no channel is connected. Everything below it is still demo — the per-video, retention and Short-cut panels remain unwired. The Short-cut fixture in `demo.ts` is at least the genuine output of `engine/shorts.py` run over the demo retention curve, not an invented one |
| Series, New channel | demo only, **no network call at all** — there is no series table, and the channel-launch endpoint has no caller |

Series and New channel used to ship five buttons wired to nothing, including both
screens' single primary action: pressing the one prominent control did nothing at
all — no navigation, no request, no message. That contradicted this codebase's own
rule, written down in `queue/page.tsx`, that a button doing nothing is worse than no
button. Pause, Resume and Edit are now deleted; "New series" and "Create series"
remain as `disabled` controls carrying the reason ("Creating a series needs the
series endpoint, which does not exist yet"), because they are what tells you what the
screen is for. Disabled-and-explained is not the same lie as live-and-inert. When the
series endpoint lands, these are the controls to re-enable.

The fallback is not a flag or an env var: `get<T>()` in `apps/web/lib/engine.ts`
returns `null` on any failure including a non-2xx, and each page does
`const live = x !== null`. Consequences worth knowing: it also swallows a genuine
500, so a broken endpoint looks identical to a stopped engine. Mutations do *not*
fall back — `send()` throws, so a failed publish never reads as success.

Everything showing fixtures carries a "demo data" badge, Library omits views and CTR
in live mode rather than showing zeros, and Calendar refuses to persist a drag when
`!live` and says "nothing was saved".

### 5.6 Duplicate detection is lexical, not semantic
Jaccard overlap on content words. Catches "why bridges collapse" vs "the reason
bridges collapse". Will **not** catch "why bridges collapse" vs "the physics of
structural failure in suspension spans" — same video, no shared words. An embedding
model would; it was rejected because this runs on every idea against the whole
catalogue and needs to be explainable on the idea card.

### 5.7 The 500-char keyword and tag trimmers are naive
They keep the earliest entries and drop the rest. Should drop the *lowest-value*
ones.

### 5.8 Two surfaces exist, cost something, and are read by nothing
Both are decisions rather than oversights, recorded so they stop being re-found:

**Channel launches are not persisted.** `repository.save_launch`/`load_launches` and
the `ChannelLaunch` table all exist; no application code calls either, so a launch is
lost on restart. Wiring it up is a loader rewrite, not a missing call — `load_launches`
returns a flattened dict that does not match the mirror shape `api/channels.py` reads
(`states`, `events`, `inputs`). What is lost is a regenerable LLM artifact on a flow
whose manual channel-creation step is a documented gap anyway (§3.1). The module
docstring used to claim launches survived a restart; that claim is gone.

**`ChaptersStage` output is generated, billed and consumed by nothing.** YouTube only
renders chapters from timestamps in the description, and nothing appends them there —
`SeoPackage` (which has a `chapters` field) is never constructed anywhere. So the
stage costs about $0.01 per run for a value no caller reads. It is left in place
rather than deleted because plumbing it properly is a real design choice: either
append the block to the description with 5000-char guarding, or reorder the graph to
`titles → chapters → description`, which drags `subtitles` into the SEO chain. Its
dependency declaration *was* wrong and is fixed — it read `ctx.get("subtitles")` while
declaring only `("titles",)`, so re-running the voiceover left chapter timestamps
pointing at cues that no longer existed.

---

## 6. Not built at all

- ~~**No Postgres.**~~ Done — SQLAlchemy models, Alembic migrations, and a quota
  ledger that survives a restart. AUDIT.md §5.1.
- ~~**No arq workers.**~~ Done — `engine/worker.py`, events over Redis pub/sub.
  The in-process path is kept as a supported single-process mode. AUDIT.md §5.2.
- **No auth.** The engine is unauthenticated. Do not expose it.
- **No ⌘K command palette**, though the design spec leans on it to keep screens
  sparse.
- **No thumbnail A/B swapping**, which Phase 8's attribution is otherwise ready for.
- **No engine authentication.** Still true and still the largest gap: anything that
  can reach `127.0.0.1:8080` can read and write credentials through
  `PUT /v1/setup/keys`. CORS names `http://localhost:3000`, so any *other* dev
  server a developer happens to run on that port is a trusted origin. Do not expose
  the engine.
- **No trend monitoring.** The idea backlog accepts a `trending_terms` argument that
  nothing currently supplies, so `freshness` is zero on every real idea and its
  decay curve has nothing to decay. The scoring is ready for a supplier; there
  isn't one.
- **The weekly review has no screen and sends no notification.** The cron job runs
  Monday 06:00 UTC and `POST /v1/insights/review` runs it on demand, but the only
  way to read the result is the API or the worker log. `Review.worth_reading` is
  there for a notifier that does not exist yet — most weeks it is false, which is
  the point.
- **The review needs a running worker.** It is an arq cron job, so the in-process
  fallback mode (no Redis) never fires it. Nothing warns about this.
- **Shorts are selected but not cut.** `GET /v1/analytics/shorts/{video_id}` ranks
  the stretches of a long-form video worth clipping and says why, but nothing
  renders the clip. Two things are missing and neither is small: `VideoRecord`
  carries no path to the rendered master, so there is no file to cut from, and a
  9:16 crop of 16:9 footage needs a subject to crop *around* — a centre crop of a
  talking-head shot is fine and a centre crop of anything else is not. Until both
  exist the endpoint is a recommendation, which is why the UI shows timestamps and
  no "Cut this" button.

---

## 7. Do this first

**Fixes for everything on this page are written up as paste-able agent prompts in
[FIX-TASKS.md](FIX-TASKS.md)**, ordered by dependency, each with a verifiable
"Done when". Start with A1 — it is the only item with multi-day external latency.


```bash
docker compose up -d
```

1. Start the Google Cloud OAuth application (slowest thing on this list)
2. Run `scripts/setup.sh` (or `.\scripts\setup.ps1` on Windows), then add
   `ANTHROPIC_API_KEY` and `PEXELS_API_KEY` to the `.env` it wrote
3. Generate **one short** end to end and watch where it breaks
4. Point a real Ollama daemon at it (1.3) — every transport is mock-tested and none
   has met a live endpoint
