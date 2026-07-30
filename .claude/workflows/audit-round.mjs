export const meta = {
  name: "audit-round",
  description:
    "One full-app audit round: dimension finders, adversarial verify, phased plan, directory-disjoint fixes, gate",
  whenToUse:
    "Deep audit of the Studio repo. Pass {round: N, focus: '...'} to run round N. Intended to be run four times.",
  phases: [
    { title: "Find", detail: "auditors across disjoint dimensions", model: "opus" },
    { title: "Triage", detail: "dedup against the ledger, rank, bucket", model: "fable" },
    { title: "Verify", detail: "two adversarial skeptics per finding", model: "opus" },
    { title: "Plan", detail: "ordered, phased remediation list", model: "fable" },
    { title: "Fix", detail: "one fixer per disjoint directory tree", model: "opus" },
    { title: "Gate", detail: "pytest, ruff, typecheck, build, openapi", model: "opus" },
  ],
};

// `args` is meant to arrive as an object, but it can reach the script as the
// JSON *string* of that object depending on how the invocation serialises it.
// That failure is silent and expensive: `args.round` and `args.focus` are both
// undefined on a string, so round 2 labelled itself round 1 and ran with an
// empty focus — the whole per-round steer was dropped and nothing said so.
// Parse defensively, and shout if the steer went missing.
const INPUT = (() => {
  if (typeof args === "string") {
    try {
      return JSON.parse(args);
    } catch {
      return {};
    }
  }
  return args || {};
})();

const ROUND = INPUT.round || 1;
const EXTRA = INPUT.focus || "";

const REPO = "/home/user/Youtube-Project";

// Shared preamble. Every agent gets this: without it they rediscover the project's
// own conventions as "findings", and re-report things AUDIT-3.md already settles.
const CONTEXT = `
You are auditing the Studio repository at ${REPO} — a YouTube workflow automation app.
Layout: apps/web (Next.js 16 App Router), apps/engine (FastAPI + arq + MoviePy),
packages/contracts (TS types generated from the engine's OpenAPI), scripts/ (setup and
launcher), vendor/moneyprinterturbo (READ-ONLY reference, never imported, never edited,
excluded from lint and tests — do NOT audit it).

MUST READ FIRST, in this order:
  1. ${REPO}/AUDIT-3.md   — the "Accepted" table lists decisions already argued.
     Reporting anything in it is a wasted finding. Do not.
  2. ${REPO}/CLAUDE.md    — the five non-negotiables and the house conventions.
  3. ${REPO}/KNOWN-ISSUES.md — what is already documented as broken or unverified.

Two earlier audits exist (AUDIT.md, AUDIT-2.md) and their findings are fixed. This is
round ${ROUND} of 4. Your job is what those missed, not what they found.

The engine venv is at apps/engine/.venv/bin/python. Run things — do not reason about
behaviour you could observe. NEVER read ${REPO}/.env or anything under storage/.
`;

const RULES = `
What counts as a finding:
  * It is wrong, missing, or misleading in code that ships.
  * You can state a concrete failure: specific input or state, and the specific wrong
    output, crash, leak, cost, or false claim that results.
  * It is not in AUDIT-3.md's Accepted table.

What does NOT count — do not report these:
  * Style, naming, formatting, comment density. Ruff and Prettier own those.
  * "Could add a test for X" with no defect behind it.
  * Speculative hardening with no reachable failure path.
  * Anything in vendor/.
  * Restating a documented limitation as though it were undiscovered.

Prefer three findings you have proven to ten you suspect. Reproduce where you can:
run the code, run the test, curl the endpoint. Say in the evidence field exactly what
you ran and what it printed.
`;

