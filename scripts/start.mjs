/**
 * Start the whole app with one command.
 *
 *   npm start
 *
 * Studio is two processes — a FastAPI engine and a Next dev server — and starting
 * it meant two terminals, one of which needed a path into a virtualenv that
 * differs by platform:
 *
 *     apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080
 *     .\apps\engine\.venv\Scripts\python -m uvicorn ...        (Windows)
 *
 * That is the single largest piece of friction between cloning this and seeing it
 * work, and it is friction with no purpose: nobody wants to run half of Studio.
 * So this spawns both, labels their output, and takes both down together — if the
 * engine dies, a web app talking to nothing is worse than no web app, because it
 * silently falls back to demo data and looks like it is working.
 *
 * Deliberately dependency-free. `concurrently` would do this in a line, but it is
 * one more thing to install before the thing you installed can start, on a script
 * whose entire job is removing steps.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WINDOWS = process.platform === "win32";

// Same resolution as scripts/setup.*: `Scripts/python.exe` on Windows, `bin/python`
// everywhere else. Getting this wrong is the "The module 'apps' could not be
// loaded" error, which names nothing useful.
const PYTHON = join(
  ROOT,
  "apps/engine/.venv",
  WINDOWS ? "Scripts/python.exe" : "bin/python",
);

const ENGINE_PORT = process.env.STUDIO_ENGINE_PORT ?? "8080";
const WEB_PORT = process.env.PORT ?? "3000";

if (!existsSync(PYTHON)) {
  console.error(
    `\nNo Python environment at apps/engine/.venv.\n\n` +
      `Run setup first — it installs everything and takes a couple of minutes:\n\n` +
      (WINDOWS ? `  .\\scripts\\setup.ps1\n\n` : `  ./scripts/setup.sh\n\n`),
  );
  process.exit(1);
}

/**
 * Is something already listening there?
 *
 * Checked before spawning anything, because the alternative is what uvicorn
 * prints on its own:
 *
 *     ERROR:    [Errno 98] Address already in use
 *
 * which does not name the port, does not say what is holding it, and does not
 * mention the overwhelmingly likely cause — that Studio is already running in
 * another terminal. Someone meeting that on their first day has no way to tell it
 * apart from a broken install.
 */
function portInUse(port) {
  return new Promise((resolve) => {
    const probe = createServer();
    probe.once("error", (error) => resolve(error.code === "EADDRINUSE"));
    probe.once("listening", () => probe.close(() => resolve(false)));
    probe.listen(port, "127.0.0.1");
  });
}

const busy = [];
for (const [name, port] of [
  ["engine", ENGINE_PORT],
  ["web", WEB_PORT],
]) {
  if (await portInUse(port)) busy.push(`${name} (port ${port})`);
}
if (busy.length > 0) {
  console.error(
    `\nSomething is already listening on ${busy.join(" and ")}.\n\n` +
      `Studio is probably already running in another terminal — check there first;\n` +
      `if it is, you do not need to start it again.\n\n` +
      `Otherwise, stop whatever is holding the port, or pick different ones:\n\n` +
      (WINDOWS
        ? `  $env:PORT=3001; $env:STUDIO_ENGINE_PORT=8081; npm start\n\n`
        : `  PORT=3001 STUDIO_ENGINE_PORT=8081 npm start\n\n`),
  );
  process.exit(1);
}

/** ANSI colour, unless the output is being piped or NO_COLOR is set. */
const tty = process.stdout.isTTY && !process.env.NO_COLOR;
const paint = (code, text) => (tty ? `[${code}m${text}[0m` : text);

const children = [];
let shuttingDown = false;

function run(name, colour, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd ?? ROOT,
    env: { ...process.env, ...options.env },
    // Piped rather than inherited so each line can be labelled. With both
    // processes writing straight to the terminal, a traceback from one is
    // indistinguishable from a traceback from the other.
    stdio: ["ignore", "pipe", "pipe"],
    // .cmd shims on Windows are not executables; without a shell, spawn fails
    // with a bare ENOENT that reads like the tool is not installed.
    shell: WINDOWS,
  });

  const label = paint(colour, name.padEnd(6));
  const relay = (stream) => {
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      // The last element is whatever came after the final newline — an
      // incomplete line. Held back, or progress output gets split mid-word
      // across two labelled lines.
      buffer = lines.pop() ?? "";
      for (const line of lines) console.log(`${label} ${line}`);
    });
    stream.on("end", () => {
      if (buffer) console.log(`${label} ${buffer}`);
    });
  };
  relay(child.stdout);
  relay(child.stderr);

  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    console.log(
      `\n${label} exited (${signal ?? `code ${code}`}). Stopping the other half too.\n`,
    );
    stop(code ?? 1);
  });
  child.on("error", (error) => {
    console.error(`${label} could not start: ${error.message}`);
    stop(1);
  });

  children.push(child);
  return child;
}

function stop(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
    }
  }
  // A moment for SIGTERM to be honoured before the process itself goes away —
  // uvicorn's reloader needs it to take its worker down, and skipping the wait
  // leaves an orphan holding port 8080 that the next `npm start` collides with.
  setTimeout(() => process.exit(code), 400);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    console.log("\nShutting down.");
    stop(0);
  });
}

console.log(
  `\n  ${paint(36, "Studio")}\n` +
    `  web     http://localhost:${WEB_PORT}\n` +
    `  engine  http://localhost:${ENGINE_PORT}\n` +
    `  ${paint(90, `first run? open http://localhost:${WEB_PORT}/setup and paste your keys`)}\n`,
);

run(
  "engine",
  35,
  PYTHON,
  ["-m", "uvicorn", "engine.main:app", "--reload", "--port", ENGINE_PORT],
  // From apps/engine, because Settings reads `.env` relative to the working
  // directory and alembic resolves its migrations the same way.
  { cwd: join(ROOT, "apps/engine") },
);

run("web", 36, WINDOWS ? "npm.cmd" : "npm", ["run", "dev", "--workspace=@studio/web"], {
  env: {
    PORT: WEB_PORT,
    // Told explicitly rather than left to the default, so a non-default engine
    // port works without anyone having to discover a second variable.
    ENGINE_URL: process.env.ENGINE_URL ?? `http://localhost:${ENGINE_PORT}`,
    NEXT_PUBLIC_ENGINE_URL:
      process.env.NEXT_PUBLIC_ENGINE_URL ?? `http://localhost:${ENGINE_PORT}`,
  },
});
