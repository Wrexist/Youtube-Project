# Connecting TikTok — step by step

Everything left to do to make the Repurpose tab work with a real TikTok account.
No prior knowledge assumed. Five parts; four of them take minutes, and the first
one takes days because TikTok reviews your app before it will work at all.

**Start Part 1 today even if you do nothing else this week.** It is the only step
with a queue in front of it.

---

## Before you start: what this actually does

Worth reading, because it decides whether the rest is worth your time.

Studio connects to **your own TikTok account** and lists **your own posts**. That
is all TikTok's API offers. There is no search, no downloading other creators'
videos, no sweeping a hashtag — not because Studio lacks the feature but because
TikTok does not expose it:

| What you might expect | What is actually available |
| --- | --- |
| Search all of TikTok for clips | ✗ Does not exist in any public API |
| Download another creator's video | ✗ Not offered |
| List your own posts | ✓ This is what you're setting up |
| Read trending terms | ✓ Optional, needs your own feed URL |

TikTok's **Research API** does allow broader access, but it is restricted to
approved academic researchers at accredited institutions. It is not something a
channel can apply for.

So this is **Lane A** — repurposing your own TikToks onto YouTube. Other people's
clips reach Studio through **Lane B**, where a paid clipping campaign gives you
the footage and your enrolment in the campaign *is* the permission. Lane B does
not use the TikTok API at all, and nothing in this guide is needed for it.

---

## Part 1 — Register a TikTok app *(30 minutes of work, then days of waiting)*

You need two secret strings from TikTok: a **client key** and a **client secret**.
They are issued when you register an app, but the app only works once TikTok has
reviewed it.

> **Heads up:** TikTok redesigns this portal regularly, so the exact button names
> below may differ from what you see. The *things you need to accomplish* — an
> app, two products added, two scopes requested, one redirect URI — do not change.
> Follow the portal's own labels where they disagree with mine.

### 1.1 Make a developer account

1. Go to **https://developers.tiktok.com/**
2. Click **Log in** (top right) and sign in with the TikTok account whose posts
   you want to repurpose. Use the *real* account — you'll be connecting this one.
3. Accept the developer terms.

### 1.2 Create the app

1. Go to **Manage apps** → **Connect an app**.
2. Give it a name. This name is shown to you on the consent screen later, so make
   it something you'll recognise — "Studio" is fine.
3. Fill in the basics it asks for: description, category, icon. Keep the
   description accurate and boring: *"Lists my own TikTok posts so I can re-edit
   them into YouTube videos."* Review is a human reading this.

### 1.3 Add the two products

In the app's page, find **Products** (sometimes "Add products") and add:

- **Login Kit** — this is what lets you sign in and grant access.
- **Display API** — this is what lists your posts.

Both are needed. Login Kit alone gets you a sign-in that can't read anything.

### 1.4 Request exactly two scopes

Under the app's scopes or permissions section, request:

| Scope | Why Studio needs it |
| --- | --- |
| `user.info.basic` | Reads your @handle, so clips can be credited on screen |
| `video.list` | Lists your own posts |

**Do not request more than these two.** Studio does not use anything else, and
every extra scope is another thing a reviewer has to justify approving — which
slows you down and gives you nothing.

### 1.5 Set the redirect URI

This is the address TikTok sends the browser back to after you approve. It has to
match *exactly* — one character off and you get `redirect_uri_mismatch`.

If you're running Studio on your own computer, it is:

```
http://localhost:8080/v1/repurpose/auth/tiktok/callback
```

⚠️ **Expect friction here.** TikTok has historically insisted on `https://` and a
verified domain, and may reject a `localhost` URI outright. If it does, you have
two options:

- **A tunnel** — run something like `ngrok http 8080`, which gives you an
  `https://something.ngrok-free.app` address. Register
  `https://something.ngrok-free.app/v1/repurpose/auth/tiktok/callback` instead.
- **A real domain**, if you already host Studio somewhere.

If you use a tunnel or a domain, you must also tell Studio, because it builds the
callback address from `GOOGLE_REDIRECT_URI` in `.env`:

