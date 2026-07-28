/**
 * Delete every node_modules in the workspace and reinstall from scratch.
 *
 *   npm run reinstall
 *
 * Exists because doing this by hand goes wrong in two specific ways, both of
 * which cost a real afternoon:
 *
 *  1. `rm -rf node_modules` only removes the root one. A clean install here hoists
 *     everything and creates no workspace-level node_modules at all, so one that
 *     survives is stale — and it *wins* over the root copy for anything resolved
 *     inside that workspace. A machine ran Next 16 in its dev server banner with a
 *     Next 10 tree underneath, which is where `Configuring Next.js via
 *     'next.config.ts' is not supported` and 93 phantom npm advisories came from.
 *
 *  2. On Windows a running dev server holds a lock on those files, the delete
 *     fails with EBUSY or EPERM, and the usual `-ErrorAction SilentlyContinue`
 *     hides it — so it looks like it worked and nothing changed.
 *
 * So this one reports what it deleted, refuses to continue quietly if a delete
 * failed, and says which process to stop.
 *
 * Plain Node stdlib on purpose: it has to run while it is deleting node_modules.
 */

import { execSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Root plus every workspace, derived from package.json rather than hardcoded. */
function targets() {
  const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  const globs = pkg.workspaces ?? [];
  const dirs = ["."];

  for (const glob of globs) {
    if (!glob.endsWith("/*")) {
      dirs.push(glob);
      continue;
    }
    // "packages/*" — expand it, so a new workspace is covered without editing this.
    const parent = join(ROOT, glob.slice(0, -2));
    if (!existsSync(parent)) continue;
    for (const entry of readdirSafe(parent)) {
      dirs.push(join(glob.slice(0, -2), entry));
    }
  }
  return dirs.map((d) => ({ label: d, path: join(ROOT, d, "node_modules") }));
}

function readdirSafe(path) {
  try {
    return readdirSync(path, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name);
  } catch {
    return [];
  }
}

console.log("\nRemoving node_modules:\n");

const failures = [];
for (const { label, path } of targets()) {
  if (!existsSync(path)) {
    console.log(`  absent   ${label}/node_modules`);
    continue;
  }
  try {
    rmSync(path, { recursive: true, force: true });
    console.log(`  deleted  ${label}/node_modules`);
  } catch (error) {
    console.log(`  FAILED   ${label}/node_modules — ${error.code ?? error.message}`);
    failures.push({ label, error });
  }
}

if (failures.length) {
  console.error("\n  Could not delete everything. On Windows this is almost always a");
  console.error("  running dev server holding the files open.\n");
  console.error("  Stop it (Ctrl+C in that terminal), or:\n");
  console.error(
    process.platform === "win32"
      ? "    Get-Process node | Stop-Process -Force\n"
      : "    pkill -f 'next dev'\n",
  );
  console.error("  then run `npm run reinstall` again.\n");
  process.exit(1);
}

console.log("\nInstalling...\n");
execSync("npm install", { cwd: ROOT, stdio: "inherit" });

console.log("\nVerifying...\n");
execSync("node scripts/check-web-toolchain.mjs", { cwd: ROOT, stdio: "inherit" });
console.log("");
