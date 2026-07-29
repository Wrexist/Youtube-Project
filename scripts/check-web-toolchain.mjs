/**
 * Can the CSS toolchain actually load on this machine?
 *
 * Tailwind v4 compiles CSS through two native binaries — `@tailwindcss/oxide` and
 * `lightningcss` — and npm records an optional dependency only for the platform it
 * resolved on. A `package-lock.json` generated on Linux therefore has no win32
 * entry at all, `npm install` on Windows never fetches the binary, and the first
 * thing anyone sees is a 500 on `/` with:
 *
 *     Cannot find module '../lightningcss.win32-x64-msvc.node'
 *
 * which names a file nobody has heard of and reads like a corrupt install. The
 * root package.json now pins every variant so the lockfile carries all of them,
 * but a `node_modules` installed before that fix is still broken — so this runs at
 * the end of setup, where it can say what to do, instead of leaving it to the
 * first page load.
 *
 *   node scripts/check-web-toolchain.mjs          exit 0 if the toolchain is sound
 */

import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const platform = `${process.platform}-${process.arch}`;

/** Does the toolchain load at all? The half that matters on the day. */
function checkLoads() {
  try {
    // Loading the PostCSS plugin pulls in oxide, which pulls in lightningcss.
    // Importing them individually would miss the nested resolution that actually
    // fails, which is the whole thing being checked.
    require("@tailwindcss/postcss");
    return null;
  } catch (error) {
    return /Cannot find module '(.+?)'/.exec(error.message)?.[1] ?? "a native binary";
  }
}

function versionOf(...candidates) {
  for (const relative of candidates) {
    const path = join(ROOT, "node_modules", relative, "package.json");
    if (existsSync(path)) return JSON.parse(readFileSync(path, "utf8")).version;
  }
  return null;
}

/**
 * Do the pins in optionalDependencies still match what is installed?
 *
 * This is the half CI can check. Running on Linux it cannot notice a missing win32
 * binary — nothing there needs one — but a Tailwind bump that leaves the pins
 * behind is exactly what would silently reintroduce the problem for Windows, and
 * that *is* visible from here.
 */
function checkPins() {
  const pins = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"))
    .optionalDependencies ?? {};

  // lightningcss is normally hoisted, but Tailwind pins its own copy, so it can
  // also sit nested. Not finding it is not a failure — there is simply nothing to
  // compare against, and reporting a phantom drift would be worse than silence.
  const installed = {
    "@tailwindcss/oxide": versionOf("@tailwindcss/oxide"),
    lightningcss: versionOf("@tailwindcss/node/node_modules/lightningcss", "lightningcss"),
  };

  const stale = [];
  for (const [name, pinned] of Object.entries(pins)) {
    const family = name.startsWith("@tailwindcss/oxide") ? "@tailwindcss/oxide" : "lightningcss";
    const actual = installed[family];
    if (actual && pinned !== actual) {
      stale.push(`    ${name} pinned ${pinned}, but ${family} is ${actual}`);
    }
  }
  return stale;
}

/**
 * Is every pin actually recorded in the lockfile?
 *
 * `checkPins` above compares versions, which catches a Tailwind bump that left the
 * pins behind. It cannot catch the failure the pins exist to prevent, because that
 * one is about *presence*: npm records an optional dependency only for the platform
 * it resolved on, so a lockfile generated on Linux carries no win32 entry, `npm ci`
 * on Windows never fetches the binary, and Tailwind dies at the first CSS import.
 *
 * Pinning every variant in `optionalDependencies` is what forces them all into the
 * lockfile — but only if the lockfile was regenerated afterwards. Add a pin, forget
 * to `npm install`, commit: the versions all match, this script prints success, and
 * Windows is broken again in exactly the original way. That is the gap, and it is
 * invisible from Linux without looking at the lockfile itself.
 */
