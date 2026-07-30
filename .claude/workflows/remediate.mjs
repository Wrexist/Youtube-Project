export const meta = {
  name: "remediate",
  description: "Plan and fix an existing set of verified findings, then gate",
  whenToUse:
    "When findings are already verified and only the Plan/Fix/Gate half needs to run — e.g. recovering a round whose planner degenerated.",
  phases: [
    { title: "Plan", detail: "ordered remediation list", model: "fable" },
    { title: "Fix", detail: "one fixer per disjoint directory tree", model: "opus" },
    { title: "Gate", detail: "pytest, ruff, typecheck, build, openapi", model: "opus" },
  ],
};

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

const ROUND = INPUT.round || 0;
const FINDINGS = INPUT.findings || [];
const REPO = "/home/user/Youtube-Project";

if (FINDINGS.length === 0) {
  log("No findings supplied — nothing to remediate.");
  return { round: ROUND, applied: [], skipped: [], note: "no findings" };
}

const CONTEXT = `
You are working in the Studio repository at ${REPO} — a YouTube workflow automation app.
apps/web (Next.js 16), apps/engine (FastAPI + arq + MoviePy), packages/contracts (TS
types generated from the engine's OpenAPI), scripts/ (setup and launcher),
vendor/moneyprinterturbo (READ-ONLY, never imported, never edited).

Read ${REPO}/CLAUDE.md for the five non-negotiables and the house conventions, and
${REPO}/AUDIT-3.md for decisions already argued. The engine venv is at
apps/engine/.venv/bin/python. NEVER read ${REPO}/.env or anything under storage/.

These findings have already survived two independent adversarial verifiers. They are
real. Your job is to fix them, not to re-litigate them — though if you get into the
code and a finding genuinely does not hold, say so rather than changing working code.
`;

phase("Plan");

const PLAN_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["summary", "tasks"],
  properties: {
    summary: { type: "string", minLength: 120 },
    tasks: {
      type: "array",
      minItems: FINDINGS.length,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["step", "bucket", "title", "change", "verify", "blocks"],
        properties: {
          step: { type: "string" },
          bucket: { type: "string", enum: ["engine", "engine-tests", "web", "ops"] },
          title: { type: "string", minLength: 12 },
          change: { type: "string", minLength: 60 },
          verify: { type: "string", minLength: 30 },
          blocks: { type: "string" },
        },
      },
    },
  },
};

const plan = await agent(
  `${CONTEXT}

${FINDINGS.length} verified findings from audit round ${ROUND}. Produce an ordered
remediation plan with AT LEAST one task per finding — every finding must be covered.

${JSON.stringify(FINDINGS, null, 1)}

Rules:
  * Order by dependency first, then severity. If fixing A changes code B touches, A
    comes first and B names it in blocks.
  * "change" must be executable without re-deriving the finding: name the file, the
    function, and what the code should become. At least a sentence or two.
  * "verify" must be a command that can be run or an observation that can be made.
  * Every task must touch only its own bucket's tree, because the fixers run in
    parallel over disjoint trees: engine = apps/engine/engine/**, engine-tests =
    apps/engine/tests/**, web = apps/web/** and packages/**, ops = scripts/**,
    .github/**, infra/**, docker-compose.yml, .dockerignore and root *.md.
  * summary: what this batch is about, in three or four sentences.`,
  { label: "plan", phase: "Plan", model: "fable", schema: PLAN_SCHEMA },
);

const tasks = (plan && plan.tasks) || [];
log(`plan: ${tasks.length} tasks for ${FINDINGS.length} findings — ${plan.summary.slice(0, 200)}`);

phase("Fix");

const TREES = {
  engine: "apps/engine/engine/**",
  "engine-tests": "apps/engine/tests/**",
  web: "apps/web/** and packages/**",
  ops: "scripts/**, .github/**, infra/**, docker-compose.yml, .dockerignore, and root *.md",
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
          verified: { type: "string" },
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

You are the ${bucket} fixer. Apply these ${mine.length} tasks:

${JSON.stringify(mine, null, 1)}

HARD BOUNDARY. Edit ONLY files under: ${TREES[bucket]}
The other fixers are editing the other trees right now. Editing outside your tree
loses your change or theirs. If a task needs a file outside your tree, skip it and
say so — do not reach across.

How to work:
  * Read before you edit. Use your judgement on the fix; say what you did and why if
    it differs from the plan.
  * Match the surrounding code: same comment style and density. This codebase
    comments the WHY — why a non-obvious choice was made, what broke without it.
  * Run each task's verify step. Put what you ran and what it printed in "verified".
    A task you did not verify is a task you did not finish.
  * Do NOT run git add, git commit, git stash or git checkout. The orchestrator
    commits; leave your work in the tree.
  * Never touch ${REPO}/.env, storage/, or vendor/.`,
      { label: `fix:${bucket}`, phase: "Fix", model: "opus", schema: FIX_SCHEMA },
    );
  }),
);

const applied = fixes.filter(Boolean).flatMap((f) => f.applied || []);
const skipped = fixes.filter(Boolean).flatMap((f) => f.skipped || []);
log(`fixed: ${applied.length} applied, ${skipped.length} skipped`);

phase("Gate");

const gate = await agent(
  `${CONTEXT}

The fixers have just edited the working tree across several directory trees in
parallel. Verify the repository is whole, and repair it if not.

Run every one of these from ${REPO} and report each verbatim:

  1. cd apps/engine && STUDIO_PERSIST=false .venv/bin/python -m pytest tests -q
  2. cd apps/engine && .venv/bin/python -m ruff check engine tests
  3. cd apps/engine && .venv/bin/python -m ruff format --check engine tests
  4. apps/engine/.venv/bin/python apps/engine/scripts/export_openapi.py --check
  5. npm run lint
  6. npm run typecheck
  7. npm run build

If a check fails, FIX IT. You are the only writer now, so you may edit any file.
Two failures parallel fixers reliably cause: ruff format on a file another fixer
wrote (just run ruff format on it), and a stale openapi.json if a route or model
changed — fix with export_openapi.py then npm run generate, and leave BOTH files
changed.

Report green=true only if all seven pass. Do NOT run git commit or git add.`,
  {
    label: "gate",
    phase: "Gate",
    model: "opus",
    schema: {
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
              output: { type: "string" },
            },
          },
        },
        repaired: { type: "string" },
      },
    },
  },
);

log(`gate: ${gate && gate.green ? "GREEN" : "RED"}`);

return { round: ROUND, summary: plan.summary, tasks, applied, skipped, gate };
