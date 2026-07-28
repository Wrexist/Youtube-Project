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

console.log(`  CSS toolchain loads on ${platform}, and the platform pins match`);
