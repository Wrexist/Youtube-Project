# Known issues

What is unverified, what is knowingly incomplete, and what will need a human.
Ordered by how likely it is to bite you.

Last updated after Phase 10 + channel launcher + model routing.
**104 engine tests passing. Web builds and typechecks clean.**

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

### 1.3 Ollama is supported but untested against a real daemon
The routing table, cost model, `/api/chat` transport, and the `format: json`
constraint are all implemented and unit-tested. **No actual Ollama server has been
called.** `probe_ollama` and `register_ollama` in particular are unverified against a
live daemon.

**To fix:** `ollama serve`, `ollama pull qwen2.5:14b`, then
`POST /v1/models/ollama/register` and `POST /v1/models/test`.

### 1.4 No `PEXELS_API_KEY` → no footage
`MaterialsStage` raises immediately without one. Free key, instant signup.

---

## 2. Verified working — so you know where the floor is

These were actually executed on this machine, not assumed:

- **Render pipeline.** A real 480×854 MP4 was written with two scaled-and-cropped
  clips and two composited subtitle overlays. Fixed two real bugs to get there — see
  4.1.
- **Edge TTS + subtitle cues.** Real audio, real word-boundary timings, correctly
  grouped into readable lines. Fixed a real bug — see 4.2.
- **FastAPI app imports** with all 16 routes registered.
- **104 unit tests**, covering the workflow framework, scheduling, quota arithmetic,
  statistics, attribution, automation, and model routing.

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

### 3.3 Music licensing
Nothing ships with licensed music. MoneyPrinterTurbo's bundled `resource/songs` has
unclear provenance and is deliberately **not** carried over. Do not publish anything
with it.

---

## 4. Bugs found and fixed (recorded so they aren't reintroduced)

### 4.1 MoviePy 2.x API
Written against 1.x; 2.1.2 installed. `moviepy.editor` is gone, and
`subclip`/`resize`/`crop`/`set_*` were all renamed. `TextClip` now requires an
explicit font **path** — a family name raises. Fixed, with a `_subtitle_font()` probe
that fails loudly with an actionable message rather than 90% into a render.

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
version of this in `voice.py:_match_script_line`.

### 5.2 Publish-time scheduling is a heuristic
YouTube exposes no hourly "when your viewers are online" dimension publicly. The
scheduler measures **weekday** from real data and **estimates hour-of-day** from a
built-in evening-weighted curve. The API labels this `measured_weekday_only` and the
UI says "estimated" rather than implying precision it does not have.

### 5.3 Thumbnails are placeholder images
`make_thumbnail` composes real typography with correct safe zones onto a solid
background. **No image model is wired in.** The composition and text layer are the
parts worth getting right first; the background is a two-line swap once you pick a
provider.

### 5.4 Storage and job state are in-process
`JOBS`, `CHANNELS`, `SCHEDULE`, `RECORDS`, `LAUNCHES` are module-level dicts. A
restart loses everything. The shapes match what the Postgres tables need, so the swap
is contained — but until then, do not run this anywhere that restarts.

### 5.5 The web app runs entirely on demo data
Every screen renders from `apps/web/lib/demo.ts`. **Nothing is wired to the engine
yet** — no `fetch`, no SSE subscription. That was deliberate (a design you cannot
look at is a design you cannot judge), but it means the UI currently proves the
design, not the integration.

### 5.6 Duplicate detection is lexical, not semantic
Jaccard overlap on content words. Catches "why bridges collapse" vs "the reason
bridges collapse". Will **not** catch "why bridges collapse" vs "the physics of
structural failure in suspension spans" — same video, no shared words. An embedding
model would; it was rejected because this runs on every idea against the whole
catalogue and needs to be explainable on the idea card.

### 5.7 The 500-char keyword and tag trimmers are naive
They keep the earliest entries and drop the rest. Should drop the *lowest-value*
ones.

---

## 6. Not built at all

- **No Postgres.** `docker-compose.yml` starts it; nothing connects. No models, no
  migrations.
- **No arq workers.** Jobs run as in-process asyncio tasks. Fine for one user,
  wrong for anything else.
- **No auth.** The engine is unauthenticated. Do not expose it.
- **No ⌘K command palette**, though the design spec leans on it to keep screens
  sparse.
- **No thumbnail A/B swapping**, which Phase 8's attribution is otherwise ready for.
- **No trend monitoring.** The idea backlog accepts a `trending_terms` argument that
  nothing currently supplies.

---

## 7. Do this first

**Fixes for everything on this page are written up as paste-able agent prompts in
[FIX-TASKS.md](FIX-TASKS.md)**, ordered by dependency, each with a verifiable
"Done when". Start with A1 — it is the only item with multi-day external latency.


```bash
docker compose up -d
```

1. Start the Google Cloud OAuth application (slowest thing on this list)
2. `cp .env.example .env`, add `ANTHROPIC_API_KEY` and `PEXELS_API_KEY`
3. Generate **one short** end to end and watch where it breaks
4. Wire the web app to the engine (5.5) — until then the UI is a very detailed mockup
5. Move job state to Postgres (5.4) before running anything unattended
