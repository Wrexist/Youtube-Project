# Audit 3 — rolling ledger

A four-round audit of the whole application. Each round fans finders out across
disjoint dimensions, verifies every finding adversarially before it is allowed to
count, plans, fixes, and gates. Rounds append; nothing is silently dropped.

Read this before auditing. Anything listed under **Accepted** below is a decision
already made and argued — re-reporting it wastes a round.

---

## Accepted — known, deliberate, not defects

These have been investigated and consciously left as they are. Each has its
reasoning recorded in `KNOWN-ISSUES.md`. Do not report them as findings.

| What | Why it stands | Recorded |
|---|---|---|
| `ChaptersStage` output is read by nothing | YouTube renders chapters only from description timestamps; plumbing it is a graph reorder, not a fix | KNOWN-ISSUES §5.8 |
| Channel launches are not persisted | `save_launch`/`load_launches` exist but are unwired; wiring needs a loader rewrite, and the loss is a regenerable LLM artifact | KNOWN-ISSUES §5.8 |
| Analytics calls are not metered into the quota ledger | Separate, far larger quota pool; sharing the counter would refuse uploads there is budget for | KNOWN-ISSUES §3.2b |
| npm advisories remain open | Need an upstream Next release; not fixable from this repository | AUDIT.md §4.7 |
| Neither Google API has run against a live account | Needs OAuth credentials from a real Cloud project | KNOWN-ISSUES §1.1 |
| No LLM/image provider endpoint has been called for real | All transports are mock-tested only | KNOWN-ISSUES §1.3 |
| The engine is unauthenticated | Single-user local tool; do not expose it | KNOWN-ISSUES §6 |
| Windows scripts have never been executed | No PowerShell in the development environment | KNOWN-ISSUES §6 |
| Duplicate detection is lexical, not semantic | Explainability on the idea card was chosen over recall | KNOWN-ISSUES §5.6 |
| Chinese comments remain in unedited vendored-derived files | CLAUDE.md forbids blanket translation passes — translate only in files being edited | CLAUDE.md |

---

## Rounds

Three rounds ran. A fourth was planned and cancelled.

| Round | Raw | Verified | Critical | Tests after | Outcome |
|---|---|---|---|---|---|
| 1 — broad sweep | 61 | 14 | 4 | 615 | fixed, gate green |
| 2 — depth | 61 | 14 | 6 | 649 | fixed, gate green |
| 3 — adversarial | 56 | 14 | 2 | 672 | fixed via `remediate.mjs`, gate green |

Each finding was found by one of twelve dimension auditors, merged and ranked by a
triage pass against this file's Accepted table, then given to two verifiers with
different lenses — *does the code do what is claimed*, and *granting that, does the
consequence follow* — both instructed to refute and defaulting to refuted. Refuted by
either lens killed it. Only survivors reached a fixer.

### The pattern worth keeping

**The most valuable findings in rounds 2 and 3 were defects in the previous round's
fixes.** Two examples, both load-bearing:

* **Quota atomicity was "fixed" twice before it held.** Round 1 made `reserve()`
  atomic under an `asyncio.Lock`. Round 2 found that lock is per-process and called
  its fix cross-process. Round 3 disproved *that* with two real OS processes: 8,400
  of 10,000 pre-spent, both admitted, **11,600 units booked against a 10,000
  ceiling** — and four processes against a 1,600 limit all admitted. The module
  docstring asserted the race was fixed while it was not. It is now one transaction
  holding a day-scoped write lock (`BEGIN IMMEDIATE` on SQLite, `FOR UPDATE` on
  Postgres), and `tests/test_quota_multiprocess.py` fails if the lock is removed
  (verified by mutation).
* **The publish gate was closed twice and found open twice.** Round 1 refused
  `/rerun` and `/edit` on a finished publish. Round 2 found `interrupted` slipped
  through. Round 3 found `failed` did too — a publish whose upload landed but which
  died on a later stage returned 202 with no `force`, uploading a second public
  video and spending another 1,600 units.

The lesson for anyone auditing this next: **a green gate immediately after a round of
fixes is not evidence the underlying problem is solved.** It is evidence the tests
that exist still pass. Attack the fix.

### What the verifiers killed

Adversarial verification is only worth its cost if it removes things. Across the
three rounds it demoted, among others: that secrets bake into the Docker images (the
web image's final stage copies four explicit paths); that the test suite could bill a
real LLM call under default env; and that oversized YouTube metadata would waste
quota (the insert 400s before any is charged). Those are recorded here rather than
"fixed", because changing working code on a wrong premise is the expensive mistake.

### Still open — real, verified, and deliberately not fixed

Triage capped each round at 14 findings. These survived verification but lost the
ranking, and are recorded so they are not re-discovered from scratch:

| What | Why it was cut |
|---|---|
| `storage/tmp` keeps a full duplicate of every render forever (`put_file` copies, nothing sweeps) | Disk growth only — no money, secret, or publication consequence. Reported by four auditors across three rounds; the most persistent thing on this list. |
| Light mode never redefines `ok`/`warn`/`bad`; status text renders at 1.6–2.8:1 contrast, and `--color-faint` fails 4.5:1 in both themes | A WCAG failure, but readability in one theme with a usable dark-mode path |
| Calendar is inoperable without a mouse — seven draggable elements, zero focusable | Auto-schedule gives a partial keyboard path |
| Create pipeline has no `aria-live` region, so stage completion is never announced | Lowest-consequence of the accessibility cluster |
| `POST /v1/calendar/auto` is unbounded and runs an O(n²)–O(n³) planner on the event loop | Needs an adversarial payload the shipped UI cannot produce, against an API the Accepted table designates unauthenticated-local-only |
| The cost chip stays `$0.00` for a whole run despite per-stage costs on the wire | Display-only, though it touches non-negotiable #5 |
| `scripts/setup.sh` writes `.env` world-readable (0644) while the code path enforces 0600 | Only bites shared multi-user hosts |
| Tailwind/lightningcss platform pins omit `linux-arm64` | Build-time only; the web image cannot build on Apple Silicon or Graviton |
| Competitor mining can never run — `youtube_client` is never attached to startable jobs — while `TitlesStage` still asks the model for the competitor gap | Weakens SEO grounding; no data loss or wrong publish |

The accessibility cluster is four separate findings that were each individually cut
on consequence. Together they are the strongest argument for a dedicated pass, and
that is the recommended next piece of work.

### One caveat on round 3

Round 3's fixes went in **without a subsequent round attacking them**, because the
fourth round was cancelled. Every behavioural fix carries a regression test and the
quota one was mutation-checked, but given that this exact code has been wrong twice
before, treat the cross-process quota reserve as the least-proven change in the set.

### A process failure worth recording

Round 3's audit half worked and its planner then returned a single task titled `"t"`
with the summary `"test"`. The fixer had nothing to do, the tree never changed, and
the gate went green over an untouched repository — indistinguishable from a clean
round unless you check the diffstat. The findings were recovered from the run journal
and remediated separately. `audit-round.mjs` now requires at least one task per
finding and logs `PLAN INCOMPLETE`; `remediate.mjs` exists so a round whose second
half fails can be finished without repaying for the finder and verifier fan-out.
