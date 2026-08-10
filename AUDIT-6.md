# Audit 6 — what to build next

Written after PR #8 and #9 merged/opened. This is a *different* list from
[AUDIT-5.md](AUDIT-5.md) Part 2: that one ranked ten features found by watching a run
fail. This one comes from asking a narrower question — **what can the engine already
do that no screen can reach?** — and the answer turned out to be most of a product.

Ranked by impact divided by effort. Every claim below was checked against `main`.

---

**Status.** 1, 2, 4 and 7 are built and verified against a running engine. 3 and 6
are built but **not verified end to end** — the playlist picker needs a connected
YouTube channel and the notification needs a completed render, and neither was
available on the machine this was written on. **5 and 8 are open**, and they are the
two large ones; 8 explicitly wants to land with 5 rather than before it.

Each `✅` heading below carries the same distinction. The prose under a heading
describes the state that motivated the work, which is why it is written in the past
where the thing has since been built.

**Found while building #2, and worth its own line:** `database_url` defaults to the
*relative* `./storage/studio.db`, and the engine runs from `apps/engine` while the
documented alembic command ran from the repo root. So migrating created a second,
empty database and reported success, and the app 500'd on a missing column with a
traceback that never mentioned which file it opened. The command in CLAUDE.md is
fixed; the underlying footgun — a relative database path in an app that legitimately
starts from two directories — is not. `env_path()` already solves exactly this for
`.env` by checking both locations. Storage deserves the same and did not get it here,
because moving where a database lives is not a change to make at the end of a long
session.

## The finding behind most of this

The engine has capabilities with no dial. `Settings` has twenty-eight fields; the web
app mentioned none of these until the Style screen:

| Field | Default | What it decides |
|---|---|---|
| `tts_voice` | `en-US-AvaNeural` | who narrates, on every video ever made |
| `tts_provider` | `edge` | — |
| `bgm_enabled` | `False` | whether there is any music at all |
| `bgm_volume` | `0.12` | how loud |
| `subtitle_font` | `""` | what the captions look like |
| `ken_burns` | `alternate` | how stills move |
| `transition_fade_s` | `0.0` | hard cuts, always |
| `image_provider` | `auto` | which model draws the B-roll |

So every video this install has ever produced is narrated by the same voice, silent,
in the default font, with alternating Ken Burns and hard cuts — and there is no way
to change any of it short of editing `.env`. The render engine honours all of it
already. This is the single largest gap between what Studio *can* do and what it
*offers*.

It is also a monetization concern, not only a taste one. CLAUDE.md names YouTube's
inauthentic-content policy as targeting mass-produced templated content; a channel
where every video shares its voice, silence and typography with every other install's
defaults is the definition of templated.

---

## 1. Channel style — voice, music, captions, motion ✅ done

