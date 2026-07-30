# Setup

Everything that could be automated has been. This is the list of what is left,
which is **API keys and one Google form**. Nothing here is busywork — each item
exists because it needs an account only you can hold, or because Google provides
no API for it.

## What you need first

| | | |
|---|---|---|
| **Python 3.11+** | runs the render engine | <https://www.python.org/downloads/> |
| **Node.js 20+** | runs the web app | <https://nodejs.org> |

3.11, 3.12 or 3.13 for preference: two dependencies publish compiled Windows wheels
only for versions that have been out a while, and setup will offer to fetch 3.12
alongside a newer Python rather than fight it.

That is the whole list. **No Docker, no database server, no ffmpeg** — one ships
with the engine's Python dependencies. Postgres and Redis are optional upgrades
(see the bottom of this page), never prerequisites.

You do not have to install these by hand. On Windows the installer offers to
fetch both through `winget`, which ships with Windows 10 and 11; on macOS and
Linux it prints the exact one-line command for your package manager and stops.
Either way you are told what is missing and how to get it, rather than meeting a
failure halfway through.

On Windows, tick **"Add Python to PATH"** if you do install it yourself — it is
off by default, and nothing works without it.

## The short version

**Windows — nothing to type.** Double-click **`Install Studio.cmd`** once, then
**`Studio.cmd`** (or the **Studio** shortcut it puts on your Desktop) whenever
you want to use it.

**macOS / Linux** — run `./scripts/setup.sh` once. After that, open **Studio**
from Applications (macOS) or the Studio launcher on your Desktop (Linux).
`npm start` works everywhere too.

Your browser opens by itself. On a fresh install it lands on a short setup flow
that asks for your keys — it says what each one unlocks, links to where to get
it, and turns green when you have enough to make a video. You do not need to
edit a file, and you do not need to restart anything: a save takes effect
immediately.

Double-clicking the launcher while Studio is already running just brings the
browser back rather than starting a second copy.

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

**The easy way:** double-click the launcher (or `npm start`) — your browser opens on
the setup screen by itself, normally at <http://localhost:3000/setup>. Paste the keys
in and press Save. The screen writes `.env` for you, keeps your comments, and shows
which keys are already set without ever displaying one back.

If port 3000 or 8080 is already taken, Studio moves to the next free pair rather than
refusing to start, and prints the address it chose — so use whatever it printed.

**By hand:** put both in `.env` at the repository root. The names below are the
ones the engine reads.

### 1. An LLM key — 2 minutes

```
ANTHROPIC_API_KEY=sk-ant-...
```

From <https://console.anthropic.com> → API Keys. Requires a payment method; a
short video costs a few cents.

This key also does the **research**. The model routed to the research task on the
**Models** screen searches the web itself and cites what it read — no scraping, and
nothing else to sign up for. That is why research is routed to the best model by
default: everything downstream is only as good as what it found.

