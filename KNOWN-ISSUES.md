# Known issues

What is unverified, what is knowingly incomplete, and what will need a human.
Ordered by how likely it is to bite you.

> **A full-system audit on 2026-07-26 found 20 issues this file did not list.**
> **19 are now fixed.** Publishing is wired up and gated, CI is green, SSE no longer
> duplicates events, every setting either works or is gone, state survives a restart,
> renders run in a worker, and the whole web app reads live data (every screen —
> see §5.5, which is the honest version). The exception is the npm
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

> **2026-08-14:** Engine authentication (§6, was the largest open gap) is done —
> see the entry below. `.env.example` was missing `TIKTOK_CLIENT_KEY` /
> `TIKTOK_CLIENT_SECRET` / `TIKTOK_TRENDS_URL` entirely, despite
> `docs/TIKTOK-SETUP.md` telling operators to add them there — fixed, matching the
> pattern every other credential in that file already follows. While checking
> FIX-TASKS.md's Phase D against the actual code: D1 (subtitle punctuation), D3
> (semantic duplicate detection) and D4 (value-aware keyword/tag trimming) were
> already implemented and tested (`test_semantic_dedup.py`, `test_trimming.py`) —
> FIX-TASKS.md just hadn't been told. Marked done there. D2 (thumbnail image
> provider) was already recorded done in §5.3.

---

## 1. Blocking — nothing works end to end without these

### 1.1 No Google credentials → no publishing, no analytics
**Status:** every Google API call in this repo is unexecuted code.

`videos.insert`, `captions.insert`, `thumbnails.set`, the OAuth exchange, the whole
Analytics API client — reviewed, typed, and never once run against Google. The
resumable-upload chunk loop and the `308 Resume Incomplete` handling are the parts
most likely to be subtly wrong, because they are the parts that cannot be reasoned
about without a real response.

**Narrowed, not closed.** `tests/test_youtube_simulated.py` drives the whole path —
refresh, resumable session, chunked PUTs, `308` resume, the four publish stages of
`PUBLISH_WORKFLOW` — against a `respx` simulation of Google that holds its own copy
of the uploaded bytes and refuses anything non-contiguous. It found five real
defects; see §4.12. It proves the protocol, not the endpoint: no Google server has
still ever answered this code.

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

### 4.9 SQLite ignored every foreign key, so dev and CI disagreed about the schema
SQLite ships with foreign key checks **off**, and nothing turned them on. Every
`ForeignKey` in `tables.py` was therefore a real constraint on the Postgres CI runs
and pure decoration on the SQLite that every developer and every fresh clone uses.

It surfaced the expensive way. A test wrote a `repurpose_projects.job_id` pointing
at a job that did not exist — production always writes the job row before
dispatching the workflow, so the state was one production never reaches — and it
passed locally for the length of a feature branch. The first CI run against
Postgres rejected it, the failed transaction poisoned the connection, and sixteen
unrelated tests in a different file errored during fixture setup. Three failures
and sixteen errors, none of them in the file with the bug.

`db._enforce_foreign_keys` now sets `PRAGMA foreign_keys=ON` per connection —
per connection because that is the only scope SQLite offers, and a connection that
misses it silently stops enforcing. The full suite passes with it on, which is the
evidence that this was the only violation rather than the first of many.

### 4.10 The repurpose tests mixed `TestClient` with the `database` fixture
`test_spend.py` has documented the rule since it was written: `TestClient` runs the
app on a blocking portal with **its own event loop**, so a test that also writes rows
on pytest's loop ends up with one asyncpg pool shared across two — which aiosqlite
tolerates and asyncpg refuses. The suite is deliberately split, `database`-fixture
tests on one side and `TestClient` tests on the other.

`test_repurpose_endpoint.py` and two tests in `test_repurpose_workflow.py` did both.
Every one of them passed locally on SQLite and all eighteen failed on CI with
"attached to a different loop" — an error naming neither the file nor the cause.
They now call the endpoint functions directly, like `test_spend.py` and
`test_backlog.py` already did.

Two things made this expensive to find, and both are worth remembering. The failures
appeared in files whose own logic was fine, so the message pointed nowhere useful.
And the branch had no CI history at all — the pull request was opened after the work
was done, so the first run was also the first time any of it met Postgres.

**Run the suite against Postgres before pushing** if you have touched persistence or
added an endpoint test. `CLAUDE.md` has the command. It takes seven minutes and it
is the only way to see what CI sees.