const FINDING_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["findings"],
  properties: {
    findings: {
      type: "array",
      maxItems: 6,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["title", "file", "severity", "failure", "evidence", "fix", "bucket"],
        properties: {
          title: { type: "string", maxLength: 140 },
          file: { type: "string", description: "repo-relative path, with :line if known" },
          severity: { type: "string", enum: ["critical", "high", "medium", "low"] },
          failure: {
            type: "string",
            description: "Concrete inputs/state -> the wrong outcome. No hedging.",
          },
          evidence: {
            type: "string",
            description: "What you actually ran or read, and what it showed.",
          },
          fix: { type: "string", description: "The specific change, not a direction." },
          bucket: {
            type: "string",
            enum: ["engine", "engine-tests", "web", "ops"],
            description:
              "engine=apps/engine/engine/**, engine-tests=apps/engine/tests/**, web=apps/web/** and packages/**, ops=scripts/** .github/** infra/** and *.md",
          },
        },
      },
    },
  },
};

const DIMENSIONS = [
  {
    key: "security",
    prompt: `Audit SECURITY and SECRET HANDLING.

CLAUDE.md #4: secrets live in .env, never in config.toml, never committed, never
logged; refresh tokens encrypted at rest. CLAUDE.md: never cache API responses
carrying user OAuth state.

Look hard at: engine/api/setup.py (it writes .env and os.environ — check the atomic
write, permissions, and whether a crafted key name or value can escape its line or
inject a second variable); engine/providers/youtube.py token encryption and key
derivation; whether a provider base_url from settings can be pointed at an internal
address (SSRF) by engine/providers/*; path handling anywhere a user-supplied string
reaches a filesystem path or an ffmpeg/MoviePy argument (services/stock.py,
services/bgm.py, render/compose.py, fonts); Next.js Server Actions that accept
untrusted input; whether any error response or log line can carry a key.

Try to actually break something. A crafted value that lands in .env verbatim, a
filename that escapes storage/, a log line that echoes a token.`,
  },
  {
    key: "render-core",
    prompt: `Audit the RENDER CORE for correctness: engine/render/compose.py and
engine/services/{video,voice,material,subtitle,effects,bgm,stock}.py.

Consult vendor/moneyprinterturbo/app/services/ when upstream behaviour is unclear —
read only, never edit, never import.

Look for: timeline arithmetic that can produce a video shorter or longer than the
narration; subtitle cues that overlap, run off-frame, or are dropped; resources
(VideoFileClip, AudioFileClip, temp files) not closed on the error path; the thread
executor boundary — blocking calls on the event loop, or shared mutable state across
threads; division by zero or index errors on a one-clip or zero-clip video; aspect
ratio and orientation handling; what happens when a stock clip is shorter than the
beat it must fill.

Where you can, prove it with a real render using synthetic clips rather than by
reading. Small and fast — a 2-3 second render is enough to measure.`,
  },
  {
    key: "engine-api",
    prompt: `Audit the ENGINE HTTP SURFACE: engine/main.py and engine/api/*.py.

Look for: request fields with no bounds that reach an expensive operation; missing or
wrong status codes; exception paths that return a 500 where a 4xx states the cause;
endpoints that mutate before validating everything (a partial apply); race conditions
between concurrent requests on shared module-level state (JOBS, CHANNELS, SCHEDULE,
the quota ledger); SSE streams that leak subscribers or never terminate; anything
reachable that can wedge the event loop.

Start the app for real and exercise it: apps/engine/.venv/bin/python -m uvicorn
engine.main:app --port 8123, then curl it. Use a port in the 8100-8199 range and stop
what you start. STUDIO_PERSIST=false and a temp STUDIO_DATABASE_URL so you touch no
real data.`,
  },
  {
    key: "persistence",
    prompt: `Audit PERSISTENCE and DATA INTEGRITY: engine/db.py, engine/repository.py,
engine/tables.py, engine/quota.py, engine/migrations/.

Look for: writes that are not atomic where they must be (the quota ledger above all —
losing or double-counting spend causes a real quota overrun); transaction boundaries
that let a partial write survive; the in-process mirror drifting from the rows;
restore paths that crash or silently drop a row on unexpected data; migrations that do
not match tables.py; SQLite-versus-Postgres behavioural differences the code assumes
away; concurrent access from the API process and an arq worker at once.

Run it: create a temp SQLite database, drive the repository functions, kill and
restore. Never touch storage/studio.db or any real database.`,
  },
  {
    key: "jobs-sse",
    prompt: `Audit the JOB LIFECYCLE: engine/worker.py, the arq path, job cancellation,
resume, and the SSE event stream in engine/main.py.

Look for: a cancelled job that keeps running or keeps spending; a job that cannot be
resumed after a worker crash mid-stage; events duplicated, dropped, or delivered out
of order; a subscriber that never gets a terminal event and so hangs the browser
forever; the in-process fallback path behaving differently from the worker path in a
way callers can observe; what happens when Redis disappears mid-job.

The known-unfixed arq-side cancel abort is in AUDIT-3.md Accepted — do not re-report
it, but DO report anything adjacent that is not covered by it.`,
  },
  {
    key: "web-correctness",
    prompt: `Audit the WEB APP for correctness: apps/web/**.

Next.js 16 App Router. Server Components by default, "use client" only where
interaction demands it, mutations via Server Actions, types from packages/contracts.

Look for: a Server Action that can fail silently or leaves the UI showing stale state
(missing revalidatePath); a fetch with no error handling where failure is visible to
the user as wrong data rather than as an error; hand-written types mirroring an API
response instead of importing from packages/contracts; a "use client" boundary that
pulls a large tree client-side unnecessarily; hydration mismatches; an unhandled
rejection in a Server Component; the SSE/TanStack Query live views leaking listeners
on unmount.

Build and run it. npm run build works. To exercise pages, use a port in 3100-3199.`,
  },
  {
    key: "web-design",
    prompt: `Audit the WEB UI against docs/UI-DESIGN.md and CLAUDE.md's first
non-negotiable: the UI stays quiet, one primary action per screen, no cockpit
dashboard, no screen that needs a legend.

Read docs/UI-DESIGN.md first — it is the spec, and it wins over your taste.

For every screen in apps/web/app/, check: is there exactly one primary action, and is
it obvious? Does anything need explaining that should be self-evident? Are the
"demo data" badges honest and consistently placed? Accessibility: keyboard reachability
of every interactive element, focus visibility, labels on inputs, colour contrast in
BOTH light and dark, aria on the custom Radix-derived components, and whether the
drag-and-drop calendar is operable without a mouse at all.

Report concrete violations with the screen and element, not general impressions.`,
  },
  {
    key: "onboarding",
    prompt: `Audit the FIRST-RUN PATH as a new user with nothing installed.

Files: scripts/setup.sh, scripts/setup.ps1, scripts/start.mjs, scripts/doctor.py,
scripts/install-shortcut.mjs, scripts/reinstall.mjs, scripts/check-web-toolchain.mjs,
Studio.cmd, "Install Studio.cmd", README.md, SETUP.md, and the /welcome and /setup
screens.

Look for: a failure whose message does not say what to do next; a step that silently
succeeds while doing nothing; a path that requires a terminal in a flow documented as
double-click-only; anything that breaks if the repo lives in a directory containing a
space or a non-ASCII character; the Windows branches (never executed anywhere — read
them with real suspicion, and check quoting, path separators and PATH refresh); a
re-run that is not idempotent.

Test what you can actually run. Do NOT run setup.sh against the real tree in a way
that mutates .env or node_modules — copy what you need to a temp directory, or
exercise individual shell functions in isolation.`,
  },
  {
    key: "tests",
    prompt: `Audit the TEST SUITE itself: apps/engine/tests/** and any vitest tests
under apps/web.

Look for: assertions true on every branch (the classic: asserting a substring that
appears in both the success and failure return); a test whose only assertion sits
inside an if; over-mocking that means the test proves nothing about real code;
critical paths with no coverage at all — publish gating, quota ceiling enforcement,
approval gates, cost metering, provenance recording; tests that share state and pass
only in a particular order; anything that can hang or is timing-dependent.

The strongest evidence is a MUTATION: change the implementation so the behaviour is
wrong, show the test still passes, then restore it. Do that for anything you claim is
vacuous, and put the before/after in evidence. ALWAYS restore the file — verify with
git diff that you left the tree clean.`,
  },
  {
    key: "non-negotiables",
    prompt: `Audit compliance with CLAUDE.md's five non-negotiables, mechanically.

  2. EVERY generated artifact records the prompt + model that produced it. Walk every
     Stage subclass in engine/workflows/*.py and every provider call: is Provenance
     populated with the real prompt and model, every time, including on the retry and
     fallback paths? A stage that records a truncated or templated prompt instead of
     the one actually sent is a violation.
  3. NOTHING publishes without an explicit approval gate unless full-auto is on for
     that specific series. Trace every path that can reach a YouTube upload and prove
     the gate cannot be bypassed.
  5. Cost is tracked per video. Every provider call goes through the metering wrapper.
     Find calls that do not.

Also: no config.app.get( anywhere; no bare filesystem path in a service (must go
through the ObjectStore interface); VideoParams must not leak into an API response or
the web app.

These are the rules the project says it will not break. Report every actual breach,
with the call path.`,
  },
  {
    key: "youtube-limits",
    prompt: `Audit compliance with YouTube's HARD EXTERNAL LIMITS, which CLAUDE.md says
every design is downstream of.

  * Data API quota 10,000 units/day; videos.insert ~1,600 => ~6 uploads/day. Is the
    ledger's unit accounting per operation actually correct against Google's published
    costs? Is remaining budget surfaced in the UI? Can any code path issue an upload
    without first charging the ledger?
  * Title <= 100 chars. Description <= 5000. Tags <= 500 chars TOTAL. Thumbnail <= 2MB
    and 1280x720. Find every place a generated value reaches the API and check it is
    clamped BEFORE the call, not hoped about. Include the multi-byte case: 100
    characters is not 100 bytes.
  * The inauthentic-content policy: are sources actually carried through to the
    description, or is the grounding discarded after scoring?

Read engine/quota.py, engine/providers/youtube.py, engine/workflows/seo.py and
publish.py. Check the clamps with real over-long inputs.`,
  },
  {
    key: "deps-perf",
    prompt: `Audit DEPENDENCIES and RESOURCE USE.

Dependencies: apps/engine/pyproject.toml, package.json files, package-lock.json. Look
for a declared dependency that is never imported, an imported package that is never
declared (the failure mode that made CI red once with scipy), a version pin that
contradicts what is installed, lockfile drift, and platform-specific optional
dependencies that break a clean install on another OS. Check LICENCES for anything
redistributed — CLAUDE.md is explicit that no licensed music ships.

Resource use: memory growth across a render (MoviePy holds frames), connection pool
sizing against max_concurrent_renders, unbounded caches or lru_cache on anything
keyed by user input, N+1 queries in the repository restore paths, and any unbounded
list that grows for the life of the process.

Measure rather than estimate where you can — run a render and watch RSS.`,
  },
];

