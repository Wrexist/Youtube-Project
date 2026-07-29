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

_Each round's verified findings and their outcomes are appended below by the audit
workflow._
