# Setup

Everything that could be automated has been. This is the list of what is left,
which is **API keys and one Google form**. Nothing here is busywork — each item
exists because it needs an account only you can hold, or because Google provides
no API for it.

---

## First: one command

```bash
./scripts/setup.sh
```

Creates the venv, installs both toolchains, writes `.env`, creates the database
schema, runs the tests, and finishes by telling you exactly which of the items
below are still missing.

**No Docker required.** Left alone, the engine uses SQLite and runs renders
in-process. Postgres and Redis are an upgrade, not a prerequisite.

Re-run it any time — it is idempotent and only redoes what changed.

To check the state at any point:

```bash
apps/engine/.venv/bin/python apps/engine/scripts/doctor.py
```

It prints one line per dependency and, for anything missing, the single next
action. Exit code is non-zero only if something genuinely blocking is wrong.

---

## Then: two keys, five minutes

These two are the only things standing between a fresh clone and a rendered
video. Put both in `.env`.

### 1. An LLM key — 2 minutes

```
ANTHROPIC_API_KEY=sk-ant-...
```

From <https://console.anthropic.com> → API Keys. Requires a payment method; a
short video costs a few cents.

*Alternative with no account and no cost:* install [Ollama](https://ollama.com),
run `ollama pull qwen2.5:14b`, and route every task to it on the **Models**
screen. Slower and noticeably weaker at the critique pass, but it works.

### 2. A stock footage key — 2 minutes

```
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
```

- Pexels: <https://www.pexels.com/api/> — free, instant, no review.
- Pixabay: <https://pixabay.com/api/docs/> — free, instant.

Either alone works. Set **both** if you can: Pexels is searched first and Pixabay
fills whatever it could not, and a beat with no footage is a hole in the video.

**At this point you can generate and render videos.** Everything below is only
needed to publish them.

---

## To publish: Google — about 15 minutes, plus waiting

This is the slow one, and none of it can be automated. Start it before you need
it: the verification step has a delay Google controls.

### 3. A Google Cloud OAuth client

1. <https://console.cloud.google.com> → create a project.
2. **APIs & Services → Library**: enable **YouTube Data API v3** and
   **YouTube Analytics API**. Both.
3. **OAuth consent screen**: External, add yourself as a test user. You do not
   need to submit for verification while you are the only user.
4. **Credentials → Create Credentials → OAuth client ID** → *Web application*.
   Add this exact redirect URI:
   ```
   http://localhost:8080/v1/auth/google/callback
   ```
5. Put the two values in `.env`:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```

Then visit <http://localhost:8080/v1/auth/google> and follow the link it returns.
If the credentials are missing it says so plainly rather than handing you a broken
URL.

> If you change `GOOGLE_REDIRECT_URI`, it must match what you registered in step 4
> **character for character**. A mismatch produces `redirect_uri_mismatch`, which
> is the least informative error Google returns.

### 4. A YouTube channel — manual, permanently

**There is no `channels.insert` API.** A channel can only be created through the
YouTube UI, and any tool claiming otherwise is lying to you.

The **New channel** screen designs the whole identity — name, handle, About text,
keywords, visual direction, series, and 30 de-duplicated video ideas — validated
against the real character limits. You then do five things by hand:

1. Create the channel. **Use a Brand Account** — ownership can be transferred later, a personal channel cannot.
2. Claim the handle. First-come; do this first.
3. Set the channel name. **Not settable via the API, ever.**
4. Verify by phone. Unlocks custom thumbnails and videos over 15 minutes — you need both.
5. Connect it via OAuth (step 3 above).

After step 5, `POST /v1/channels/launch/apply` pushes the description, keywords
and country. Name, handle, avatar and banner stay manual permanently.

---

## Optional, and genuinely optional

| | Why you might | Cost |
|---|---|---|
| `docker compose up -d` | Postgres instead of SQLite, and renders in a worker so restarting the API cannot kill one mid-encode. Worth it once you are rendering regularly. | 1 command |
| `STUDIO_BGM_ENABLED=true` | Background music. The directory is empty on purpose — nothing here ships licensed music, and publishing over an unlicensed bed is a copyright strike. Drop your own tracks in `storage/bgm`. | your own audio |
| `KEYWORD_API_URL` | A fallback keyword source. Only matters if you are on a datacenter or VPN network — YouTube autocomplete and DuckDuckGo block those, and grounding is the first stage of the only workflow. `doctor.py` tells you if this affects you. | depends |
| Quota extension | Google grants 10,000 units/day, and an upload costs 1,600 — about **4 publishes a day** once thumbnails and captions are counted. More needs an audited application that takes weeks. | weeks |

---

## Running it

```bash
npm run dev                                                              # :3000
apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080
```

With `docker compose up -d` also running, start the render worker so a restart
cannot kill a render:

```bash
apps/engine/.venv/bin/python -m arq engine.worker.WorkerSettings
```

Or run the whole stack in containers:

```bash
docker compose --profile full up -d
```

On Windows the interpreter is at `.venv/Scripts/python`.

---

## What is still unproven

Honest about the edges, because you will hit them before I would:

- **No Google API call has ever been executed** against a live account. The
  upload, captions, thumbnail and analytics clients are reviewed code, not proven
  code. The resumable-upload chunk loop and its `308 Resume Incomplete` handling
  are the parts most likely to be subtly wrong, because they cannot be reasoned
  about without a real response.
- **The pipeline has not been run end to end with real footage and real TTS.**
  The render core is verified by measurement (see `KNOWN-ISSUES.md` §2) but with
  synthetic clips and tones.
- **Ollama is implemented and unit-tested but no real daemon has been called.**
- **Thumbnails are placeholder compositions.** No image model is wired in, so the
  layout and safe zones are right but the background is flat colour.
