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
import { get as httpGet } from "node:http";
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

/**
 * Launcher mode: `npm start -- --open`, which is what the desktop shortcut runs.
 *
 * It opens a browser once the web app answers, and it treats an already-running
 * Studio as success rather than as an error. Someone who double-clicks the icon
 * twice means "show me Studio", not "start a second copy" — reporting a port
 * conflict there would be technically accurate and useless.
 */
const OPEN = process.argv.includes("--open");

if (!existsSync(PYTHON)) {
  const setup = WINDOWS ? ".\\scripts\\setup.ps1" : "./scripts/setup.sh";
  console.error(
    `\nStudio is not installed yet — there is no Python environment at\n` +
      `apps/engine/.venv.\n\n` +
      `Run setup first. It installs everything and takes a couple of minutes:\n\n` +
      `  ${setup}\n\n`,
  );
  // The shortcut launches this with no terminal attached, so an immediate exit
  // means the window vanishes before the message can be read. Hold it open.
  await pause();
  process.exit(1);
}

/** Keep a double-clicked window up long enough to read what went wrong. */
async function pause() {
  if (!OPEN || !process.stdin.isTTY) return;
  process.stdout.write("Press Enter to close.");
  await new Promise((resolve) => process.stdin.once("data", resolve));
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

/**
 * GET a local URL and report the status code, or 0 if it did not answer.
 *
 * `node:http` rather than `fetch`, deliberately. Node 22's `fetch` honours
 * `HTTP_PROXY`/`HTTPS_PROXY`, so on any machine with a proxy configured — every
 * corporate laptop, and this repository's own CI container — a request to
 * *localhost* is handed to the proxy, which cannot route it, and it times out.
 * Measured here: `curl localhost:3000` returned 307 while `fetch` to the same
 * URL aborted after two seconds. That failure is silent and it would have meant
 * the browser never opening, on exactly the machines least able to diagnose it.
 *
 * `node:http` consults no proxy environment at all.
 */
function probe(port, path = "/", timeoutMs = 2000) {
  return new Promise((resolve) => {
    const request = httpGet(
      { host: "127.0.0.1", port, path, timeout: timeoutMs },
      (response) => {
        response.resume(); // Drain, or the socket is never released.
        resolve(response.statusCode ?? 0);
      },
    );
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(0));
  });
}

/**
 * Is the thing on that port *Studio*, or something else?
 *
 * A port being busy says nothing about who is holding it. Asking the engine's
 * own health endpoint is the difference between "Studio is already up, here it
 * is" and "something unrelated owns 8080, and starting would fail" — two
 * situations with opposite right answers, which a port probe alone cannot tell
 * apart.
 */
async function studioIsUp() {
  return (await probe(ENGINE_PORT, "/health", 1500)) === 200;
}

/**
 * Open the app in the default browser, on whatever platform this is.
 *
 * Failing to open a browser must never be fatal — Studio is running either way,
 * and the URL is printed. That took a real bug to get right: `spawn` reports a
 * missing executable through an **asynchronous `error` event**, not a throw, so
 * a `try/catch` around the call catches nothing and the unhandled event takes
 * the whole process down with it. Observed here: no `xdg-open` in the container,
 * `Error: spawn xdg-open ENOENT`, and the launcher crashed — killing both halves
 * it had just started, having orphaned them holding their ports. Any headless
 * Linux box or minimal desktop would have hit exactly that.
 */
function openBrowser(url) {
  const [command, args] =
    process.platform === "darwin"
      ? ["open", [url]]
      : WINDOWS
        // `start` is a cmd builtin, not an executable, and its first quoted
        // argument is taken as the window title — hence the empty "".
        ? ["cmd", ["/c", "start", "", url]]
        : ["xdg-open", [url]];

  const fallback = () => console.log(`  Could not open a browser. Go to ${url}\n`);

  try {
    const child = spawn(command, args, { stdio: "ignore", detached: true });
    child.on("error", fallback); // The one that actually fires.
    child.unref();
  } catch {
    fallback(); // Synchronous failures are rarer, but not impossible.
  }
}

const busy = [];
for (const [name, port] of [
  ["engine", ENGINE_PORT],
  ["web", WEB_PORT],
]) {
  if (await portInUse(port)) busy.push(`${name} (port ${port})`);
}

