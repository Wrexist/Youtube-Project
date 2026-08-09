/**
 * `npm audit`, with an explicit allowlist.
 *
 *   node scripts/audit.mjs
 *
 * The CI step this replaces was a bare `npm audit --audit-level=high`, with a
 * comment stating the policy it could not actually implement:
 *
 *   > Advisories with no upstream fix belong in an explicit allowlist, not in a
 *   > threshold that hides every finding below it.
 *
 * npm has no allowlist. So the only levers were the threshold — which hides
 * everything below it, including things that *do* have fixes — and `--omit=dev`,
 * which hides an entire dependency class for the sake of one advisory. Both are
 * the failure the comment warns about, one directory up.
 *
 * This fails on any high or critical advisory that is not named below, with the
 * reason it was accepted and what would let it be removed. A new advisory is a
 * red build; a known one is a line of text somebody had to write.
 */

import { execFileSync } from "node:child_process";

/**
 * Advisories accepted, with why and what retires them.
 *
 * Every entry is a debt. Keep the reason specific enough that the next person
 * can check whether it still holds, rather than assuming it does.
 */
const ALLOWED = {
  "GHSA-5p4m-2wfm-xmqj": {
    what: "js-yaml <=4.3.0 — quadratic CPU consumption resolving `!!omap`",
    why:
      "Build-time only: it reaches us through openapi-typescript → " +
      "@redocly/openapi-core, which parses one file this repo generates itself " +
      "(packages/contracts/openapi.json). It never runs in the app and never sees " +
      "untrusted YAML — `npm audit --omit=dev` reports zero.",
    retire:
      "js-yaml 4.3.1 is patched, but @redocly's range resolves to 4.3.0 and npm " +
      "will not override it without regenerating the lockfile from scratch — " +
      "which drops the platform binaries pinned in package.json's " +
      "optionalDependencies, the exact breakage `npm run check:toolchain` exists " +
      "to catch. Remove this once @redocly ships a release that pulls 4.3.1+.",
  },
};

/** npm exits non-zero when it finds anything, so the output is read either way. */
function audit() {
  try {
    return execFileSync("npm", ["audit", "--json"], {
      encoding: "utf8",
      shell: process.platform === "win32",
      maxBuffer: 32 * 1024 * 1024,
    });
  } catch (error) {
    if (typeof error.stdout === "string" && error.stdout.trim()) return error.stdout;
    throw error;
  }
}

const report = JSON.parse(audit());
const serious = ["high", "critical"];

/** Every high/critical advisory in the tree, by its GHSA id. */
const found = new Map();
for (const [name, vulnerability] of Object.entries(report.vulnerabilities ?? {})) {
  if (!serious.includes(vulnerability.severity)) continue;
  for (const via of vulnerability.via ?? []) {
    // A string `via` is a pointer to another vulnerable package in the same
    // chain, not an advisory of its own — the advisory is recorded once, on the
    // package that actually carries it.
    if (typeof via !== "object" || !via.url) continue;
    const id = via.url.split("/").pop();
    if (!found.has(id)) found.set(id, { id, title: via.title, severity: via.severity, paths: [] });
    found.get(id).paths.push(name);
  }
}

const unexpected = [...found.values()].filter((a) => !ALLOWED[a.id]);
const accepted = [...found.values()].filter((a) => ALLOWED[a.id]);

for (const advisory of accepted) {
  console.log(`  allowed  ${advisory.id}  ${ALLOWED[advisory.id].what}`);
}

// A stale entry is its own small lie: it claims a debt that has been paid.
for (const id of Object.keys(ALLOWED)) {
  if (!found.has(id)) {
    console.log(`  stale    ${id} is allowlisted but no longer reported — remove it`);
  }
}

if (unexpected.length === 0) {
  console.log(`\n  no unexpected high or critical advisories (${accepted.length} allowed)\n`);
  process.exit(0);
}

console.error(`\n  ${unexpected.length} high or critical advisory not in the allowlist:\n`);
for (const advisory of unexpected) {
  console.error(`    ${advisory.severity}  ${advisory.title}`);
  console.error(`    ${advisory.id}  via ${[...new Set(advisory.paths)].join(", ")}`);
  console.error("");
}
console.error("  Fix it, or add it to ALLOWED in scripts/audit.mjs with a reason.\n");
process.exit(1);