// ─────────────────────────────────────────────────────────────────────────────

phase("Find");
log(`Round ${ROUND}: ${DIMENSIONS.length} auditors across disjoint dimensions`);
log(EXTRA ? `focus: ${EXTRA.slice(0, 160)}…` : "focus: NONE — the per-round steer did not arrive");

const reports = await parallel(
  DIMENSIONS.map((d) => () =>
    agent(`${CONTEXT}\n${RULES}\n${EXTRA ? `Extra focus for this round: ${EXTRA}\n` : ""}\n${d.prompt}`, {
      label: `find:${d.key}`,
      phase: "Find",
      model: "opus",
      schema: FINDING_SCHEMA,
    }),
  ),
);

const raw = reports.filter(Boolean).flatMap((r) => r.findings || []);
log(`${raw.length} raw findings from ${reports.filter(Boolean).length}/${DIMENSIONS.length} auditors`);

if (raw.length === 0) {
  log("Nothing found this round.");
  return { round: ROUND, raw: 0, verified: [], note: "no findings" };
}

// Barrier is correct here: triage compares every finding against every other and
// against the ledger, which needs the whole set at once.
phase("Triage");

const TRIAGE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["keep", "dropped"],
  properties: {
    keep: {
      type: "array",
      maxItems: 14,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["title", "file", "severity", "failure", "evidence", "fix", "bucket"],
        properties: {
          title: { type: "string" },
          file: { type: "string" },
          severity: { type: "string", enum: ["critical", "high", "medium", "low"] },
          failure: { type: "string" },
          evidence: { type: "string" },
          fix: { type: "string" },
          bucket: { type: "string", enum: ["engine", "engine-tests", "web", "ops"] },
        },
      },
    },
    dropped: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["title", "reason"],
        properties: { title: { type: "string" }, reason: { type: "string" } },
      },
    },
  },
};