*Alternative with no account and no cost:* install [Ollama](https://ollama.com),
run `ollama pull qwen2.5:14b`, and route every task to it on the **Models**
screen. Slower and noticeably weaker at the critique pass, but it works — with one
real caveat: local models cannot search, so research falls back to scraping keyless
search engines. Those refuse automated requests often, and that is the usual reason
a render dies at Research with *no usable sources found*. The Models screen says so
next to the research picker.

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

Every click, because the console renamed half of this in 2025 and most guides now
describe menus that no longer exist. What used to be "OAuth consent screen" is now
**Google Auth Platform**, and its Test users live under **Audience**.

**Why it has to be your own project.** The two values are not a second login —
they identify your copy of Studio to Google. The YouTube API's 10,000 units a day
(≈ six uploads) are counted *per Cloud project*, so a client shipped inside Studio
would mean everyone who installed it fighting over the same six.

**3a. A project.** <https://console.cloud.google.com/projectcreate> → any name →
**Create**. Nothing is billed. Wait for the notification, then make sure the
project picker at the top shows it — every step below applies to the selected
project, and the commonest way to lose an hour here is doing step 3c in a
different project from step 3d.

**3b. The two APIs.** Enable both, on that project:

- <https://console.cloud.google.com/apis/library/youtube.googleapis.com> →
  **Enable**. This is the one that uploads.
- <https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com> →
  **Enable**. This is the one that measures.

**3c. The consent screen** — <https://console.cloud.google.com/auth/overview>.
If it offers **Get started**, take it. Then:

1. **Branding**: App name (`Studio` is fine — only you ever see it), user support
   email = your own, developer contact email = your own → **Save**.
2. **Audience**: User type **External**. Under **Test users** → **+ Add users** →
   your own Google address → **Save**. Only accounts listed here can authorise the
   app while it is in Testing.
3. **Data access** → **Add or remove scopes** → **Manually add scopes**, and paste
   these four:
   ```
   https://www.googleapis.com/auth/youtube.upload
   https://www.googleapis.com/auth/youtube.readonly
   https://www.googleapis.com/auth/youtube.force-ssl
   https://www.googleapis.com/auth/yt-analytics.readonly
   ```
   → **Update** → **Save**. (`force-ssl` is not optional — captions need it.)

**3d. The client** — <https://console.cloud.google.com/auth/clients> →
**+ Create client**:

1. Application type: **Web application**. Not "Desktop app": the flow here is a
   redirect back to a local HTTP server, which is the web-application shape.
2. Name: anything.
3. Leave **Authorised JavaScript origins** empty.
4. **Authorised redirect URIs** → **+ Add URI** → paste exactly:
   ```
   http://localhost:8080/v1/auth/google/callback
   ```
   `http` not `https`, `8080` not `3000`, no trailing slash. Anything else is
   `redirect_uri_mismatch` later, which is the least informative error Google
   returns.
5. **Create**. The dialog then shows both values. The client ID ends
   `.apps.googleusercontent.com`; the secret starts `GOCSPX-`.

**3e. Into Studio.** Paste both into the Publishing fields on
<http://localhost:3000/setup> → **Save** → **Connect YouTube**. Or put them in
`.env` by hand:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Google then asks which account, warns that the app is unverified — **Advanced →
Go to Studio** — and shows the permissions. **Leave every checkbox ticked**: an
unticked upload scope fails at the moment you publish a video, not here. Studio
stores one refresh token, encrypted, and nothing else.

> **Testing mode expires refresh tokens after seven days.** Once you have
> confirmed the connection works, go back to **Audience** and press
> **Publish app**. It stays unverified — the warning screen and the 100-user cap
> remain, neither of which matters for your own channel — but the weekly
> reconnect stops. This is the single most common reason a working connection
> dies a week later for no visible reason.

> If you change `GOOGLE_REDIRECT_URI`, or Studio reports the engine on a port
> other than 8080 because something else held it, the registered URI must match
> what Studio actually uses, character for character.

The engine must be running when you press Connect: the redirect lands on
`localhost:8080`, and nothing is listening otherwise.

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

Double-click the **Studio** launcher — the Desktop shortcut on Windows and Linux,
or Studio in Applications on macOS. `Studio.cmd` in this folder is the same
thing. From a terminal, `npm start` is the same thing again.

All three run one process that starts both halves, labels their output so you can
tell an engine traceback from a Next one, waits for the web app to actually
answer, and then opens Studio. A window stays open while Studio runs — that
is deliberate, it is where errors appear, and closing it is how you quit. Both
halves go down together: a web app talking to a dead engine quietly falls back to
demo data and looks like it is working, which is worse than stopping.

Launching it again while it is already running just brings the window back rather
than starting a second copy.

### The window it opens

Studio opens in a window of its own — no tabs, no address bar, no bookmarks bar,
its own taskbar entry. That is a Chromium app window (`--app=`), borrowed from
whichever of Edge, Chrome or Brave is installed; Edge ships with Windows, so on
Windows there is normally nothing to choose. It is the same app either way, and
where none of those exist it opens a normal browser tab instead.

Set `STUDIO_BROWSER=1` to always get an ordinary tab — worth it if you want
devtools, your extensions, or your own profile.

It is not an Electron app, and deliberately so: a real `.exe` would mean shipping
a second browser engine, a build step and code signing, to change what the window
frame looks like. If you want a Start-menu entry that behaves like an installed
program, the browser will make you one — in Edge, **Settings and more (…) → Apps →
Install this site as an app**, with Studio running.

If something *else* holds the ports it says so, and suggests different ones,
rather than letting uvicorn print `[Errno 98] Address already in use`.

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

**`NativeCommandError` during setup, naming a program that looks like it worked** —
for example `py.exe : Python 3.14.6 ... NativeCommandError`. Windows PowerShell 5.1
turns a single line written to stderr into a terminating error whenever the script
captures output and `$ErrorActionPreference` is `Stop`, so a program that succeeded
can still stop the install. Fixed in `setup.ps1`; if you see it, you are on an old
copy — `git pull` and run `Install Studio.cmd` again.

**`Unexpected token ')'` in `setup.ps1`, on a line that is plainly fine** — same
cause, one layer down: a `.ps1` saved as UTF-8 with no BOM is read using the
machine's ANSI code page, and a non-ASCII character in a string can swallow the
closing quote. The file is ASCII-only for this reason, so again: `git pull`.

**Setup offers to install Python 3.12 when you already have 3.14** — deliberate,
and it installs alongside rather than replacing anything. Some dependencies
(`ctranslate2`, `scipy`) publish compiled Windows wheels only for versions that
have been out a while; on a newer Python pip tries to build them from source and
fails on the absent C++ toolchain. Decline it and setup continues on 3.14 anyway.
To point setup at a specific interpreter, set `STUDIO_PYTHON` to its full path.

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
- **No LLM provider has been called for real.** All four transports — Anthropic,
  OpenAI-compatible, Gemini and Ollama — are covered by `tests/test_llm.py` against
  mocked HTTP, so the request shape and the response parsing are proven. That a live
  endpoint accepts those requests is not: a renamed usage field or a rejected
  parameter would pass the suite and fail on first contact. (Until recently this was
  worse — `tests/conftest.py` stubbed the module out entirely, so *no* test touched
  it. That stub is gone.)
- **No image API has been called for real.** The two transports are written against
  OpenAI's `images/generations` and Imagen's `:predict`, and both are covered by
  tests against recorded response shapes — but like the Google clients, they are
  reviewed code, not proven code, until a live key runs through them. The
  composition either side of the call *is* proven: see the note below.
