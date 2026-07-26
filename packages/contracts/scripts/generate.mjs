/**
 * Regenerate `src/schema.d.ts` from the engine's OpenAPI document.
 *
 * `--check` regenerates into memory and fails if the committed file differs. That
 * is what CI runs: a hand-edited contract, or one left stale after an endpoint
 * changed, is exactly the drift `packages/contracts` exists to prevent.
 *
 * The schema is read from `openapi.json`, which the engine exports. Fetching a
 * running server instead would make codegen depend on a live engine, and the web
 * app is meant to build without one.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = resolve(here, "../openapi.json");
const outPath = resolve(here, "../src/schema.d.ts");

const BANNER = `/**
 * GENERATED — do not edit.
 *
 * Source: packages/contracts/openapi.json, exported from the engine.
 * Regenerate: npm run generate --workspace=@studio/contracts
 *
 * CLAUDE.md: "Types come from packages/contracts. Never hand-write a type that
 * mirrors an API response."
 */

`;

const ast = await openapiTS(new URL("file://" + schemaPath));
const generated = BANNER + astToString(ast);

if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(outPath, "utf8");
  } catch {
    console.error("src/schema.d.ts is missing — run: npm run generate --workspace=@studio/contracts");
    process.exit(1);
  }
  if (current !== generated) {
    console.error(
      "src/schema.d.ts is out of date with openapi.json.\n" +
        "Run: npm run generate --workspace=@studio/contracts",
    );
    process.exit(1);
  }
  console.log("contracts are up to date");
} else {
  writeFileSync(outPath, generated);
  console.log(`wrote ${outPath}`);
}