const triage = await agent(
  `${CONTEXT}

You are triaging ${raw.length} findings from ${DIMENSIONS.length} independent auditors of
this repository. Here they are as JSON:

${JSON.stringify(raw, null, 1)}

Do four things:

  1. DROP anything in AUDIT-3.md's Accepted table, anything already documented in
     KNOWN-ISSUES.md as a known limitation, and anything that is style rather than
     defect. Read those files — do not guess.
  2. MERGE duplicates. Several auditors looked at overlapping code and will have found
     the same thing from different angles. Merge into the single sharpest statement,
     keeping the strongest evidence from each.
  3. RANK what survives, most severe first. Severity means consequence: data loss,
     money spent wrongly, a secret exposed, a published video that should not have
     been, a user stuck with no way forward. Not how hard it is to fix.
  4. Keep at most 14, and assign each an accurate bucket. The bucket decides which
     fixer touches it, and the fixers run in parallel over disjoint trees — a wrong
     bucket means a file gets edited by nobody or by two agents at once.

Every kept finding must carry a concrete failure and real evidence. If a finding's
evidence is only "this looks wrong", drop it and say so in dropped[].`,
  { label: "triage", phase: "Triage", model: "fable", schema: TRIAGE_SCHEMA },
);

const candidates = (triage && triage.keep) || [];
log(`triaged: ${candidates.length} kept, ${((triage && triage.dropped) || []).length} dropped`);