**Pros** — The engine already does all of it, so this is a screen rather than a
feature. Largest perceived quality jump per hour of work available anywhere in the
codebase, and the direct answer to "the video is garbage". Pairs with the channel
identity idea (#5): style is half of what a channel *is*.

**Cons** — Needs restraint. CLAUDE.md says expose the three things that actually
vary, and edge-tts alone offers hundreds of voices, so the picker has to be curated
and previewable rather than a dropdown of everything. BGM needs tracks on disk —
`bgm_dir` exists, nothing ships in it, and licensing is the operator's problem, which
the UI has to say plainly rather than implying Studio provides music.

**Effort** — Small to medium. The voice preview is the only real work.

---

## 2. A screen for the weekly review ✅ done

The cron runs Monday 06:00 UTC, `review.run()` produces findings, and the only way to
read them is the API or the worker log. `Review.worth_reading` exists for a notifier
that was never built.

**Pros** — Phase 8's entire feedback loop currently terminates in a log file. This is
the payoff for work already done, and the data is a dict away. Now unblocked: the
cron never fired at all without a worker, and `npm start` runs one as of PR #9.

**Cons** — Must be honest about quiet weeks. `worth_reading` is false most weeks *by
design*, and a screen that manufactures an insight every Monday to look busy is worse
than no screen — it trains you to ignore it.

**Effort** — Small.

---

## 3. Choose a playlist when publishing ✅ built, E2E unverified

`PlaylistStage` is complete and always skips: it returns early unless
`ctx.inputs["playlist_id"]` is set, and `playlist_id` appears nowhere in the web app
or in `packages/contracts`.

**Pros** — Pure wiring. Playlists drive session time, which is the metric YouTube
rewards hardest, and the upload half is already written and tested.

**Cons** — Needs a playlist list from the Data API (small quota cost) and a control
in the publish flow, which is the one screen that must stay uncluttered.

**Effort** — Very small.

---

## 4. Spend over time ✅ done

Cost is metered per stage and capped per video, and there is no answer to "what has
this channel cost me this month". `automation.SpendLedger` is written — record,
window, total — and nothing calls it.

**Pros** — Cost is the constraint that decides whether this is usable at volume.
Complements AUDIT-5 #5 rather than duplicating it: that one is a ceiling for the video
in front of you, this is the bill.

**Cons** — ~~`SpendLedger` is in-memory, so it needs a persistence story.~~ It did
not need one. The plan above was wrong: `jobs.cost_usd` already holds the number,
written by `save_job` from what the stages actually spent, so this reads the jobs
table and `SpendLedger` stays where it is — unused, and belonging to #5.

**Effort** — Small to medium.

---

## 5. Series — and it is less work than AUDIT-5 said

**A correction.** AUDIT-5 #9 called this "an entire feature from scratch". That was
wrong. `automation.py` already contains `Series`, `VideoState`, `WeekPlan`,
`BudgetPolicy`, `SpendLedger`, `check_budget`, `publish_blockers`, `resolve_stage` and
`plan_week` as pure, testable logic. `main.py` imports the module and uses exactly
three names from it: `Series`, `VideoState`, `publish_blockers`.

What is missing is endpoints, persistence and UI — not the domain model.

**Pros** — This is the automation half of the product's premise, and the hard thinking
is done. It is also what the channel-identity idea needs to live in.

**Cons** — Still the largest item here, and the genuinely useful version — unattended
publishing — collides with CLAUDE.md #3's approval gate, which is a product decision
before it is a coding one.

**Effort** — Medium to large. Down from large.

---

## 6. Tell me when the render is done ✅ built, E2E unverified

A long-form render takes tens of minutes. The window gets closed or buried, and there
is no signal when it lands.

**Pros** — The SSE stream is already open and already knows. The Notification API is a
permission prompt and about fifteen lines. It is the difference between babysitting a
render and starting one.

**Cons** — Browser notifications need a user gesture to request permission and are
silently useless if the tab is closed entirely, which is exactly when they would help
most. A durable version wants the worker to send it, not the page.

**Effort** — Very small for the browser version.

---

## 7. A backlog of ideas that persists and depletes ✅ done

The Create screen researches and scores ideas, shows them once, and forgets them.

**Pros** — Turns a suggestion box into a plan. Cheap on top of `api/ideas.py`, which
already does the expensive half.

**Cons** — Needs a table and a notion of "used", and it overlaps with Series enough
that building it twice would be waste.

**Effort** — Small.

---

## 8. Channel identity as an object every video is generated against

Positioning, audience, content pillars, banned topics — stored once, fed to every
prompt in the chain.

**Pros** — The strongest idea to come out of reading what other people recommend for
this problem, and the thing that separates a channel from seventeen unrelated videos.
Would improve every existing stage's output without changing any of them.

**Cons** — Prompt surface area grows everywhere at once, and a bad identity makes
every video worse rather than one. Wants to land with Series, not before it.

**Effort** — Medium.

---

## Still true from AUDIT-5

Not repeated above, still open, still ranked as they were: edit a stage's output
before the next one runs (#3 there — the largest single lever on output quality, and
`editStage` still has no caller in any `.tsx`), authenticate the engine (#4), a real
trend source so `freshness` stops being a dead 15% of every idea score (#6), thumbnail
A/B (#7), cut Shorts from finished long-form (#8), ⌘K (#10).

Part 3's smaller items are unchanged, plus the type-scale drift recorded there after
PR #9.