### 4.11 The anthropic 1.x SDK broke the suite twice, in two different ways
`pyproject.toml` said `anthropic>=0.40`, so a fresh install resolved 1.0.0 — and six
tests failed on a clean clone while every existing environment stayed green.

Two independent breakages, one release:

* **The sampling keywords are gone from `messages.create()`.** Passing
  `temperature` is a client-side `TypeError` before any request is made, while the
  models our `temperature_policy` admits still accept it on the wire. The transport
  now sends it via `extra_body`, which merges into the request JSON as-is — the
  wire shape is unchanged, which is why `test_llm.py`'s body assertions needed no
  edits.
* **The HTTP layer moved from `httpx` to `httpx2`.** respx patches `httpx`, so
  every mocked Anthropic call silently stopped being intercepted and escaped to
  the real API — the failure was an `AuthenticationError` from api.anthropic.com,
  in a test suite. The fix is `httpx2.alias_httpx()` before anything imports
  `httpx`, which is earlier than it sounds: respx registers a pytest entry-point
  plugin that imports `httpx` before `conftest.py` loads, so `addopts = "-p
  no:respx"` blocks the plugin (the suite only ever uses `@respx.mock` directly,
  never the fixture) and the alias in `conftest.py` runs first. `alias_httpx`
  raises if it is too late, which is the failure mode you want.

The pin is now `anthropic>=1,<2` because the code is written against 1.x in both
places; a 0.x resolve would break the conftest import.