if (candidates.length === 0) {
  return { round: ROUND, raw: raw.length, verified: [], dropped: triage.dropped };
}

// Two skeptics per candidate, each with a different lens. Redundant refuters catch
// less than diverse ones: a finding can be wrong because the code does not do what
// was claimed, or because it does but the consequence does not follow.
phase("Verify");

const VERDICT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["refuted", "why"],
  properties: {
    refuted: { type: "boolean" },
    why: { type: "string", description: "What you checked and what it showed." },
    correction: {
      type: "string",
      description: "If the finding is real but misstated, the accurate statement.",
    },
  },
};

const LENSES = [
  {
    key: "code",
    ask: `Does the code actually do what this finding claims? Read the real file at the
real line. Check the surrounding guards, the caller, and any validation upstream that
would prevent the described input from ever arriving. A finding that describes a
reachable-looking bug behind an unreachable branch is refuted.`,
  },
  {
    key: "consequence",
    ask: `Grant the mechanism — the code does what is claimed. Does the stated failure
actually follow? Run it if you can: reproduce the concrete input and show the wrong
output. If the consequence is milder than claimed, or is already handled downstream,
or the described state cannot occur in this application, it is refuted.`,
  },
];

const verdicts = await parallel(
  candidates.map((finding, index) => () =>
    parallel(
      LENSES.map((lens) => () =>
        agent(
          `${CONTEXT}

Your job is to REFUTE this finding, not to confirm it. Default to refuted=true unless
you can show it is real. A false finding that survives costs more than a true one
missed, because it will be "fixed" — changing working code on a wrong premise.

FINDING
  title:    ${finding.title}
  file:     ${finding.file}
  severity: ${finding.severity}
  failure:  ${finding.failure}
  evidence: ${finding.evidence}
  proposed: ${finding.fix}

YOUR LENS — ${lens.key}
${lens.ask}

If the finding is real but overstated or misattributed, set refuted=false and put the
accurate version in correction. Do not modify any file.`,
          {
            label: `verify:${lens.key}:${index + 1}`,
            phase: "Verify",
            model: "opus",
            schema: VERDICT_SCHEMA,
          },
        ),
      ),
    ).then((votes) => {
      const live = votes.filter(Boolean);
      const kills = live.filter((v) => v.refuted).length;
      return {
        finding,
        // Refuted by either lens is enough to kill it. Both lenses are necessary
        // conditions for the finding to be true, so failing one is failing.
        survives: live.length > 0 && kills === 0,
        votes: live,
      };
    }),
  ),
);

