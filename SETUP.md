# Setup

Everything that could be automated has been. This is the list of what is left,
which is **API keys and one Google form**. Nothing here is busywork — each item
exists because it needs an account only you can hold, or because Google provides
no API for it.

## The short version

```bash
./scripts/setup.sh      # or: powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
npm start
```

Then open **<http://localhost:3000/setup>** and paste your keys into the screen.
It says what each one unlocks, links to where to get it, and turns green when
you have enough to make a video. You do not need to edit a file, and you do not
need to restart anything — a save takes effect immediately.

The rest of this page is the same information at length, for when something goes
wrong or you would rather edit `.env` by hand. **You can stop reading here.**

---

## First: one command

**macOS / Linux**

```bash
./scripts/setup.sh
```

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

> The `-ExecutionPolicy Bypass` is required, not a suggestion — Windows refuses to
> run unsigned scripts by default and you get "running scripts is disabled on this
> system" instead.
>
> `./scripts/setup.sh` on Windows opens the "how do you want to open this file?"
> picker, because `.sh` is a bash script and nothing is registered to run it.
>
> And do not paste the <code>```bash</code> line — that is markdown fencing, not a
> command. PowerShell reports it as `The term '```bash' is not recognized`.

Creates the venv, installs both toolchains, writes `.env`, creates the database
schema, runs the tests, and finishes by telling you exactly which of the items
below are still missing.

**No Docker required.** Left alone, the engine uses SQLite and runs renders
in-process. Postgres and Redis are an upgrade, not a prerequisite.

Re-run it any time — it is idempotent and only redoes what changed.

To check the state at any point:

```bash
apps/engine/.venv/bin/python apps/engine/scripts/doctor.py          # macOS / Linux
```

```powershell
.\apps\engine\.venv\Scripts\python apps\engine\scripts\doctor.py         # Windows
```

It prints one line per dependency and, for anything missing, the single next
action. Exit code is non-zero only if something genuinely blocking is wrong.

---

## Then: two keys, five minutes

These two are the only things standing between a fresh clone and a rendered
video.

**The easy way:** `npm start`, then <http://localhost:3000/setup>. Paste them in
and press Save. The screen writes `.env` for you, keeps your comments, and shows
which keys are already set without ever displaying one back.

**By hand:** put both in `.env` at the repository root. The names below are the
ones the engine reads.

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
| `OPENAI_API_KEY` | **Real thumbnail backgrounds**, via GPT Image. With no key, thumbnails are composed correctly but over a flat panel. There is no separate image key and nothing to configure — `STUDIO_IMAGE_PROVIDER` defaults to `auto` and picks this up. Budget ~$0.57 per video for three variants. `GEMINI_API_KEY` works as a cheaper alternative (~$0.12) and is used only if there is no OpenAI key. | 2 min |
| `STUDIO_BGM_ENABLED=true` | Background music. The directory is empty on purpose — nothing here ships licensed music, and publishing over an unlicensed bed is a copyright strike. Drop your own tracks in `storage/bgm`. | your own audio |
| `KEYWORD_API_URL` | A fallback keyword source. Only matters if you are on a datacenter or VPN network — YouTube autocomplete and DuckDuckGo block those, and grounding is the first stage of the only workflow. `doctor.py` tells you if this affects you. | depends |
| Quota extension | Google grants 10,000 units/day, and an upload costs 1,600 — about **4 publishes a day** once thumbnails and captions are counted. More needs an audited application that takes weeks. | weeks |

---

## Running it

```bash
npm start
```

One command, both halves, on every platform. It prints where each one is
listening, labels their output so you can tell an engine traceback from a Next
one, and takes both down together on Ctrl-C — a web app talking to a dead engine
quietly falls back to demo data and looks like it is working, which is worse than
stopping.

Then open **<http://localhost:3000>**.

If the ports are busy it says so and suggests different ones rather than letting
uvicorn print `[Errno 98] Address already in use`. The usual cause is that Studio
is already running in another terminal.

<details>
<summary>Starting the two halves separately</summary>

Useful when you want to restart one without the other, or attach a debugger.
Two terminals, both from the repo root. Neither exits — that is correct.

**macOS / Linux**

```bash
npm run dev                                                              # terminal 1 → :3000
apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080   # terminal 2
```

**Windows (PowerShell)**

```powershell
npm run dev
.\apps\engine\.venv\Scripts\python -m uvicorn engine.main:app --reload --port 8080
```

</details>

> **The leading `.\` is required.** PowerShell will not run an executable given as a
> relative path without it — it treats a bare `apps\...` as a command name to look
> up and fails with `The module 'apps' could not be loaded`, which does not sound
> like a path problem at all. This bites on every command below too.

Two differences from macOS/Linux, and that is all: the interpreter is at
`.venv\Scripts\python` rather than `.venv/bin/python`, and relative commands need
the `.\` prefix.

### Checking it worked

- <http://localhost:8080/health> returns JSON → the engine is up.
- The web app shows **"demo data"** when it cannot reach the engine and live
  figures when it can. That label is the fastest way to tell which you are seeing.
- `.\apps\engine\.venv\Scripts\python apps\engine\scripts\doctor.py` lists anything
  still missing, one line each. (Only the *first* path needs `.\` — it is the one
  being executed; the second is just an argument.)

### Optional extras

With `docker compose up -d` also running, start the render worker so restarting the
API cannot kill a render mid-encode:

```bash
apps/engine/.venv/bin/python -m arq engine.worker.WorkerSettings              # macOS / Linux
```

```powershell
.\apps\engine\.venv\Scripts\python -m arq engine.worker.WorkerSettings     # Windows
```

Or run the whole stack in containers — no Node or Python needed on the host:

```bash
docker compose --profile full up -d
```

---

## If something does not start

**Most web-side problems are one command:**

```bash
npm run reinstall
```

It deletes *every* `node_modules` in the workspace — root and each workspace — then
reinstalls and verifies. Stop the dev server first; on Windows a running server
holds those files open, the delete fails, and the usual
`-ErrorAction SilentlyContinue` hides that it failed. This script reports it
instead of pretending.

The specific symptoms, and what each one is:

**`Configuring Next.js via 'next.config.ts' is not supported`**, or `npm audit`
reporting ~107 findings instead of 14, or stack traces mentioning webpack,
styled-jsx or autoprefixer — none of which this app uses. A stale
`apps/web/node_modules` from an older layout is shadowing the root install. A clean
install here hoists everything and creates no workspace `node_modules` at all, so
any that exists is left over — and it wins over the root copy for anything resolved
inside that workspace, which is how a dev server can announce Next 16 while running
a Next 10 tree. `npm run reinstall`.

**`Cannot find module '../lightningcss.win32-x64-msvc.node'`** — a 500 on the first
page load. npm records a native optional dependency only for the platform that
generated the lockfile, so a `node_modules` installed before those were pinned has
no Windows binary. `npm run reinstall`.

**`The module 'apps' could not be loaded`** — PowerShell will not run an executable
given as a relative path. Prefix it with `.\`:

```powershell
.\apps\engine\.venv\Scripts\python -m uvicorn engine.main:app --reload --port 8080
```

**`The term '```bash' is not recognized`** — that line is markdown fencing from
these docs, not a command. Copy the lines *between* the fences.

**Anything engine-side** — run the doctor; it names the single next action for
whatever is missing.

`npm run check:toolchain` reports the state of the web dependencies without
changing anything.

---

## Security — what is assumed

This is built to run on your machine, for you. Two things follow from that, and
both matter before you put it anywhere else.

**Nothing published leaves the local machine.** Every port in `docker-compose.yml`
is bound to `127.0.0.1`. They were bound to every interface, which put Postgres
(credentials `studio/studio`), Redis (no password) and the engine on whatever
network the laptop had joined — a café or hotel wifi is enough. **The engine has no
authentication**: anything that can reach it can start renders, read every job and
publish to a connected channel. Exposing it needs a reverse proxy that terminates
TLS and authenticates. Do not just change the binding.

**Back up `storage/.secret_key`.** It is generated on first use and it encrypts your
YouTube refresh tokens. Lose it and every connected channel has to be reconnected —
which is recoverable, and the app will tell you to. Leak it and someone with a copy
of the database has your channel. It is `0600` and inside a gitignored directory, so
it will not be committed by accident.

> Earlier versions shipped `STUDIO_SECRET_KEY=change-me-32-bytes-minimum-for-token-encryption`
> in `.env.example`, and `setup.sh` copies that file to `.env`. If your `.env` still
> has that line uncommented, **comment it out** — that value is in this repository,
> so anything encrypted under it is public. Reconnect any channel you had connected.

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
- **Ollama is implemented and has no test coverage at all.** Not just "no real
  daemon" — `tests/conftest.py` stubs out `engine.providers.llm` entirely, so no
  test in this repository ever exercises the transport. The routing table and cost
  model around it are covered; the code that talks to a daemon is not.
- **No image API has been called for real.** The two transports are written against
  OpenAI's `images/generations` and Imagen's `:predict`, and both are covered by
  tests against recorded response shapes — but like the Google clients, they are
  reviewed code, not proven code, until a live key runs through them. The
  composition either side of the call *is* proven: see the note below.