if (busy.length > 0) {
  if (OPEN && (await studioIsUp())) {
    // Double-clicked while already running. Show it and get out of the way.
    //
    // The URL is printed rather than only opened: this branch exits immediately,
    // so `openBrowser`'s error handler — which is asynchronous — never gets to
    // report a failure. Naming the address means the window is useful even when
    // no browser could be launched.
    const url = `http://localhost:${WEB_PORT}`;
    console.log(`\n  Studio is already running.\n  Opening ${url}\n`);
    openBrowser(url);
    // Awaited, not a bare setTimeout: a timer would schedule the exit and let
    // execution fall straight through to the spawn calls below, which would then
    // fight the very instance this branch just found. The pause itself is for the
    // browser spawn to reach the OS before this process goes away.
    await new Promise((resolve) => setTimeout(resolve, 250));
    process.exit(0);
  } else {
    console.error(
      `\nSomething is already listening on ${busy.join(" and ")}.\n\n` +
        `If Studio is already running in another window, use that one.\n\n` +
        `Otherwise, stop whatever is holding the port, or pick different ones:\n\n` +
        (WINDOWS
          ? `  $env:PORT=3001; $env:STUDIO_ENGINE_PORT=8081; npm start\n\n`
          : `  PORT=3001 STUDIO_ENGINE_PORT=8081 npm start\n\n`),
    );
    await pause();
    process.exit(1);
  }
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
    (OPEN
      ? `  ${paint(90, "starting — your browser will open in a moment")}\n` +
        `  ${paint(90, "keep this window open; closing it stops Studio")}\n`
      : `  ${paint(90, `first run? open http://localhost:${WEB_PORT}/setup and paste your keys`)}\n`),
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

// `-p` passed explicitly. `apps/web`'s dev script used to hardcode `-p 3000`,
// and that flag beat the PORT environment variable — so asking for 3100 still
// started Next on 3000, which is exactly the collision the port override exists
// to escape, and exactly what the "pick different ones" advice above tells
// people to do. The hardcoded flag is gone (Next defaults to 3000 anyway, so a
// bare `npm run dev` is unchanged) and the port is passed from here instead, so
// there is one place that decides it. `--` separates npm's arguments from the
// script's.
run(
  "web",
  36,
  WINDOWS ? "npm.cmd" : "npm",
  ["run", "dev", "--workspace=@studio/web", "--", "-p", WEB_PORT],
  {
    env: {
      PORT: WEB_PORT,
      // Told explicitly rather than left to the default, so a non-default engine
      // port works without anyone having to discover a second variable.
      ENGINE_URL: process.env.ENGINE_URL ?? `http://localhost:${ENGINE_PORT}`,
      NEXT_PUBLIC_ENGINE_URL:
        process.env.NEXT_PUBLIC_ENGINE_URL ?? `http://localhost:${ENGINE_PORT}`,
    },
  },
);

/**
 * Wait for the web app to actually serve, then open it.
 *
 * Polling rather than opening immediately: Next needs a few seconds to compile
 * on a cold start, and a browser that arrives first shows a connection-refused
 * page. Someone who double-clicked an icon reads that as "the app is broken",
 * and reloading is not an obvious remedy when you never asked for a browser.
 *
 * Polls the *web* port, not the engine's — the engine is usually up first, and
 * the page is what was asked for.
 */
if (OPEN) {
  const url = `http://localhost:${WEB_PORT}`;
  const deadline = 90_000; // Cold `next dev` on a slow disk is genuinely slow.
  const started = Date.now();

  const poll = setInterval(async () => {
    if (shuttingDown) return clearInterval(poll);

    if (Date.now() - started > deadline) {
      clearInterval(poll);
      console.log(`\n  Still starting. Open ${url} yourself once it is ready.\n`);
      return;
    }
    // Any answer means the server is listening — a 404, a 500 or the 307 to
    // /welcome are all something a browser can usefully show, and all beat
    // waiting longer. 0 means it did not answer; the interval is the retry.
    if ((await probe(WEB_PORT)) > 0) {
      clearInterval(poll);
      console.log(`\n  Opening ${url}\n`);
      openBrowser(url);
    }
  }, 700);
  // Do not let the poll itself hold the process open past a shutdown.
  poll.unref?.();
}