const survivors = verdicts.filter(Boolean).filter((v) => v.survives);
const killed = verdicts.filter(Boolean).filter((v) => !v.survives);
log(`verified: ${survivors.length} confirmed, ${killed.length} refuted`);

for (const k of killed) {
  log(`  refuted — ${k.finding.title}`);
}

if (survivors.length === 0) {
  return {
    round: ROUND,
    raw: raw.length,
    verified: [],
    refuted: killed.map((k) => ({ title: k.finding.title, why: k.votes.map((v) => v.why) })),
  };
}

// Fold each verifier's correction back in, so the fixer works from the accurate
// statement rather than the original claim.
const confirmed = survivors.map((s) => ({
  ...s.finding,
  corrections: s.votes.map((v) => v.correction).filter(Boolean),
}));

phase("Plan");

const PLAN_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["summary", "tasks"],
  properties: {
    summary: { type: "string" },
    tasks: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["step", "bucket", "title", "change", "verify", "blocks"],
        properties: {
          step: { type: "string", description: "e.g. 1.1, 1.2, 2.1" },
          bucket: { type: "string", enum: ["engine", "engine-tests", "web", "ops"] },
          title: { type: "string" },
          change: { type: "string", description: "The specific edit to make." },
          verify: { type: "string", description: "The command or observation that proves it." },
          blocks: { type: "string", description: "Steps this must land before, or 'nothing'." },
        },
      },
    },
  },
};

const plan = await agent(
  `${CONTEXT}

${confirmed.length} findings survived two independent adversarial verifiers. Turn them
into an ordered remediation plan.

${JSON.stringify(confirmed, null, 1)}

Rules for the plan:
  * Order by dependency first, then severity. If fixing A changes the code B touches,
    A comes first and B says so in blocks.
  * One task per finding unless two genuinely collapse into one edit.
  * "change" must be specific enough to execute without re-deriving the finding: name
    the file, the function, and what the code should become.
  * "verify" must be a command that can be run or an observation that can be made. Not
    "confirm it works".
  * Group tasks so that every task in one bucket touches only that bucket's tree —
    the fixers run in parallel and a cross-bucket edit will be lost or conflict.
  * Say in summary what the round found overall, in three sentences.`,
  { label: "plan", phase: "Plan", model: "fable", schema: PLAN_SCHEMA },
);

const tasks = (plan && plan.tasks) || [];
log(`plan: ${tasks.length} tasks — ${plan.summary}`);

// Directory-disjoint fixers. One agent owns each tree, so two agents can never edit
// the same file; within a tree the agent works sequentially and can see its own edits.
phase("Fix");

const TREES = {
  engine: "apps/engine/engine/**",
  "engine-tests": "apps/engine/tests/**",
  web: "apps/web/** and packages/**",
  ops: "scripts/**, .github/**, infra/**, and the *.md files at the repository root",
};

const FIX_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["applied", "skipped"],
  properties: {
    applied: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["step", "what", "verified"],
        properties: {
          step: { type: "string" },
          what: { type: "string" },
          verified: { type: "string", description: "What you ran, and its result." },
        },
      },
    },
    skipped: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["step", "why"],
        properties: { step: { type: "string" }, why: { type: "string" } },
      },
    },
  },
};

const buckets = Object.keys(TREES).filter((b) => tasks.some((t) => t.bucket === b));