### 4.12 Five defects in the YouTube client, found by simulating Google
§1.1 says the resumable chunk loop and the `308` handling "cannot be reasoned about
without a real response". We still have no credentials, so `tests/
test_youtube_simulated.py` builds the response instead: a `respx` stand-in for
`oauth2.googleapis.com` and `www.googleapis.com` that keeps its own copy of the
bytes it has persisted, answers `308` with a `Range` header that is the only truth
about how much arrived, and refuses — with Google's 400 — any chunk that does not
continue what it holds. Reading the code found none of these. Running it against a
server that does not agree with us found all five.

* **A revoked channel burned 1,600 units per publish attempt.** `upload()` calls
  `ledger.reserve()` first, which is right — Google charges when the session opens.
  But `await self._headers()` sat *between* the reservation and the `try` that
  refunds it, and `_headers()` is a network call: an expired access token detours
  through `refresh()`, and a refresh that comes back `invalid_grant` raises
  `ChannelDisconnected` from there. So a channel whose consent had been withdrawn —
  the single most likely reason to be refreshing at all — booked a full upload's
  quota against a request Google never received. Six attempts, and the day's uploads
  were gone for uploads that could not have happened. The refresh is inside the
  `try` now.
* **A `Range` that went backwards ended the upload.** `_resume_offset`'s docstring
  says the server's `Range` "is authoritative and may confirm less than we sent, so
  it is the only thing worth believing"; the loop then acted on it only when it
  moved *forward* and re-sent from its own offset otherwise. A server that drops a
  buffered chunk therefore got a chunk starting past its data, which Google answers
  with a hard 400 — an unrecoverable end to a recoverable upload, after the 1,600
  units were spent. It now rewinds to whatever the server says it has, and counts
  the rewind against `MAX_CHUNK_RETRIES` so it cannot oscillate forever.
* **`thumbnails.set` sent no `uploadType`.** Required on every `/upload/` URI.
  `videos.insert` passes `resumable`; this passed nothing, so the 50 units bought a
  400 and the published video kept its auto-generated frame. Now `media`.
* **`captions.insert` sent `multipart/form-data`.** It was built with httpx's
  `files=`, and Google's media-upload protocol refuses that flavour by name — "Media
  type 'multipart/form-data' is not supported. Valid media types:
  [multipart/related]" — and also wanted the `uploadType=multipart` this call
  likewise omitted. No caption track this repository could have uploaded would have
  been accepted, at 400 units an attempt. `_multipart_related` builds the body by
  hand: metadata part first, media part second, identified by position rather than
  by a `name` field form-data would have added.
* **The upload progress callback never reached 1.0.** The final chunk answers 200
  and returns before reporting, so a four-chunk upload finished at 0.90. Cosmetic on
  a 3KB file; on a forty-minute upload the Create screen looks stalled at the exact
  moment it succeeded.

What this still does not prove is anything about Google's actual behaviour — see
§1.1, which stays open. The simulation is faithful to the documented protocol and to
nothing else, so a header Google requires that neither the docs nor we thought of is
exactly as invisible as it was before.

### 4.13 Four defects in the TikTok path, found by simulating TikTok
The same exercise as 4.12, against the other unproven API. §5.5 calls TikTok
"connectable but unproven": every piece of it had unit tests, and each of those
tests stubbed out the half of the system the *other* file was testing —
`test_tiktok_reliability.py` mocks the transport under one provider function,
`test_tiktok_account.py` mocks `tiktok.refresh` under the repository. Nothing ran
the two together. `tests/test_tiktok_simulated.py` removes both stubs and drives
the whole path — endpoint, repository, encryption, provider — against a `respx`
stand-in for `open.tiktokapis.com` that is faithful to TikTok's annoyances rather
than to a tidy REST API.

* **The OAuth token endpoint's error body crashed the client.** TikTok has *two*
  error shapes and we knew about one. The Display API nests it —
  `{"error": {"code": "access_token_invalid"}}` — and `_unwrap` read it that way,
  correctly, at HTTP 200. But `/v2/oauth/token/` speaks plain OAuth 2.0:
  `{"error": "invalid_grant", "error_description": "Refresh token is invalid or
  expired."}`, where `error` is a **string**. `"invalid_grant".get("code")` is an
  `AttributeError` — not `TikTokUnavailable`, not `TikTokAuthExpired` — so every
  `except` clause guarding this path missed it. The most ordinary failure this
  integration has, a refresh token that died after its year, produced a 500 and a
  traceback instead of "reconnect the account". `_error_in` now knows there are two
  shapes and is the only place that does; `invalid_grant` and `access_denied` join
  `_AUTH_ERRORS`, while `invalid_client` deliberately does not — wrong keys in
  `.env` are a configuration fault and reconnecting cannot fix them.
* **An outage while refreshing the token was a 500, one line from where it was a
  502.** `POST /discover` wrapped the sweep in handlers for both exception types,
  but wrapped `repository.tiktok_access_token()` in a handler for
  `TikTokAuthExpired` only — and acquiring that token is itself a call to TikTok.
  So a 503 during the refresh escaped unhandled, while the identical 503 four lines
  later came back as a 502 with a sentence. Worse than a wrong status code: §5.5
  records that the web app's `get<T>()` returns `null` on any non-2xx, so the 500
  rendered as "the engine is not running".
* **The rights chip on the card contradicted the guard that enforces it.**
  `clip_sources` selected grants `.order_by(created_at.desc())` and collected them
  into a dict — which keeps whatever it sees *last*, so descending order left the
  **oldest** grant standing. Grants append rather than replace (that is how "were we
  allowed to publish this, at the time" stays answerable), so the oldest is
  precisely the superseded one. A clip whose permission had been withdrawn came back
  from the repository as `cleared: true` while `record_asset` refused its media, and
  because `api.clips` re-reads the grant itself through `latest_grant`, the card
  disagreed with its own contents: a green chip beside a fatal "revoked" problem.
  Now ordered ascending, tie-broken on `id`, matching `grants_for` and
  `latest_grant`.
* **A clip's fit score depended on how many other clips were swept with it.**
  `_pooled_suggestions` runs one autocomplete sweep per seed caption and pooled the
  results without deduplicating, while `fit.score_clip` counts matches rather than
  distinct ones. Four captions about the same niche return largely the same phrases,
  so each was counted four times. Measured: the same clip scored 0.305 swept alone
  and 0.417 swept with three others, and its card claimed "12 YouTube autocomplete
  queries match this" where three did. `upsert_clip_sources` writes the new score
  over the stored one on every pass and the grid sorts by it, so the whole screen
  re-ranked itself for a reason that had nothing to do with the clips. Pooled
  through a dict now.

What this does not prove is anything about TikTok's actual behaviour. §5.5 stays as
it is: the app still needs review before credentials exist, the error codes in
`_AUTH_ERRORS` are transcribed from documentation rather than observed, and the
cursor semantics are simulated the way the docs describe them and no other way. The
test file ends with the full list, next to the code that would have to change.

### 4.15 TikTok sign-in never worked: PKCE was missing entirely
**The first defect in this file found by a person using the product**, and the
clearest possible demonstration of what §4.13 said a simulation cannot prove.

Pressing Connect landed on TikTok's own page reading *"Something went wrong —
We couldn't log in with TikTok"*, with one item in the small print: `code_challenge`.
TikTok requires PKCE on the authorize request and `authorize_url` never sent it.
Every simulated test passed throughout, because a fixture answers whatever it is
asked — the parameter TikTok wanted was one nobody had thought to send, so nobody
had thought to assert it either. That is exactly the failure mode §4.13's closing
paragraph named, arriving on first contact rather than in a test.

Two things had to be right, and only one of them is guessable:

* `authorize_url` now sends `code_challenge` and `code_challenge_method=S256`,
  and `exchange_code` sends the matching `code_verifier`. The verifier is
  generated per sign-in, kept in `_PENDING_STATES` beside the CSRF state, and
  never leaves the engine — only its hash goes to TikTok.
* **The challenge is hex-encoded, not base64url.** RFC 7636 §4.2 says
  `BASE64URL(SHA256(verifier))` and every other provider in this repo means that,
  so the obvious implementation is *accepted at the authorize step* and then dies
  at the token exchange with a bare `invalid_grant` naming nothing. TikTok's own
  Login Kit documentation is explicit — "You must use hex encoding of SHA256" —
  and their example is `CryptoJS.SHA256(code_verifier).toString(CryptoJS.enc.Hex)`.
  `code_challenge_method` is still `S256`, because that names the *hash* and not
  the encoding, which is precisely why this is easy to get wrong. A test pins the
  hex form and asserts it differs from the base64url one, so a well-meaning
  "correction" to the spec fails loudly.

The flow around it was tightened at the same time, because the reason to connect
TikTok is on the Repurpose screen and the only button that could was on Setup.
A sweep that finds nobody signed in now offers the connection in place, the
round trip returns to the screen it started from (`return_to`, allowlisted —
it reaches a `Location` header, and reflecting an arbitrary path there is an open
redirect), and both outcomes are stated on arrival instead of leaving "did that
work?" to be inferred from an empty grid.

### 4.16 The consent page opened in the wrong window, and a bad key said nothing
The follow-on from 4.15. With PKCE in place TikTok stopped complaining about
`code_challenge` and started complaining about `client_key` — a different fault,
on the same unhelpful page, and one the app was in a position to catch first.

* **Credentials are stripped.** `Settings` now sets `str_strip_whitespace`. A key
  pasted with a trailing newline is invisible in an editor and fatal at the other
  end, and no provider says so: TikTok answers `client_key`, Google answers
  `invalid_client`, and the operator is left comparing two values that look
  identical.
* **The obvious faults are named before the browser leaves.**
  `tiktok.credential_problem()` refuses a placeholder still sitting in `.env`,
  the same string in both fields, and a missing half — the three that are
  unambiguous. Deliberately *not* a format check on the key: TikTok's keys
  currently start `aw` and run about twenty characters, but that is an
  observation about today's keys rather than a documented contract, and refusing
  a valid future key would be worse than the error page it replaces.
  `configured()` was the only gate before, and it asks nothing except whether the
  string is non-empty.
* **The Setup card shows which key is configured** — first two characters and a
  length, never the value, since it reaches a browser and a screenshot. That is
  enough to spot the three things that actually happen: the wrong field pasted, a
  truncated copy, a key from a different app.
* **Consent opens in a real tab.** Every OAuth start used
  `window.location.href`, which navigates whatever window the app is in — and
  Studio is commonly launched through its desktop shortcut as an app-mode window
  with no address bar. The consent page then loads somewhere the operator cannot
  read a URL from, cannot copy an error out of, and which may not carry the
  browser session they are actually signed in with. `lib/consent.ts` opens the
  tab **synchronously on click**, before the server action is awaited, because a
  `window.open` after an `await` is precisely the shape a popup blocker stops;
  the URL is assigned into the already-open tab when it arrives, and a blocked
  window falls back to navigating in place rather than doing nothing.

### 4.17 `noopener` made the new tab impossible, and the outcome never came back
Two problems from 4.16's fix, one a defect and one a design that stopped short.

**`window.open` returned `null` every time.** The feature string was
`"noopener,noreferrer"`, and returning `null` is *what `noopener` means* — the
whole point of the flag is to withhold the handle. It is not a mistake you notice
by looking, because the window still opens. So the handle was always null, and
both branches of `send` were wrong at once: the blank window was orphaned on
screen with nothing ever assigned into it, and every consent URL fell through to
the "popup was blocked" path, which navigates the app's own window. The visible
symptom was an abandoned `about:blank` tab sitting next to a consent page in
exactly the window the fix existed to avoid using. `lib/consent.test.ts` asserts
the absence of that one word.

**The window that opened consent never heard the result.** The engine's callback
redirected to `/setup?connected=1`, which was right when consent had replaced the
app's own window and wrong the moment it did not: the redirect landed in the new
window, which became a second full copy of Studio, while the window the operator
was actually looking at — the one with the button they pressed — sat unchanged
until they reloaded it by hand.

The shape every large integration converged on, and what each part is for:

* **A popup, sized and centred on the window it came from**, rather than a tab.
  The app stays visible behind it, so the screen they started from is still there.
  Centred with `screenLeft`/`screenTop`, which is the current monitor rather than
  the primary one.
* **A handoff page, `/connected`, that nobody is meant to see.** Both callbacks
  redirect there; it posts the outcome to `window.opener` and closes itself. Two
  hundred milliseconds, no chrome.
* **Three independent ways to learn the outcome**, because each alone fails on
  some real browser: the `postMessage`, which is instant and carries the reason; a
  poll of the engine, which is the *only* signal that survives
  Cross-Origin-Opener-Policy severing `window.opener` — increasingly the default,
  and the reason a message-only implementation hangs on a spinner while the
  connection it is waiting for has already succeeded; and watching for the window
  closing, which catches someone giving up. The close-watch carries a 2.5s grace
  period, because `/connected` closes itself the moment it has posted, which can
  be *before* a poll issued a moment earlier comes back — without it a successful
  connection is reported as abandoned.
* **The no-popup path is unchanged.** When there is no opener to tell — the popup
  was blocked, or the browser severed the link — `/connected` forwards to exactly
  the URL the callback used to redirect to, query string and all. The Setup
  screen's handling of `access_denied` is several paragraphs of hard-won wording
  and the fallback is where a person most needs it; rewriting that contract to
  suit the popup would have meant maintaining the explanation twice.
* **`return_to` is an allowlist**, checked in `api/oauth_return.py`. It arrives
  from a query string and lands in a `Location` header, and reflecting it
  unchecked is an open redirect — worth more to a phisher than the account being
  connected, since the victim walks through a real consent screen first.

Both screens now report progress while consent is open ("Waiting for Google…",
"Approve it in the Google window. Closing that window cancels") and settle in
place, without a navigation. Closing the window without finishing is reported as
what it is — nothing happened — rather than as an error.

Not fixed by any of this: TikTok still answers `client_key` on this install. The
request is well-formed, the pre-flight passes and the engine logs the exact URL,
which leaves the app's registration on TikTok's side — see 4.16 and the checklist
on the Setup card.

### 4.14 Three gaps the simulations named but did not close
Each was written down as a known limit when 4.12 and 4.13 landed, and each was a
small fix once someone looked at it rather than a research problem.

* **An access token expired mid-upload.** `_headers()` was resolved once, before
  the chunk loop. A token lasts an hour and a 2GB master on a domestic upstream
  does not fit inside one, so every chunk after the hour mark went up with a dead
  credential — a 401, which the loop does not retry, on an upload whose 1,600
  units were already spent. The headers are re-derived per chunk now, which
  refreshes only when the token is actually within 60s of expiry.
* **Chunk PUTs carried no `Authorization` at all.** The resumable protocol says
  the unguessable session URI is the authorisation, which is true as documented —
  but Google's own client library sends the header anyway, and a stricter tenancy
  would be entitled to require it. If it ever is required the failure is a 401 on
  chunk one, which is an expensive way to find out something that costs nothing
  to prevent.
* **A grant could be recorded and enforced but never withdrawn.** The rights model
  had `revoked_at` from the beginning, `record_asset` refused media without a live
  grant, and the card knew how to draw a revocation — and no endpoint set one. The
  only route was writing to the repository by hand, which is not a route: a
  creator who changes their mind is the most ordinary rights event there is.
  `POST /v1/repurpose/clips/{id}/revoke` appends a revoked copy rather than
  mutating the standing grant, for the same reason `record_grant` appends — the
  old row is what answers "were we allowed to publish that, at the time" — and the
  Repurpose card grew a two-press Revoke control beside it.

One thing found on the way, in `Grant.as_dict`: it serialised its timestamps with
a bare `.isoformat()`, while every *comparison* in that module goes through
`_aware`. So the same revocation left the API as `…+00:00` when it was still in
memory and as `…` with no offset once it had been through SQLite — and
`new Date("2026-08-28T09:43:20")` is parsed as **local** time by every browser
while the offset form is parsed as UTC. The same event rendered hours apart
depending only on whether it had been persisted yet. Postgres returns aware
datetimes and hides it, which is the same reason `_aware` exists at all.

---

## 5. Known-imperfect, working as intended for now

### 5.1 ~~Subtitles lose punctuation~~ — fixed, by aligning to the written script
edge-tts reports what it *said*, not what it read. That cost punctuation
(`On purpose Here is why`) and, worse, every numeral: a script saying `$50,000 on
October 5, 2018` produced the on-screen caption **"fifty thousand dollars"** and
**"October fifth twenty eighteen"**. Both were visible in rendered frames from a real
job. Numerals matter twice over — a caption track that spells them out reads like a
transcript of a phone call, and width is the scarcest thing on a 9:16 frame.

`media._restore_written_forms` now walks the written script as the authority and
consumes cues to match, emitting the token *as written*. Punctuation comes back free,
because the written token already carries it. A token containing a digit or a currency
symbol swallows the spoken run that follows — capped by `_spoken_word_count`, because
greedy consumption let `5,` eat "fifth twenty eighteen" and the following `2018.` then
ate a real word out of the script. Timing is taken from the first and last spoken word,
so nothing drifts, and badly-drifted alignment falls back to the old punctuation-only
pass rather than shipping captions out of sync.

Still not recovered: commas and quotes *inside* a sentence, when the alignment falls
back. MoneyPrinterTurbo's version is in
`vendor/moneyprinterturbo/app/services/voice.py:_match_script_line`.

### 5.2 Publish-time scheduling is a heuristic
YouTube exposes no hourly "when your viewers are online" dimension publicly. The
scheduler measures **weekday** from real data and **estimates hour-of-day** from a
built-in evening-weighted curve. The API labels this `measured_weekday_only` and the
UI says "estimated" rather than implying precision it does not have.

### 5.3 Thumbnail backgrounds are generated — and so is B-roll for unmatched beats
Backgrounds come from Gemini 3 Pro Image, also sold as Nano Banana Pro, through
[providers/images.py](apps/engine/engine/providers/images.py), reusing
`OPENAI_API_KEY`/`GEMINI_API_KEY` rather than adding a key. With neither set the
composition falls back to a flat panel, so a keyless clone still gets a thumbnail.

Both transports have now been called against a live API and both work, so the
"unproven" caveat that used to sit here is gone. Gemini 3 Pro Image is preferred over
GPT Image 1 on all three counts that matter: better output, cheaper ($0.134 against
$0.19), and native 16:9 where GPT Image returns 3:2 and has to be cropped.

`MaterialsStage` also generates when a beat has no stock match, which satisfies
CLAUDE.md's "hero shots get generative B-roll". This is not a nicety — no stock
library has footage of a named person, so a video about a specific creator used to
fill its beats with whatever a truncated query happened to return. One real beat
asked for a subscriber counter and got coloured paper clips. Generation is metered
per image and reported in the stage's `cost_usd`, so the per-video ceiling sees it.

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

### 5.5 ~~The web app runs entirely on demo data~~ — every screen now reads live data
This entry and the header above used to disagree with each other — one said "every
screen renders from demo.ts", the other "the web app reads live data". Neither was
right. Per screen, as of today:

| Screen | Data |
|---|---|
| Create | live; `POST /v1/jobs` then SSE, falling back to `DEMO_JOB` if the create fails |
| Queue, Library, Models | live, falling back to `demo.ts` when the engine is unreachable |
| Setup, Welcome | live only — no fallback, deliberately. They show "the engine is not running" instead of plausible fiction, because a setup screen that invents its own state is worse than one that admits it is blind |
| Calendar | live: quota, bookings **and the draggable tray** — `GET /v1/calendar/pending` serves rendered-but-unpublished videos server-side (completed video jobs minus anything a publish job has uploaded or is uploading), so a scheduled chip's title resolves to the video actually booked. Demo tray only when the engine is unreachable |
| Analytics | live per section, each with its own badge: monetisation, spend and the weekly review as before; the tiles read `GET /v1/analytics/daily`; findings read `GET /v1/insights`; the retention map and Short-cut panel anchor to the newest published video via `GET /v1/analytics/retention/{id}` and `/analytics/shorts/{id}`; the per-video table reads the new `GET /v1/analytics/videos`. Sections without live data (no channel, nothing published) fall back to the demo fixture and say so — the numbers need a connected channel to be real |
| Series | **live.** `GET/POST/PATCH/DELETE /v1/series` exist and the screen uses all four; each active card's warning line is the run planner's own verdict from `GET /v1/series/{id}/plan` — the first production caller `plan_week` has ever had. "New series" is a real form; Pause/Resume/Remove are back and wired |
| New channel | **live.** "Design it" calls `POST /v1/channels/launch`, which now runs as a background task the screen polls (it used to run seven LLM stages inside one request); progress renders as a pipeline; finished designs are persisted and resumable from the input screen; "Create series" materialises the launch's series plan through the series endpoints; "Apply description & keywords" calls `/launch/apply` and surfaces its 409s verbatim |
| Connected | no data at all, and nothing to fall back to. It is the OAuth handoff page (§4.17): it reads its outcome from the query string the engine's callback put there, hands it to the window that opened the popup, and closes. Visible only when a browser refuses `close()` |
| Repurpose | **live end to end.** "Find clips" runs a sweep, and clips, grants and the episode builder all read and write the engine; "Build episode" starts the `repurpose` workflow and links to the running job. Until the sweep button existed the screen said "nothing has been swept in yet" above no control that swept anything, which left the whole TikTok path unreachable from the UI while every part of it worked. Two honest limits remain: the pre-check shows a *real* rights verdict but only a projection of originality, because narration, cuts and audio are decided while it builds — the card says so; and the standalone originality card lower down is still the `demo.ts` fixture, labelled "example report", since it describes no particular episode. Falls back to `demo.ts` wholesale when the engine is unreachable, with every write disabled and carrying its reason. TikTok itself is **connectable but unproven**: OAuth, token refresh, pagination and error handling are all implemented and unit-tested against mocked responses, and none of it has been run against TikTok — the app needs review before credentials exist. §1.1's argument, for a second API |

The disabled "New series"/"Create series" buttons this section used to document —
kept disabled-and-explained because the series endpoint did not exist — are live
now that it does. What the live analytics sections still need to show real numbers
is a connected channel and published videos (§1.1), which no amount of wiring
supplies.

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

### 5.8 ~~Two surfaces exist, cost something, and are read by nothing~~ — both wired
Both were long-standing "recorded rather than fixed" entries; both are fixed:

**Channel launches are persisted.** The loader rewrite this entry said was needed
happened: `load_launches` returns the payload unflattened, `api/channels.py` saves
after every stage boundary and `restore()` (called from the lifespan handler)
rebuilds the `states`/`events`/`inputs` mirror with `load_states`. A launch that was
mid-run at shutdown comes back `interrupted` rather than pretending to still run.
`GET /v1/channels/launches` lists stored designs so the New channel screen can
resume one — the point, since the manual steps take days. Covered by
`tests/test_launch_persistence.py`.

**`ChaptersStage` output reaches YouTube.** `seo.append_chapters` appends the
chapter block to the description at upload time — the first point where both the
written description and the render-timed chapter list exist — with the 5000-byte
ceiling enforced all-or-nothing: a truncated chapter list misdescribes the video,
and the description is prose someone may have edited, so neither is ever cut.
`UploadStage` records `chapters_appended` in its provenance so "why does this video
have no chapters" is answerable. Covered by `tests/test_chapters_append.py`.
`SeoPackage` remains unconstructed and is now the only vestige of the old design.

### 5.9 On Windows, the `0o600` on `.env` and the key file does nothing
Both writers ask for owner-only access — `api/setup.py` chmods the temp file before
the atomic rename, and `crypto.py` creates the key with `O_EXCL` and mode `0o600`.
On Windows neither takes effect. `os.chmod` there only toggles the read-only
attribute, and `os.stat` synthesises `st_mode` as `0o666` for every writable file
regardless of who can actually open it. So the mode bits are neither honoured nor
observable, and the two tests that assert on them
(`test_the_file_is_not_world_readable`, `test_the_generated_key_is_not_readable_by_other_users`)
are skipped on `os.name == "nt"` — they were failing every Windows install, and
passing them would have meant testing Python's emulation rather than the file.

What decides who can read either file on Windows is the NTFS ACL it inherits from
its directory. This section used to argue that inheritance was good enough in
practice, on the grounds that a clone under a user profile — `C:\Users\<you>\...`,
where anyone who double-clicked `Install Studio.cmd` from their Downloads folder
ends up — inherits an ACL that already excludes other non-administrator users.

**That was wrong, and the machine it was written on was the counterexample.** A
sandboxing tool had added an explicit `(OI)(CI)` ACE granting a service group Read
& Execute on `C:\Users\<user>\Downloads`, so `.env` came out as:

```text
.env  Phantomen\CodexSandboxUsers:(I)(RX)
      NT AUTHORITY\SYSTEM:(I)(F)
      BUILTIN\Administrators:(I)(F)
      PHANTOMEN\IsacC:(I)(F)
```

Two other local accounts could read every API key and the OAuth client secret. A
profile directory is a *convention* about permissions, not a guarantee, and the
tools most likely to add an inherited read ACE — sandboxes, MDM, backup agents — are
exactly the ones a developer machine collects.

**Fixed.** `engine/secretfile.py` sets an explicit DACL at both write sites:
`icacls /inheritance:r` followed by full control for the owner's SID, SYSTEM and
Administrators. SYSTEM and Administrators are kept deliberately — an administrator
can take ownership of any file regardless, so dropping them protects nothing and
breaks backup and antivirus tooling.

Two things about it are worth knowing:

- **It never raises.** It runs after the credential is already on disk, and a file
  with a wider ACL than intended is a smaller problem than a broken install.
- **It verifies its own work.** `/inheritance:r` strips before it grants, so a grant
  that fails halfway leaves a file readable by nobody — including the engine on its
  next start. If the tightening cannot be confirmed by reading the file back, it is
  reverted with `icacls /reset` and warned about.

That self-check exists because CI cannot cover this: every workflow is
`ubuntu-latest`, so `test_the_acl_keeps_only_the_owner_system_and_administrators`
is skipped everywhere except a developer's own Windows machine. The revert path is
tested on all platforms by driving the Windows branch directly.

The two `st_mode` tests above stay skipped on Windows — they assert on Python's
emulation, which is still not the mechanism.

**Existing files are not retro-fixed.** The ACL is applied when a file is written,
so a `.env` that predates this keeps whatever it inherited until the next key save.
To tighten one in place:

```powershell
icacls .env /inheritance:r /grant:r "$($env:USERNAME):(F)" /grant:r "*S-1-5-18:(F)" /grant:r "*S-1-5-32-544:(F)"
```

From Git Bash the same line needs `MSYS_NO_PATHCONV=1` in front of it and
`"$USERNAME:(F)"`, or the `/inheritance:r` argument is rewritten into a path.

---

## 6. Not built at all

- ~~**No Postgres.**~~ Done — SQLAlchemy models, Alembic migrations, and a quota
  ledger that survives a restart. AUDIT.md §5.1.
- ~~**No arq workers.**~~ Done — `engine/worker.py`, events over Redis pub/sub.
  The in-process path is kept as a supported single-process mode. AUDIT.md §5.2.
- ~~**No auth / no engine authentication.**~~ Done — `STUDIO_API_TOKEN`, checked by
  `engine/auth.py` on every route except `/health` and the two OAuth callbacks
  (Google's and TikTok's servers redirect a browser straight into those; neither
  can be made to carry a header, and their own `state` parameter is the CSRF
  defence an OAuth callback actually has). Empty by default — every install before
  this field existed, and every test in this suite, keeps working with the gate
  off. Set it (and `NEXT_PUBLIC_STUDIO_API_TOKEN` to the same value) to turn it on.

  One honest compromise from FIX-TASKS.md's original phrasing ("never exposed to
  the client"): two route families — `/v1/files/...` (rendered thumbnails and
  videos, read via `<img>`/`<video>`/`<a href>`) and `/v1/jobs/{id}/events` (the
  SSE progress stream, read via `EventSource`) — are reached by the browser in ways
  that cannot attach a header at all. Routing every render and every progress
  frame through a hand-rolled streaming proxy in the web app, just to keep one
  value out of a URL that only this machine's own browser ever sees, was judged
  not worth it for a tool CLAUDE.md already says not to expose. Those two route
  families additionally accept the token as `?token=`, which is weaker (it can
  land in a server log or browser history) — documented in `engine/auth.py`
  rather than silently accepted. Every write and every credential-bearing route —
  `PUT /v1/setup/keys` included — takes the header only, which only server-side
  code can supply.
- **No ⌘K command palette**, though the design spec leans on it to keep screens
  sparse.
- **No thumbnail A/B swapping**, which Phase 8's attribution is otherwise ready for.
- **No trend monitoring.** The idea backlog accepts a `trending_terms` argument that
  nothing currently supplies, so `freshness` is zero on every real idea and its
  decay curve has nothing to decay. The scoring is ready for a supplier; there
  isn't one.
- **The weekly review sends no notification.** It *does* have a screen — the
  `WeeklyReview` card sits at the top of Analytics, with an honest `NoReviewYet`
  state that distinguishes "no worker running" from "engine unreachable" (this
  entry used to claim no screen existed; that was stale). What is still missing is
  a push: `Review.worth_reading` is there for a notifier that does not exist yet —
  most weeks it is false, which is the point.
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