```
GOOGLE_REDIRECT_URI=https://something.ngrok-free.app/v1/auth/google/callback
```

Studio takes everything before `/v1/` from that line and appends its own path.
(Yes, the TikTok address being derived from a *Google* variable is odd — it's a
known wart, recorded so it surprises nobody.)

### 1.6 Submit for review, and wait

Submit the app. TikTok's review typically takes **several days**. You will get an
email. Until it is approved, the client key exists but sign-in will fail.

**While you wait, do Part 2.** Nothing in it depends on approval.

---

## Part 2 — Put the keys into Studio *(5 minutes)*

Once the app exists you can see the **client key** and **client secret** on its
page in the portal, even before approval.

### The easy way — through the Setup screen

1. Start Studio:
   ```bash
   npm start
   ```
2. Open **http://localhost:3000/setup** in your browser.
3. Scroll to the **Repurpose** group.
4. Paste the client key into **TikTok client key** and the secret into **TikTok
   client secret**.
5. Press **Save** (the bar at the bottom).

Studio writes them into `.env` for you. It edits that file rather than replacing
it, so anything else you keep in there survives.

### The manual way — if you'd rather edit the file

Open `.env` in the project root (create it by copying `.env.example` if it isn't
there) and add two lines:

```
TIKTOK_CLIENT_KEY=paste_the_key_here
TIKTOK_CLIENT_SECRET=paste_the_secret_here
```

Then restart the engine so it re-reads the file.

> **Get the names exactly right.** It is `TIKTOK_CLIENT_KEY`, **not**
> `STUDIO_TIKTOK_CLIENT_KEY`. Most variables in this project take a `STUDIO_`
> prefix and these two deliberately do not. A `STUDIO_`-prefixed version is
> silently ignored — the screen will keep saying TikTok is not configured while
> the value sits right there in the file. This has cost real time; the error
> messages now name the form that works.

### Third, optional variable

```
TIKTOK_TRENDS_URL=
```

Only if you have a trend feed to point at. TikTok's Creative Center has no public
API, so most people leave this empty. Left empty, freshness scores zero rather
than being invented, and clips are still ranked on topic fit, demand and reach.

### Check it took

Reload `/setup`. The two TikTok rows should show as configured (with the last few
characters of each key, never the whole thing).

---

## Part 3 — Update the database *(1 minute, once)*

Storing a TikTok connection needs a table that older installs don't have.

```bash
cd apps/engine
.venv/bin/python -m alembic upgrade head
```

On Windows, use `.venv/Scripts/python` instead of `.venv/bin/python`.

> **You must be in `apps/engine` when you run this.** Running it from the project
> root — even with `-c apps/engine/alembic.ini` — creates and migrates a *second,
> empty* database in the wrong place and reports success. Studio then crashes on
> the first query with a "missing column" error that says nothing about which file
> it looked in. This is documented in `CLAUDE.md` too, because it has caught
> people before.

---

## Part 4 — Connect your account *(1 minute, after approval)*

This is the step that needs TikTok's review to be finished.

1. Open **http://localhost:3000/setup**.
2. In the **Repurpose** group, find the **TikTok account** card.
3. Press **Connect TikTok**.
4. Your browser goes to TikTok's consent page. It will name the two permissions
   you requested. Approve.
5. You land back on the Setup screen. The card should now read **Connected as
   @yourhandle**.

**What Studio stores:** a refresh token, encrypted, and nothing else that matters.
Your password never touches this. The status screen that shows "connected" is
deliberately built so it cannot even read the token.

**If you see an error instead**, jump to Part 5 — the message tells you which
thing went wrong, and they have different fixes.

---

## Part 5 — Your first sweep *(1 minute)*

1. Open **http://localhost:3000/repurpose**.
2. Press **Find clips**.

Studio signs in as you, pages through your posts, scores each one against your
channel, and fills the grid — best fit first.

You'll get one of four answers, and they mean different things:

| What it says | What to do |
| --- | --- |
| "*n* clips scored for this channel" | Done. Go pick one. |
| "TikTok is not set up yet" | Part 2 — the keys aren't saved |
| "No TikTok account is connected" | Part 4 — keys are saved, nobody signed in |
| "Connected, but that account has no posts to work from" | It worked. That account is genuinely empty. |

### What happens next

Each clip shows a **rights chip** and a **fit score**. A clip cannot be built with
until you've recorded *how* you may use it — for your own posts that's one click
("My own clip", no paperwork, because there's no counterparty).

Then: add clips to an episode, and **Build episode**.

⚠️ **Recording rights does not make the finished video monetisable.** YouTube
judges copyright and reused-content *separately*, and the reused-content rule
applies even to footage you own outright. The edit still has to add something a
viewer can point at — commentary, restructuring, real cuts. Studio's originality
report shows both verdicts separately for exactly this reason, and will not let a
lazy edit through on the strength of a licence.

---

## When it breaks

The integration is written to fail loudly and name the fix. Here's the map.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Setup says not configured, but the keys are in `.env` | `STUDIO_` prefix on the TikTok names | Remove the prefix; restart the engine |
| `redirect_uri_mismatch` at TikTok | Registered URI ≠ the one Studio sent | Make them match character for character, including `http`/`https` and any trailing slash |
| "that sign-in link has expired — try again" | The sign-in took over 10 minutes, or you reused a link | Press **Connect TikTok** again |
| "Reconnect the account to continue" | TikTok revoked the grant, or the connection sat unused for about a year | Press **Reconnect** on the Setup screen |
| Sweep returns a 502 | TikTok is down or rate-limiting | Wait and retry — Studio already retried three times with backoff |
| Grid shows only your 20 newest posts | Shouldn't happen — pagination is handled | Report it; that was a real bug and is now tested against |
| Everything looks fine but the grid is empty | Check the four messages in Part 5 — one of them is showing | |

---

## What is still unproven

Being straight with you about this, because it changes what you should expect
from the first attempt.

**None of this has ever run against the real TikTok API.** Every test — and there
are around fifty covering this path — runs against a *mock* built from TikTok's
published documentation. The tests prove the code is internally consistent and
handles the failures it was designed for. They cannot prove TikTok behaves the way
its docs say.

So expect first contact to find something. The most likely candidate is the list
of error-code strings Studio uses to recognise an expired token: if TikTok's real
codes differ from the documented ones, an expired connection might report as a
generic failure instead of "reconnect". There's a backstop for exactly this — any
HTTP 401 is treated as an auth failure regardless of what code it carries — but a
backstop is not the same as having seen it work.

If something behaves oddly on your first real sweep, that is useful information,
not a broken install. The failure modes that were *designed for* are listed in the
table above; anything outside it is new.

### Already handled, so you don't have to think about them

Recorded here so you know these aren't the problem when something else goes wrong:

- **Tokens expire every 24 hours.** Studio refreshes them automatically, five
  minutes before expiry. The connection does not die overnight.
- **Two sweeps at once.** TikTok invalidates the old refresh token each time it
  issues a new one, so two simultaneous refreshes would destroy the connection.
  They're serialised — the second one finds the fresh token and makes no call.
- **TikTok answers `200 OK` with an error inside the body.** Every response is
  checked for that, so a failed call can't be mistaken for an empty account.
- **Re-sweeping.** Safe to run as often as you like. View counts and fit scores
  refresh — so a clip that went viral *after* you first saw it climbs the grid —
  while clips you dismissed stay dismissed.

---

## Quick reference

```bash
npm start                                          # everything
cd apps/engine && .venv/bin/python -m alembic upgrade head   # after updating
```

| Thing | Value |
| --- | --- |
| Developer portal | https://developers.tiktok.com/ |
| Products to add | Login Kit, Display API |
| Scopes to request | `user.info.basic`, `video.list` |
| Default redirect URI | `http://localhost:8080/v1/repurpose/auth/tiktok/callback` |
| Env variables | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` (no `STUDIO_` prefix) |
| Setup screen | http://localhost:3000/setup → Repurpose |
| Repurpose screen | http://localhost:3000/repurpose |