const fixes = await parallel(
  buckets.map((bucket) => () => {
    const mine = tasks.filter((t) => t.bucket === bucket);
    return agent(
      `${CONTEXT}

You are the ${bucket} fixer for audit round ${ROUND}. Apply these ${mine.length} tasks:

${JSON.stringify(mine, null, 1)}

HARD BOUNDARY. You may edit ONLY files under: ${TREES[bucket]}
Three other fixers are editing the other trees at this same moment. Editing outside
your tree loses your change or theirs. If a task genuinely requires a file outside your
tree, skip it and say so in skipped[] — do not reach across.

How to work:
  * Read before you edit. The finding may be accurately described and still have a
    better fix than the one proposed; use your judgement, and say what you did.
  * Match the surrounding code: same comment style and density, same idioms. This
    codebase comments the WHY — especially why a non-obvious choice was made and what
    broke without it. Follow that.
  * Every behavioural fix needs a test that fails before it and passes after, in
    apps/engine/tests/ — EXCEPT you, the ${bucket} fixer, may only write tests if
    "${bucket}" is "engine-tests". Otherwise note the test needed in "verified" and the
    engine-tests fixer or a later round will add it.
  * Run the verify step for each task. Put what you ran and what it printed in
    "verified". A task you did not verify is a task you did not finish.
  * Do NOT run git add, git commit, git stash, or git checkout. The tree is shared and
    the orchestrator commits. Leave your changes in the working tree.
  * If a fix turns out to be wrong or the finding does not hold up once you are in the
    code, skip it with the reason. That is a correct outcome, not a failure.

Never touch ${REPO}/.env, storage/, or vendor/.`,
      { label: `fix:${bucket}`, phase: "Fix", model: "opus", schema: FIX_SCHEMA },
    );
  }),
);

const applied = fixes.filter(Boolean).flatMap((f) => f.applied || []);
const skipped = fixes.filter(Boolean).flatMap((f) => f.skipped || []);
log(`fixed: ${applied.length} applied, ${skipped.length} skipped`);

phase("Gate");

const GATE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["green", "results"],
  properties: {
    green: { type: "boolean" },
    results: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["check", "passed", "output"],
        properties: {
          check: { type: "string" },
          passed: { type: "boolean" },
          output: { type: "string", description: "The last few lines, verbatim." },
        },
      },
    },
    repaired: { type: "string", description: "Anything you had to fix to get green." },
  },
};

const gate = await agent(
  `${CONTEXT}

Round ${ROUND}'s fixers have just edited the working tree across four directory trees
in parallel. Verify the repository is still whole, and repair it if not.

Run every one of these from ${REPO}, and report each verbatim:

  1. cd apps/engine && STUDIO_PERSIST=false .venv/bin/python -m pytest tests -q
  2. cd apps/engine && .venv/bin/python -m ruff check engine tests
  3. cd apps/engine && .venv/bin/python -m ruff format --check engine tests
  4. apps/engine/.venv/bin/python apps/engine/scripts/export_openapi.py --check
  5. npm run lint
  6. npm run typecheck
  7. npm run build

If a check fails, FIX IT — this is the gate, not a report. You may edit any file to get
green, including across trees, because the parallel fixers are finished and you are the
only writer now. Prefer the minimal correct repair.

Two specific things to watch for, because parallel fixers cause them:
  * ruff format failing on a file another fixer wrote — just run ruff format on it.
  * openapi.json going stale if an API route or model changed. The fix is
    apps/engine/.venv/bin/python apps/engine/scripts/export_openapi.py followed by
    npm run generate, then commit BOTH files. A stale contract makes CI red.

Do NOT run git commit or git add. Report green=true only if all seven pass.`,
  { label: "gate", phase: "Gate", model: "opus", schema: GATE_SCHEMA },
);

log(`gate: ${gate && gate.green ? "GREEN" : "RED"}`);

return {
  round: ROUND,
  raw: raw.length,
  dropped: (triage && triage.dropped) || [],
  refuted: killed.map((k) => ({ title: k.finding.title, why: k.votes.map((v) => v.why) })),
  confirmed,
  summary: plan && plan.summary,
  tasks,
  applied,
  skipped,
  gate,
};
