# Repurpose — plan

**Goal.** A new tab that finds short-form clips (TikTok first) that fit a selected
channel, clears the right to use them, rebuilds them into something YouTube will
monetise, and hands the result to the pipeline that already exists.

This document is the design. **R0–R5 are built apart from the compositing step**
— the rights ledger, discovery, fit scoring, Lane A acquisition, cut selection,
the originality gate and its enforcement at the publish gate all run. What remains
is the render itself; see §7. Read
[the finding](#1-the-finding-that-reshapes-the-brief) first — it changes one of the
four stated requirements, and the reason is technical, not squeamish. Then
[RESEARCH §2](REPURPOSE-RESEARCH.md#2-the-correction-permission-does-not-satisfy-the-reused-content-policy),
which corrected this document once already.

---

## 1. The finding that reshapes the brief

The brief asked for four things. Three are ordinary product work. The fourth —
*"altered so it doesn't get detected for reupload"* — is asking for two different
things at once, and they pull in opposite directions:

| | Judged by | Beaten by |
|---|---|---|
| **Content ID** (copyright) | an algorithm, at upload | having the licence |
| **Reused-content review** (monetisation) | **a human**, at YPP application and on appeal | actually transforming the video |

The evasion approach — mirror, crop 3%, speed to 1.02×, pitch-shift, re-encode —
targets neither of them successfully:

- **Content ID fingerprints are explicitly robust to re-encoding, pitch shifting,
  mirroring and overlay.** They are derived from audio and visual features, not file
  bytes, and have been tuned against circumvention attempts for more than a decade.
  The transforms that defeat a naive hash do not defeat the thing that is actually
  looking. ([Content ID matching](https://qu3ry.net/articles/content-anchoring/youtube-content-id), [partial-copy fingerprinting research](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0166047))
- **The monetisation gate is a human reviewer**, and a mirrored TikTok with a
  1.02× speed-up reads as a mirrored TikTok to a person in about two seconds.
  Worse, the tell-tale of evasion transforms — uniform templating applied at volume
  — is *precisely* the signal the July 2025 policy rename was written to catch.
  Enforcement is **channel-wide**: monetisation is suspended for the whole channel,
  with a 30-day wait before reapplying. ([channel monetization policies](https://support.google.com/youtube/answer/1311392?hl=en), [policy rename coverage](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/))

So evasion is a strategy that spends real engineering effort to fail two checks
instead of one, and it stakes the entire channel rather than one video. I'm not
building fingerprint-evasion transforms — not primarily as a policy stance, but
because they don't work and the downside is the whole asset.

**What replaces it is not weaker, it is the same feature aimed correctly.** The
transformations that *do* clear both gates are the ones that make the video good:
original narration over the clip, an editorial thesis binding several clips, a
replaced audio bed, a real edit. The plan below builds those, plus the two gates the
brief was gesturing at — **rights provenance** so no claim is warranted in the first
place, and a **transformation gate** that measures whether a human reviewer would
call the output original. Everything else in the brief is built as asked.

---

## 2. Research: the six external constraints

These are load-bearing. Each one kills a design that ignores it.

### 2.1 Reused content is monetisable — with a specific, human-judged bar

The reused-content policy did **not** change in July 2025 and remains permissive in
principle: compilations, reactions, clips and commentary stay monetisable *when a
viewer can tell there is meaningful difference between the original and yours*.
What must be present is added value a person can point at — editing that tells a
story, commentary with genuine analysis, educational framing.
([policy](https://support.google.com/youtube/answer/1311392?hl=en), [practitioner guide](https://vidiq.com/blog/post/youtube-reused-content-policy-guide/))

**Design consequence:** the product's job is to *manufacture and then measure* that
difference. That is the Originality Gate (§6).

### 2.2 "Inauthentic content" targets templating and scale, not AI

On 15 July 2025 the "repetitious content" policy was renamed "inauthentic content".
The bar it draws: content made "with a template with little to no variation" or
"easily replicable at scale" is ineligible. AI tooling is explicitly welcome — the
requirement is unique human-added value in the finished video.
([Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/), [Fliki summary](https://fliki.ai/blog/youtube-monetization-policy-2025))

**Design consequence:** the gate must score *across the channel's corpus*, not just
per-video. Twelve structurally identical clip-compilations in a week is the failure
mode, and no single one of them looks bad in isolation. We already have semantic
dedup (`tests/test_semantic_dedup.py`) — it gets a second job here.

### 2.3 TikTok's official APIs will not hand you other people's videos

- **Display API** returns only the *authenticated user's own* content.
- **Research API** is restricted to approved academic researchers, with usage terms.
- Neither returns raw media files for arbitrary creators.

([TikTok developer docs](https://developers.tiktok.com/), [Research API guide](https://developers.tiktok.com/doc/research-api-get-started))

**Design consequence:** there is no compliant "search all of TikTok and download it"
button, and the plan must not pretend otherwise. Acquisition is split into four
lanes with different rights bases (§5.1). Discovery — finding *what is trending* —
is a separate, entirely legitimate problem solved with public trend data.

### 2.4 Creators own their posts; posting publicly grants you nothing

Copyright vests in the creator at creation. The platform gets a licence to
distribute within its own ecosystem; that licence does not extend to you. A tag, a
like, a public setting, or a branded-hashtag entry grant no commercial licence.
Reuse needs explicit, preferably written, permission with a recorded scope.
([UGC rights guide](https://www.digitalapplied.com/blog/ugc-rights-licensing-framework-2026-brand-creator-guide), [usage rights overview](https://brands.joinstatus.com/ugc-usage-rights))

**Design consequence:** `rights_basis` is a required, non-nullable field on every
clip, and the acquire stage refuses to fetch media without one. This is the single
most important constraint in the data model.

### 2.5 TikTok audio is licensed for TikTok — the audio is the trap

This is the constraint most likely to cost real money. TikTok's music licences are
TikTok's. Lifting a video with its original sound and putting it on YouTube means the
music has **no licence coverage on YouTube** and will very likely draw a Content ID
claim. TikTok's *Commercial Music Library* is the one library cleared for
cross-platform use; general trending sounds are not.
([Soundstripe](https://www.soundstripe.com/blogs/can-i-use-tiktok-music-on-youtube-and-other-platforms-why-you-cant-and-what-to-do-about-it), [CML guide](https://www.socialrevver.com/blog/tiktok-commercial-music-library))

**Design consequence:** **full audio-bed replacement is mandatory and automatic**,
not an option. Source audio survives only as identified diegetic/speech content, and
only when the rights basis covers it. This is also, conveniently, one of the largest
contributors to the transformation score — a legal necessity that happens to be
scored as originality.

### 2.6 A third-party watermark is independently disqualifying

Shorts carrying watermarks or logos from other platforms are not eligible for
monetisation, separately from any copyright question. A creator's own TikTok can go
to Shorts and qualify — *without the TikTok watermark*.
([Shorts monetization policies](https://support.google.com/youtube/answer/12504220?hl=en), [Shorts Fund eligibility reporting](https://digiday.com/future-of-tv/youtubes-creator-fund-for-youtube-shorts-will-not-exclude-videos-posted-to-other-platforms/))

**Design consequence:** a watermark scan is a **hard block** in the gate, and the fix
is sourcing clean masters (Lane A/B), not cropping. Note the asymmetry that matters
morally and legally: removing a watermark from a video you have rights to is
housekeeping; removing one from a video you don't is still infringement with the
label filed off. The gate checks rights *before* it checks watermarks so the second
never reads as a solution to the first.

---

## 3. Audit: what this repo already gives us

The feature is far cheaper than it looks, because roughly two-thirds of it exists.

| Need | Already there | Notes |
|---|---|---|
| Staged, resumable, editable pipeline | `workflows/base.py` | Stage/Workflow, staleness propagation, retries, heartbeats. New stages plug straight in. |
| Provenance on every artifact | `base.Provenance` | Enforced by the framework — a stage returning none fails loudly. Non-negotiable #2 comes free. |
| Cost ceiling per run | `base.Workflow.run(budget_usd=…)` | Pre-flight refusal before spend. |
| Script / hook / beats chain | `workflows/script.py` | Reused verbatim for the commentary track. |
| TTS, subtitles, materials, render | `workflows/media.py`, `render/compose.py` | The assemble stage extends rather than replaces. |
| SEO package | `workflows/seo.py` | Reused; description gains a mandatory attribution block. |
| Music bed | `services/bgm.py` | Where the replacement audio comes from. |
| Publish + quota ledger | `workflows/publish.py`, `quota.py` | Unchanged. ~6 uploads/day still the ceiling. |
| Untrusted-text fencing | `untrusted.py` | **Directly needed.** TikTok captions, comments and hashtags reach the LLM and would otherwise be able to write our prompts. |
| Semantic dedup | per `KNOWN-ISSUES` §5.6 + `test_semantic_dedup.py` | Repurposed for corpus-level repetition scoring (§2.2). |
| Clip-worthiness scoring | `shorts.py` | The detrending idea transfers; the retention input does not exist for someone else's video, so the signal is replaced (§6.2). |
| YPP threshold tracking | `monetisation.py` | The gate's consequences land here — a suspension resets this to zero. |
| Approval gate before publish | `POST /v1/jobs/{id}/publish` | Where the Originality Report blocks. |

**What is genuinely new:** source discovery, the rights ledger, media acquisition,
segment selection, the assemble stage's reframe/audio-replace path, the Originality
Gate, and one screen.

Two repo conventions this must respect, both learned the hard way and written down:
`ObjectStore` rather than bare filesystem paths, and migrations run **from
`apps/engine`** (CLAUDE.md is emphatic about the second, and about why).

---

## 4. The tab

**Name:** Repurpose. **Route:** `/repurpose`. Rail item after Library — it produces
library entries, and it sits with the content screens rather than the config ones.

One screen, one primary action, per `docs/UI-DESIGN.md`.

```
┌────┬──────────────────────────────────────────────────────────┐
│    │  Repurpose            [channel ▾]        [Build episode] │
│ ▪  ├──────────────────────────────────────────────────────────┤
│ ▪  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│ ▪  │  │ ▓▓▓▓▓▓▓▓ │ │ ▓▓▓▓▓▓▓▓ │ │ ▓▓▓▓▓▓▓▓ │ │ ▓▓▓▓▓▓▓▓ │     │
│ ▪  │  │ fit 87   │ │ fit 81   │ │ fit 74   │ │ fit 71   │     │
│ ▪  │  │ ✓ owned  │ │ ⏳ asked │ │ ○ no rts │ │ ✓ licnsd │     │
│ ▪  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│ ▪  │                                                          │
│ ⚙  │  ── selected (3) ─────────────────── 1:47 ── build ──    │
└────┴──────────────────────────────────────────────────────────┘
```

- **Cards** carry: still, creator handle, duration, fit score, and one rights chip.
  The rights chip is the only coloured element — it is the thing that decides
  whether the card is usable at all.
- **Slide-over on click** (never a new page): preview, *why this fits* in plain
  sentences, the rights panel, and "Add to episode".
- **The rights panel** is the screen's real content: pick a basis, record evidence,
  or send a permission request and watch its state. A clip with no basis can be
  looked at and cannot be built with — the "Add to episode" button is disabled and
  says why, per the repo's own rule that a button doing nothing is worse than no
  button (`queue/page.tsx`).
- **Selection strip** at the bottom shows running duration. "Build episode" hands
  off to the existing Create pipeline view — the same stage rows, streaming over the
  same SSE. No second pipeline UI.

The Originality Report appears twice: as a stage row in that pipeline, and as a
blocking card on the publish gate.

Demo-data behaviour follows §5.5 of `KNOWN-ISSUES.md`: live with a `demo.ts`
fallback and the "demo data" badge, and **that table gets a new row in the same
commit** — it is the file that goes stale first.

---

## 5. Architecture

### 5.1 Four acquisition lanes

Every clip enters through exactly one. The lane determines what may be fetched, what
may be kept, and what the gate demands.

| Lane | Source | Rights basis | Notes |
|---|---|---|---|
| **A — Own** | Your own TikTok account, via Display API (`video.list`) | `own` | Zero rights risk, highest value. Best masters come from your own originals, not the watermarked TikTok export. |
| **B — Campaign** | A creator running an official clip programme / paid campaign | `campaign` | **Best economics and least friction** — see [RESEARCH §1](REPURPOSE-RESEARCH.md#1-the-finding-that-matters-most-successful-clipping-doesnt-run-on-adsense). Enrolment replaces negotiation, content rules come with it, and payment is per verified view. Always check for a programme before treating a creator as Lane C. |
| **C — Permissioned** | A creator you asked and who agreed | `licensed` | Request → grant → recorded evidence (scope, term, medium). The UGC-licensing pattern from §2.4. |
| **D — Quotation** | A short excerpt inside a video that is substantially yours | `commentary` | The excerpt is a *quote*, not the product. |
| **E — Open licence** | CC-BY, stock, brand-supplied | `open_licence` | Attribution string is required and auto-composed. |

**The lane governs copyright only.** It has no bearing on the transformation
thresholds in §6.1 — see §6.0. A licence does not make a lazy edit monetisable.

There is no Lane E. A discovered video with no basis stays metadata-only forever:
we store the URL and the public stats, we never fetch the media.

### 5.2 Discovery is separate from acquisition

Discovery answers *what should this channel make*, and needs no video files:

- **Trend signal** from TikTok Creative Center's public trending hashtags, sounds
  and keywords by region — official TikTok data, browsable without an ad account.
  ([Creative Center](https://ads.tiktok.com/business/creativecenter/pc/en))
- **YouTube-side demand** via the existing `research/keywords.py` and the connected
  Semrush MCP — because the video ships on YouTube, and TikTok virality does not
  imply YouTube search demand.
- **Channel fit** scored against the channel's style profile (`api/style.py`,
  `channel.py`): topical adjacency, format fit, tone match, and *negative* scoring
  for saturation against what the channel already published.

Output is a ranked candidate list. Turning a candidate into usable media is Lane
A–D's problem, and requires a human decision.

### 5.3 Data model

Four new tables. JSON where the shape churns, columns where we query — the same
reasoning `tables.py` already states for quota and publish times.

```
clip_sources      external_id, platform, url, creator_handle, caption,
                  hashtags[], sound_id, stats{}, region, discovered_at,
                  fit_score, fit_reasons[], status
                  ── metadata only. No media. Safe to hold for anything public.

clip_rights       source_id → clip_sources, basis (own|licensed|commentary|
                  open_licence), grantor, evidence_kind, evidence_key,
                  scope{}, granted_at, expires_at, revoked_at
                  ── expires_at and revoked_at are why this is a table and not a
                     column. A licence that lapses must be able to fail a re-render
                     of a video that passed last month.

clip_assets       source_id, storage_key, sha256, duration_s, width, height,
                  has_watermark, watermark_regions[], audio_fingerprint,
                  acquired_at
                  ── created only after a rights row exists. Enforced in the
                     acquire stage, not merely by convention.

repurpose_projects  id, channel_key, segments[] (source_id + in/out + role),
                    thesis, job_id, created_at
```

`Job` is reused unchanged — this is one more workflow, not a parallel job system.

One migration, in `apps/engine/alembic/versions/`. Run it from `apps/engine`.

### 5.4 The workflow

Registered as `"repurpose"` in `workflows/video.py::WORKFLOWS` and added to
`STARTABLE`. Stages, with dependencies:

| # | Stage | Depends on | What it does |
|---|---|---|---|
| 1 | `fit` | — | Rank candidates against the channel profile. Metadata only. |
| 2 | `rights` | `fit` | **Hard gate.** Every selected clip has a valid, unexpired, unrevoked basis, or the run fails here — before a byte is fetched or a cent is spent. `max_attempts = 1`; a missing licence is not a transient error. |
| 3 | `acquire` | `rights` | Fetch by the lane's permitted route. Hash, probe, scan for watermarks. |
| 4 | `segment` | `acquire` | Choose in/out points worth using (§6.2). |
| 4b | `hook` | `segment` | **Separate stage, deliberately.** Choosing the moment and choosing the first 0.5 seconds are different problems, and only the second decides whether the clip is watched at all — viewers decide in 1.5–3s. A segment whose payoff lands 20s in must have it *teased at the front*, not played in order. See §6.4. |
| 5 | `angle` | `segment`, `hook` | The editorial thesis: why *these* clips together, what the take is. Reuses `script.AngleStage`'s shape. |
| 6 | `script` | `angle`, `segment` | Narration written **to the clips** — timed against segment durations, not generic voiceover. Reuses the existing chain. |
| 7 | `voice` | `script` | `media.VoiceoverStage`, unchanged. |
| 8 | `assemble` | `voice`, `segment` | Reframe to target aspect, **replace the audio bed**, duck retained diegetic audio under narration, burn credit cards. |
| 9 | `subtitles` | `assemble` | `media.SubtitlesStage`, unchanged. |
| 10 | `originality` | `assemble`, `rights` | The gate (§6). Produces the report. |
| 11 | `seo` | `script`, `originality` | Existing SEO stages + mandatory attribution block. |
| 12 | `thumbnail` | `seo` | `media.ThumbnailStage`, optional as today. |

Ordering follows the existing file's logic: cheap judgement first, the irreversible
expensive thing last, and the gate *before* SEO so a blocked video does not pay for
a title it will never use.

`rights` at position 2 is the load-bearing choice. It is the only stage in the repo
whose job is to refuse.

### 5.5 Untrusted input

TikTok captions, hashtags, comments and creator bios all reach an LLM prompt. They
are exactly the threat `untrusted.py` was written for, and arguably a sharper one
than scraped web pages: a caption is short, adversarial-by-culture, and lands in a
prompt that decides what gets published under the operator's name. Every external
string goes through `fence()` at the call site. `tests/test_untrusted_sources.py`
gets the TikTok cases.

---

## 6. The Originality Gate

The product's real differentiator, and the answer to the brief's fourth
requirement. It asks *"would a human reviewer call this transformative?"* and
answers with numbers a person can check.

### 6.0 Two gates, reported separately

**Correction to an earlier assumption in this document.** Rights clearance and
transformation are *independent* gates that do not substitute for each other.
YouTube's reused-content rules apply "regardless of whether you have permission
from the original creator" — collections of songs from different artists are
unmonetisable even *with* the artists' permission — and the converse holds too: a
video can take zero copyright claims and still fail reused-content review.
([RESEARCH §2](REPURPOSE-RESEARCH.md#2-the-correction-permission-does-not-satisfy-the-reused-content-policy))

So the report carries two verdicts, never one blended score:

```
Rights          ✓ cleared      campaign · @creator · Whop, expires 2026-11-04
Transformation  ✗ 41% original — needs commentary over segments 2 and 3
```

"Cleared to use, not yet original enough" is a common and real state with a
completely different fix from its opposite. A single score hides it.

**Transformation thresholds are lane-independent.** An earlier draft gave Lane C
looser bars because it had permission; that was wrong, and permission buys nothing
here.

### 6.1 Checks

**Hard blocks — publish is refused, no override in the UI:**

| Check | Rule |
|---|---|
| Rights basis | Present, unexpired, unrevoked for every segment |
| Watermark | Zero third-party watermarks in the sampled frame set (§2.6) |
| Audio licence | No unreplaced source music bed; any retained audio covered by the basis (§2.5) |
| Attribution | Credit present on-screen and in description for Lanes B, C, D |

**Scored — warnings that need a reason to pass:**

| Signal | Why | Starting threshold |
|---|---|---|
| Original-runtime share | The headline number a reviewer intuits | ≥ 50% original |
| Longest unbroken source run | An unbroken lift reads as a reupload whatever the totals say | ≤ 15s |
| Narration coverage over source | Commentary present *while* source plays, not bolted around it | ≥ 60% of source runtime |
| Segment count | One clip lightly topped and tailed is the classic failure | ≥ 3 for a compilation |
| Cut density | An edit with real cuts is visibly not a raw lift — and it is also what retention wants (§6.4) | interrupt every 3–5s |
| **Corpus repetition** | The §2.2 templating signal, over the channel's last 30 uploads | semantic distance above floor |
| **Narration-template reuse** | *Named* by policy: "dozens of videos all using the same narration or text" | flagged on repeat skeleton |
| Structural variety | Same beat skeleton every time is templating even with different clips | flagged on 3 consecutive matches |

The last three are **corpus-level and matter most**, because they are the failure
mode automation walks into by default: no individual video looks bad, and the
channel dies anyway. They are also the checks a human operator will never run
themselves.

Thresholds are **heuristics calibrated to the policy's language, not to a published
algorithm** — YouTube does not document a numeric bar, and any file that claims
otherwise is guessing. They are constants in one module with that caveat in the
docstring, in the same spirit as `monetisation.py` refusing to state a 12-month
figure from four weeks of data. They should be tuned from real review outcomes, and
the report records which version scored each video so that tuning is possible at all.

### 6.2 Segment selection

`shorts.py` solves a similar problem by detrending a retention curve, and its
warning applies here too: any signal that decays over a video will pick the opening
every time and produce a feature that has not watched the video. But retention data
does not exist for someone else's TikTok, so the input has to change:

- audio energy and speech-density envelope (where something is *happening*),
- scene-change density,
- caption/OCR keyword hits against the episode thesis,
- an LLM pass over the transcript for the beat that pays off.

Detrended the same way, for the same reason.

### 6.3 The report

Rendered as a stage row in the pipeline and as a blocking card at publish. Plain
sentences over a score dial, matching the Analytics screen's voice:

> **Blocked — 1 hard failure.**
> Segment 2 (@creator) still carries a TikTok watermark. Source it clean or drop it.
>
> 62% original runtime · longest source run 11s · narration over 74% of source
> · 4 segments · distinct from your last 30 uploads

Stored on the job so it is auditable later — including, specifically, if a channel
is ever reviewed and someone has to demonstrate what was checked and when.

### 6.4 Retention craft — the part that decides views

Compliance decides whether a clip *can* earn. This decides whether it earns
anything. From [RESEARCH §3](REPURPOSE-RESEARCH.md#3-the-craft--what-makes-a-clip-perform),
and every item is mechanical enough to automate:

```
0.0 – 3.0s   hook       pattern interrupt; the strongest visual moment, teased
3.0 – 5.0s   promise    what the viewer gets for staying
5.0s – end   payoff     interrupt every 3–5s; first 2–3 shots rapid-fire
```

- **Captions:** 4–7 words on screen, high contrast, heavy weight, **synced to
  speech** rather than dumped as a block, inside top/bottom safe zones. Most
  short-form viewing is muted, so this is not an accessibility nicety — it is the
  primary text channel. `media.SubtitlesStage` already produces cue-accurate
  timings; what is missing is the styling preset, which `PLAN.md` §6.4 already
  admits is bad today. This is where that gets fixed, and it serves two goals at
  once.
- **Per-platform packaging:** one clip, N native versions differing in hook text,
  caption and first frame. Each platform's audience, aspect and first-frame
  treatment genuinely differ, so this is optimisation, not duplication.

Note the convenient overlap: cut density, real captions and a constructed hook all
raise the transformation score in §6.1 *and* the retention curve. The compliance
work and the quality work are largely the same work — which is the strongest
argument that this design is aimed correctly.

### 6.5 Feed clip performance into the existing loop

Ten clips a day is ~300 data points a month: enough to learn which source
creators, topics and hook structures actually produce. `insights.py`,
`feedback.py` and `review.py` already do exactly this for videos, behind the
8-per-group / p<0.05 / ≥8% lift gate. Pointing them at clip attribution is close
to free and is what turns volume into knowledge rather than into a templating
violation.

---

## 7. Build phases

Each ends with something runnable, per PLAN.md's convention.

**R0 — Rights spine — ✅ built.** Four tables (`clip_sources`, `clip_grants`,
`clip_assets`, `repurpose_projects`) + migration `e5b93c17d24a`,
[`engine/repurpose/rights.py`](../apps/engine/engine/repurpose/rights.py),
repository layer, [`api/repurpose.py`](../apps/engine/engine/api/repurpose.py),
and the screen at [`app/repurpose`](../apps/web/app/repurpose). No media anywhere.
The invariant — no media without a live grant — is enforced in
`repository.record_asset`, not left to the acquire stage.

**R4 — The gate — ✅ built, ahead of schedule.** Pure logic, so it needed no
credentials and was worth having before anything could produce a timeline to
score. [`engine/repurpose/gate.py`](../apps/engine/engine/repurpose/gate.py),
both verdicts, all hard blocks, the corpus checks, versioned thresholds.
`POST /v1/repurpose/evaluate` scores a proposed edit before it is built.

**R1 — Discovery & fit — ✅ built.**
[`repurpose/fit.py`](../apps/engine/engine/repurpose/fit.py) scores a clip against
one channel — adjacency, YouTube-side demand, reach, usability, with saturation as
a penalty. [`providers/tiktok.py`](../apps/engine/engine/providers/tiktok.py) is
Lane A only and a test asserts it exposes no search or download surface.
`POST /v1/repurpose/discover` sweeps and stores. Trend pull is behind
`STUDIO_TIKTOK_TRENDS_URL` and scores zero when unset rather than guessing.

**R2 — Lane A acquisition — ✅ built.**
[`repurpose/acquire.py`](../apps/engine/engine/repurpose/acquire.py): grant check
before the network *and* before the row, streamed download with a size ceiling,
hash, probe, and a real watermark detector tested against synthetic frame stacks.
**Unverified against the live TikTok API** — it needs credentials, same status as
the YouTube publish path in `PLAN.md` §7.

**R3 — Cuts — ✅ built; compositing still ahead.**
[`repurpose/segment.py`](../apps/engine/engine/repurpose/segment.py) chooses the
segment and, separately, the hook — see §6.4 and the three-attempt note in its
tests. *Still to build:* the actual compositing — reframe, audio-bed replacement,
ducking, caption preset, credit cards. Until that lands the workflow decides
*where* to cut without producing a file, so a repurpose job cannot yet render or
publish.

**R4b — Gate wiring — ✅ built.** `OriginalityStage` runs in the workflow and
raises rather than warns; the same report is re-checked at the approval gate in
`automation.publish_blockers`, which is where the 1,600 quota units are actually
spent.

**R5 — Lane B — ✅ built for the rights half.** Campaign enrolment is a first-class
lane with its own evidence requirements, live in the rights panel and enforced end
to end. *Still to build:* pulling campaign listings automatically, and the
per-view payout reconciliation that makes the economics visible.

**R6 — Polish — partly done.** `KNOWN-ISSUES` §5.5 row, contracts regenerated,
engine tests in `apps/engine/tests/`, web tests beside their components.
*Still to build:* multi-clip episodes with a thesis, per-platform packaging, and
clip attribution into the existing feedback loop (§6.5).

### What is left, in order

1. **Compositing** (the rest of R3) — the one thing standing between the workflow
   and an actual file. Everything else is built around a gap where the render goes.
2. **A timeline editor on the screen**, so "Build episode" has something to send.
3. **TikTok app review** for Display API credentials, which turns R1/R2 from
   reviewed code into proven code.
4. Multi-clip episodes, per-platform packaging, feedback-loop attribution.

---

## 8. Cost, quota and metering

- Every provider call goes through the existing metering wrapper (non-negotiable
  #5). New line items: video probe/transcode CPU, watermark scan, transcription of
  source clips, the fit-scoring LLM pass.
- Estimated per episode: **$0.40–$2.00** — cheaper than a from-scratch long-form,
  because there is no stock-footage sourcing and no generative B-roll. Cost lands in
  the existing per-video ledger and the spend card.
- **Quota is unchanged and remains the hard ceiling: ~6 uploads/day.** Repurposing
  is faster to produce than original long-form, which makes it the feature most
  likely to run the channel into both the quota wall *and* the templating signal in
  §2.2. The tab should show the day's remaining uploads next to "Build episode".
  Volume is the thing this feature makes easy and the thing that gets channels
  suspended.

---

## 9. Risks

Ranked by what they actually cost — the severities differ by orders of magnitude
and an earlier draft of this section treated them as comparable
([RESEARCH §5](REPURPOSE-RESEARCH.md#5-risk-model-corrected)):

| Outcome | Trigger | Cost | Recoverable? |
|---|---|---|---|
| **Channel termination** | 3 copyright strikes / 90 days | Everything | No |
| **Channel demonetisation** | Reused-content review | All revenue, 30-day lockout, `monetisation.py` to zero | Yes, after fixing |
| **Content ID claim** | Fingerprint match, usually music | That video's ad revenue | Yes — non-punitive, channel untouched |
| **Takedown / blocked** | Clipping around an official programme | One video + a burned relationship | Usually |

**A Content ID claim is not a strike.** It is automated and non-punitive: the
rights holder takes the revenue, the channel is unaffected. Worth stating plainly
because the original brief aimed at avoiding claims — the *mildest* row — by
methods that increase exposure to the two catastrophic ones. Under Lane B's
per-view economics a claim costs nothing at all. Defensive budget belongs on
strikes and reused-content review; claims are a cost line.

1. **Channel-wide enforcement.** A reused-content suspension hits the whole
   channel, 30 days minimum. This is why the gate blocks rather than warns.
2. **Audio.** §2.5 is the likeliest single cause of a claim, and the mitigation
   (total bed replacement) has to be automatic, because an option here will
   eventually be left off.
3. **Lane C drift.** "Commentary" is the lane that silently becomes reuploading as
   the clips get longer and the narration thinner. Hence the strictest thresholds
   and the longest-unbroken-run check.
4. **TikTok API access.** Display API needs app review; Research API is effectively
   closed to us. Lane A depends on approval landing. Start it early, as PLAN.md
   advises for Google credentials.
5. **Watermark detection false negatives.** A missed watermark passes a hard block
   it should have failed. Sample frames densely, and prefer clean masters over
   detection wherever the lane allows.
6. **Licence expiry after publish.** A grant with a term can lapse while the video
   is live. `clip_rights.expires_at` exists for this; a scheduled check that flags
   published videos whose basis has lapsed is R5 work, not optional polish.

---

## 10. Decisions needed before R0

0. **Business model — the one that changes everything else.** Are these clips meant
   to earn through *YouTube AdSense* (must clear both gates in §6.0, slow, fragile)
   or through *paid per-view campaigns* (Lane B — the rights holder funds it and
   wants the clips out there, $0.20–$6 per 1k views, and a Content ID claim costs
   nothing)? The research says the money is overwhelmingly in the second, and
   almost nothing in this plan changes except which lane gets built first. See
   [RESEARCH §1](REPURPOSE-RESEARCH.md#1-the-finding-that-matters-most-successful-clipping-doesnt-run-on-adsense).

1. **Lane priority.** Lane A (your own TikToks) is the fastest, safest, and highest
   quality — but only if you have an account with content worth repurposing. If
   this is meant to run on *other people's* clips from day one, Lane B campaign
   enrolment moves ahead of R2. Which is it?
2. **Channel scope.** One channel to start, or must the fit scoring handle several
   with different niches from the beginning?
3. **Output format.** TikTok-in → Shorts-out (fast, and the quota-cheapest path), or
   TikTok-in → long-form compilation (better watch-hours economics under §2.1, more
   assemble work)?
4. **Thresholds.** The §6.1 numbers are my starting proposal. Tune now or after the
   first ten videos?

**My recommendation, revised after the research:** build **Lane A first for the
craft, Lane B first for the money.**

Lane A (your own TikToks) remains the right way to build and prove R3/R4 — no
clearance path, clean masters, and you can iterate on the edit quality without any
compliance risk at all. But the earning path is Lane B: campaign clipping pays per
verified view, the rights question is answered by enrolment rather than
negotiation, and it does not require clearing the reused-content gate to make
money. If the goal is revenue rather than a channel asset, Lane B is where this
should point, and §6's gate becomes quality control rather than a gate on income.

Long-form compilation over Shorts either way — it puts the watch-hours route in
`monetisation.py` within reach, it is the variant where the transformation is most
obviously real, and the 6-uploads/day quota ceiling makes high-volume Shorts a
poor fit for YouTube specifically.