function checkLockfile() {
  const lockPath = join(ROOT, "package-lock.json");
  if (!existsSync(lockPath)) return []; // nothing to check against

  const lock = JSON.parse(readFileSync(lockPath, "utf8"));
  const pins = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"))
    .optionalDependencies ?? {};

  // npm 7+ lockfiles key packages by install path. A hoisted dependency lands at
  // `node_modules/<name>`; the nested copies are checked by substring so a package
  // Tailwind installs under itself still counts as recorded.
  const recorded = Object.keys(lock.packages ?? {});
  return Object.keys(pins).filter(
    (name) => !recorded.some((path) => path === `node_modules/${name}` || path.endsWith(`/node_modules/${name}`)),
  );
}

/**
 * Is there a stale nested install shadowing the root one?
 *
 * A clean `npm install` in this repo hoists everything and leaves no
 * `apps/web/node_modules` at all. One that exists is left over from an earlier
 * layout, and it wins over the root copy for anything inside `apps/web` — which
 * is how a machine ended up running Next 16 in the dev server while
 * `apps/web/node_modules/next` was a Next 10-era tree, dragging in webpack,
 * styled-jsx and autoprefixer 9. `npm audit` reported 107 findings including a
 * critical, against 14 on a clean tree, and none of the extras were real.
 *
 * Deleting the root `node_modules` does not touch these. That is the trap.
 */
function checkNested() {
  const rootVersion = (name) => versionOf(name);
  const shadowed = [];

  for (const workspace of ["apps/web", "packages/contracts"]) {
    const nested = join(ROOT, workspace, "node_modules");
    if (!existsSync(nested)) continue;

    for (const name of ["next", "react", "react-dom", "tailwindcss", "typescript"]) {
      const path = join(nested, name, "package.json");
      if (!existsSync(path)) continue;
      const nestedVersion = JSON.parse(readFileSync(path, "utf8")).version;
      const root = rootVersion(name);
      if (root && nestedVersion !== root) {
        shadowed.push(`    ${workspace}/node_modules/${name} is ${nestedVersion}, root is ${root}`);
      }
    }
  }
  return shadowed;
}

const shadowed = checkNested();
if (shadowed.length) {
  console.error("\n  A stale nested node_modules is shadowing the root install:\n");
  console.error(shadowed.join("\n"));
  console.error("\n  A clean install in this repo hoists everything and creates none of");
  console.error("  these. Deleting the root node_modules does not remove them — delete");
  console.error("  all of them together:\n");
  if (process.platform === "win32") {
    console.error("    Remove-Item -Recurse -Force node_modules, apps\\web\\node_modules, `");
    console.error("      packages\\contracts\\node_modules -ErrorAction SilentlyContinue");
    console.error("    npm install\n");
  } else {
    console.error("    rm -rf node_modules apps/*/node_modules packages/*/node_modules");
    console.error("    npm install\n");
  }
  process.exit(1);
}

const missing = checkLoads();
if (missing) {
  console.error(`\n  The CSS toolchain cannot load on ${platform}.`);
  console.error(`  Missing: ${missing}\n`);
  console.error("  This is an npm optional-dependency problem, not a broken machine:");
  console.error("  node_modules was installed from a lockfile that had no entry for");
  console.error("  this platform. Reinstall from scratch:\n");
  if (process.platform === "win32") {
    console.error("    Remove-Item -Recurse -Force node_modules");
    console.error("    npm install\n");
  } else {
    console.error("    rm -rf node_modules");
    console.error("    npm install\n");
  }
  process.exit(1);
}

const stale = checkPins();
if (stale.length) {
  console.error("\n  optionalDependencies have drifted from the installed Tailwind:\n");
  console.error(stale.join("\n"));
  console.error("\n  Update the pins in the root package.json to match, then `npm install`.");
  console.error("  Left stale, Windows installs the wrong binary version or none at all.\n");
  process.exit(1);
}

const unrecorded = checkLockfile();
if (unrecorded.length) {
  console.error("\n  These platform pins are not in package-lock.json:\n");
  for (const name of unrecorded) console.error(`    ${name}`);
  console.error("\n  They were added to package.json but the lockfile was never");
  console.error("  regenerated, so `npm ci` will not fetch them — which is the exact");
  console.error("  state that breaks Tailwind on the platforms they exist to cover.\n");
  console.error("    npm install        # then commit the updated package-lock.json\n");
  process.exit(1);
}

console.log(`  CSS toolchain loads on ${platform}, pins match, and the lockfile has them all`);
