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

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { get as httpGet } from "node:http";
import { createConnection, createServer } from "node:net";
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

// A port the user asked for is an instruction; a default is only our preference.
// The difference decides what happens when one is taken — see the block below.
const ENGINE_PORT_ASKED = process.env.STUDIO_ENGINE_PORT;
const WEB_PORT_ASKED = process.env.PORT;
let ENGINE_PORT = ENGINE_PORT_ASKED ?? "8080";
let WEB_PORT = WEB_PORT_ASKED ?? "3000";

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
        ? // `start` is a cmd builtin, not an executable, and its first quoted
          // argument is taken as the window title — hence the empty "".
          ["cmd", ["/c", "start", "", url]]
        : ["xdg-open", [url]];

  const fallback = () =>
    console.log(`  Could not open a browser. Go to ${url}\n`);

  try {
    const child = spawn(command, args, { stdio: "ignore", detached: true });
    child.on("error", fallback); // The one that actually fires.
    child.unref();
  } catch {
    fallback(); // Synchronous failures are rarer, but not impossible.
  }
}

/**
 * The Chromium-based browser to borrow a window from, or null if there is none.
 *
 * Absolute paths rather than a PATH lookup: none of these put themselves on PATH
 * on Windows, which is the platform this matters on.
 */
function chromiumBinary() {
  const candidates = WINDOWS
    ? [
        `${process.env.ProgramFiles}\\Microsoft\\Edge\\Application\\msedge.exe`,
        `${process.env["ProgramFiles(x86)"]}\\Microsoft\\Edge\\Application\\msedge.exe`,
        `${process.env.ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe`,
        `${process.env["ProgramFiles(x86)"]}\\Google\\Chrome\\Application\\chrome.exe`,
        `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
        `${process.env.ProgramFiles}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      ]
    : process.platform === "darwin"
      ? [
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
      : [
          "/usr/bin/google-chrome",
          "/usr/bin/microsoft-edge",
          // Brave is listed on the other two platforms, so leaving it out here
          // meant a Linux machine with only Brave fell back to a tab for no
          // reason. Its package names differ by channel; -stable is the usual one.
          "/usr/bin/brave-browser-stable",
          "/usr/bin/brave-browser",
          "/usr/bin/brave",
          "/usr/bin/chromium",
          "/usr/bin/chromium-browser",
          "/snap/bin/chromium",
        ];

  // `undefined` interpolates as the string "undefined" rather than vanishing, so
  // an unset ProgramFiles(x86) on a 32-bit-free machine yields a path that simply
  // does not exist. existsSync filters it either way.
  return candidates.find((path) => existsSync(path)) ?? null;
}

/**
 * Open the app in a window of its own: no tab strip, no address bar, no
 * bookmarks - a taskbar entry that behaves like a program rather than a page.
 *
 * `--app=` is a Chromium flag, so this needs Edge, Chrome or Brave, any of which
 * is present on the overwhelming majority of Windows machines (Edge ships with
 * the OS). Returns false when there is none, and the caller falls back to a
 * normal browser tab; Studio is the same app either way.
 *
 * Deliberately not Electron. Wrapping this in a real .exe means shipping a second
 * browser engine, a build step and a signing story to change what the window
 * frame looks like - and the app still has to work in a plain tab regardless,
 * because that is what `npm run dev` gives you.
 */
function openAppWindow(url) {
  const binary = chromiumBinary();
  if (!binary) return false;

  try {
    const child = spawn(binary, [`--app=${url}`, "--window-size=1440,940"], {
      stdio: "ignore",
      detached: true,
    });
    // Same asynchronous-error trap as openBrowser, with the same remedy: report
    // by falling back rather than letting an unhandled event kill the launcher
    // and orphan both halves.
    child.on("error", () => openBrowser(url));
    child.unref();
    return true;
  } catch {
    return false;
  }
}

/**
 * Show Studio, in its own window where that is possible.
 *
 * `STUDIO_BROWSER=1` forces an ordinary tab, for anyone who would rather have
 * devtools, extensions and their own profile than a clean frame.
 */
function showStudio(url) {
  if (process.env.STUDIO_BROWSER === "1" || !openAppWindow(url)) {
    openBrowser(url);
  }
}

/**
 * One value out of `apps/engine/.env`.
 *
 * The launcher needs `STUDIO_REDIS_URL` to know where to look for Redis, and the
 * engine reads it from that file rather than from the environment — so probing
 * `process.env` alone would check localhost on every machine that had pointed
 * Studio at a Redis somewhere else, decide it was absent, and quietly run without
 * a worker. Deliberately not a dotenv dependency for one key.
 *
 * Whatever comes back is treated as a URL and never printed: a Redis URL can carry
 * a password, and CLAUDE.md #4 is that secrets are never logged.
 */
function fromEnvFile(key) {
  try {
    const text = readFileSync(join(ROOT, "apps/engine/.env"), "utf8");
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1 || trimmed.slice(0, eq).trim() !== key) continue;
      // Strip one layer of matching quotes, which is what python-dotenv does.
      return trimmed
        .slice(eq + 1)
        .trim()
        .replace(/^(['"])(.*)\1$/, "$2");
    }
  } catch {
    // No .env on a fresh clone. The default below is the same one Settings uses.
  }
  return null;
}

/**
 * Is `name` an executable on PATH?
 *
 * Used to decide which "get a Redis" line to print. Telling a machine with no
 * Docker to run `docker compose up -d redis` is a dead end dressed as help — and
 * that is not hypothetical, it is this machine. Advice that cannot be followed is
 * worse than none, because it ends the search.
 *
 * A PATH scan rather than spawning `docker --version`: this runs on the startup
 * path and a subprocess costs a few hundred milliseconds to answer a question the
 * filesystem already knows.
 */
function onPath(name) {
  const exts = WINDOWS ? (process.env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";") : [""];
  for (const dir of (process.env.PATH ?? "").split(WINDOWS ? ";" : ":")) {
    if (!dir) continue;
    for (const ext of exts) {
      if (existsSync(join(dir, name + ext.toLowerCase()))) return true;
      if (existsSync(join(dir, name + ext))) return true;
    }
  }
  return false;
}

/** Where Redis is expected, as a host and port this script can open a socket to. */
function redisTarget() {
  const raw =
    process.env.STUDIO_REDIS_URL ??
    fromEnvFile("STUDIO_REDIS_URL") ??
    "redis://localhost:6379/0";
  try {
    const url = new URL(raw);
    return { host: url.hostname || "localhost", port: Number(url.port) || 6379 };
  } catch {
    return { host: "localhost", port: 6379 };
  }
}

/**
 * Is a Redis answering there?
 *
 * A real `PING` rather than a port probe, because the question that matters is
 * whether the *worker* will be able to connect — and something else holding 6379
 * would pass a port check and then fail every enqueue. Redis's inline protocol
 * answers `+PONG`, and a Redis that wants AUTH answers `-NOAUTH`; both prove a
 * Redis is there, and the worker has the credentials this probe deliberately does
 * not use.
 *
 * No client library, for the same reason the rest of this file has no
 * dependencies: a launcher whose job is removing setup steps should not add one.
 */
function redisAnswers({ host, port }, timeoutMs = 700) {
  return new Promise((resolve) => {
    let settled = false;
    const socket = createConnection({ host, port });
    const finish = (answer) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(answer);
    };
    socket.setTimeout(timeoutMs);
    socket.on("connect", () => socket.write("PING\r\n"));
    socket.on("data", (chunk) => finish(/^[+-]/.test(chunk.toString("utf8", 0, 1))));
    socket.on("timeout", () => finish(false));
    socket.on("error", () => finish(false));
  });
}

/**
 * The first free port at or after `from`, or null if there is no room.
 *
 * `taken` covers ports this run has already claimed but not yet bound: the two
 * halves are assigned before either is spawned, so without it a machine with 3000
 * busy could hand the same replacement to both.
 */
async function firstFreePort(from, taken = []) {
  const start = Number(from);
  for (let port = start; port < start + 20; port += 1) {
    if (taken.includes(String(port))) continue;
    if (!(await portInUse(port))) return String(port);
  }
  return null;
}

const engineBusy = await portInUse(ENGINE_PORT);
const webBusy = await portInUse(WEB_PORT);

if (engineBusy || webBusy) {
  // Ask whether it is *Studio* on those ports before deciding anything, and ask it
  // regardless of `--open`. This used to be `OPEN && studioIsUp()`, which was fine
  // while a busy port was fatal — the error told you Studio might already be
  // running either way. It stopped being fine the moment the port fell back to the
  // next free pair: plain `npm start` with Studio already up would decide the ports
  // belonged to something unrelated, move aside, and bring up a *second* engine and
  // web app. Both then write the same SQLite file, which is the cross-process
  // hazard the quota work exists to prevent, arrived at by the launcher.
  if (await studioIsUp()) {
    const url = `http://localhost:${WEB_PORT}`;
    if (OPEN) {
      // Double-clicked while already running: show it and get out of the way.
      //
      // The URL is printed rather than only opened: this branch exits immediately,
      // so `openBrowser`'s error handler — which is asynchronous — never gets to
      // report a failure. Naming the address means the window is useful even when
      // no browser could be launched.
      console.log(`\n  Studio is already running.\n  Opening ${url}\n`);
      showStudio(url);
      // Awaited, not a bare setTimeout: a timer would schedule the exit and let
      // execution fall straight through to the spawn calls below, which would then
      // fight the very instance this branch just found. The pause itself is for the
      // browser spawn to reach the OS before this process goes away.
      await new Promise((resolve) => setTimeout(resolve, 250));
    } else {
      // From a terminal, opening a browser unasked is rude. Say where it is.
      console.log(`\n  Studio is already running at ${url}\n`);
    }
    process.exit(0);
  }

  // Not Studio, then — something unrelated owns the port. This used to be fatal,
  // and the error told the reader to re-run with `PORT=3001 npm start`. For anyone
  // who got here by double-clicking a desktop shortcut that is a dead end: the
  // whole point of the shortcut is that they never open a terminal, and 3000 and
  // 8080 are two of the most commonly occupied ports on any machine that has ever
  // run another dev server. So move, and say so.
  //
  // A port the user named explicitly is different — that is an instruction, and
  // quietly using a different one would break whatever they pointed at it. Those
  // still fail, loudly, with the command to change them.
  const moves = [];
  const claimed = [];

  for (const half of [
    {
      name: "engine",
      busy: engineBusy,
      asked: ENGINE_PORT_ASKED,
      port: ENGINE_PORT,
      env: "STUDIO_ENGINE_PORT",
    },
    {
      name: "web",
      busy: webBusy,
      asked: WEB_PORT_ASKED,
      port: WEB_PORT,
      env: "PORT",
    },
  ]) {
    if (!half.busy) {
      claimed.push(half.port);
      continue;
    }
    if (half.asked !== undefined) {
      console.error(
        `\nPort ${half.port} is taken, and you asked for it explicitly ` +
          `(${half.env}=${half.port}).\n\n` +
          `Stop whatever is holding it, or name a free one:\n\n` +
          (WINDOWS
            ? `  $env:${half.env}=${Number(half.port) + 1}; npm start\n\n`
            : `  ${half.env}=${Number(half.port) + 1} npm start\n\n`),
      );
      await pause();
      process.exit(1);
    }
    const moved = await firstFreePort(Number(half.port) + 1, claimed);
    if (moved === null) {
      console.error(
        `\nPort ${half.port} is taken, and so is every port up to ` +
          `${Number(half.port) + 20}.\n\n` +
          `Something is very wrong, or a previous Studio did not shut down. ` +
          `Restarting the machine will clear it.\n\n`,
      );
      await pause();
      process.exit(1);
    }
    claimed.push(moved);
    moves.push(`${half.name} ${half.port} → ${moved}`);
    if (half.name === "engine") ENGINE_PORT = moved;
    else WEB_PORT = moved;
  }

  console.log(`\n  Default port in use, moved: ${moves.join(", ")}\n`);
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
    // `onExit` is how a non-essential child opts out of taking everything with it.
    // The engine and the web app are each half of Studio, so one dying makes the
    // other worse than useless — a web app talking to nothing silently falls back
    // to demo data. The worker is different: without it renders merely stop being
    // durable, and killing a working app over that would be the larger failure.
    if (options.onExit) {
      options.onExit(code, signal, label);
      return;
    }
    console.log(
      `\n${label} exited (${signal ?? `code ${code}`}). Stopping the other half too.\n`,
    );
    stop(code ?? 1);
  });
  child.on("error", (error) => {
    console.error(`${label} could not start: ${error.message}`);
    // Same reasoning as the exit handler: a worker that cannot be spawned is a
    // downgrade, not a reason to take the app down. Routed through `onExit` so a
    // failed spawn and a crashed process are handled in one place — a spawn error
    // fires *instead of* `exit`, so without this the supervisor below would never
    // hear about the most likely failure of all, a missing arq.
    if (options.onExit) {
      options.onExit(null, null, label);
      return;
    }
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

/**
 * Is there a Redis for the render worker to talk to?
 *
 * Answered before anything is spawned, so the header can say which of the two
 * worlds this run is in. Printed after the engine had already started logging, it
 * scrolled past under uvicorn's startup — and a warning nobody reads is the same
 * as no warning.
 */
const WORKER_TARGET = redisTarget();
const WORKER_WANTED = process.env.STUDIO_WORKER !== "0";
const DURABLE = WORKER_WANTED && (await redisAnswers(WORKER_TARGET));

/**
 * Whether WSL has a distribution, not merely whether `wsl.exe` exists.
 *
 * Windows 11 ships the `wsl.exe` stub whether or not anything is installed behind
 * it, so a PATH check says yes on a machine where every `wsl` command answers
 * "The Windows Subsystem for Linux is not installed." Suggesting it there is the
 * dead end `onPath` was written to avoid, arrived at from the other side.
 *
 * A subprocess is acceptable here: this runs only on the branch where Redis is
 * already missing, which is the degraded path anyway.
 */
function wslHasDistro() {
  if (!WINDOWS || !onPath("wsl")) return false;
  const result = spawnSync("wsl", ["-l", "-q"], {
    encoding: "utf8",
    timeout: 3000,
    windowsHide: true,
  });
  // `wsl -l -q` writes UTF-16LE, which arrives here as text interleaved with NULs.
  // Stripping them is the difference between "no distros" and "one distro whose
  // name looks like `U b u n t u`".
  const listed = (result.stdout ?? "").replace(/\0/g, "").trim();
  return result.status === 0 && listed.length > 0;
}

/** The shortest route to a Redis *on this machine*, not on an imagined one. */
function redisAdvice() {
  if (onPath("docker")) return "docker compose up -d redis";
  if (wslHasDistro()) return "wsl -e sudo service redis-server start";
  return "see README.md, 'Durable renders'";
}

console.log(
  `\n  ${paint(36, "Studio")}\n` +
    `  web     http://localhost:${WEB_PORT}\n` +
    `  engine  http://localhost:${ENGINE_PORT}\n` +
    // Said plainly either way. The failure this exists to prevent — a forty-minute
    // render lost to a restart — is invisible until it happens, so the state has to
    // be visible before it does. The host and port are named but never the URL: a
    // Redis URL can carry a password (CLAUDE.md #4).
    (DURABLE
      ? `  renders ${paint(32, "durable")} ${paint(90, `— queued through redis at ${WORKER_TARGET.host}:${WORKER_TARGET.port}`)}\n`
      : WORKER_WANTED
        ? `  renders ${paint(33, "not durable")} ${paint(90, `— no redis at ${WORKER_TARGET.host}:${WORKER_TARGET.port}, so they run inside`)}\n` +
          `           ${paint(90, `the engine and stop if it restarts. Fix: ${redisAdvice()}`)}\n`
        : `  renders ${paint(90, "not durable — STUDIO_WORKER=0, no worker started")}\n`) +
    (OPEN
      ? `  ${paint(90, "starting — the Studio window opens in a moment")}\n` +
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
 * The render worker, if there is a Redis for it to talk to.
 *
 * Without this, every render runs inside the API process — so it dies with a
 * restart, a crash, or an edit to any engine file, because uvicorn is started with
 * `--reload`. That is not hypothetical: it cost sixteen completed stages of a
 * seventeen-stage run, and the only symptom was the pipeline going quiet.
 *
 * The engine already handles both worlds. `worker.enqueue` probes Redis, checks
 * that something is actually consuming the queue, and returns false to the
 * in-process path if either answer is no — so starting a worker here changes
 * nothing except whether renders survive. That is why this is allowed to be
 * conditional rather than required: `npm start` on a clone with no Docker must
 * still work, and it does.
 *
 * `STUDIO_WORKER=0` skips it, for anyone who would rather run arq themselves with
 * different settings. Both that and the Redis probe are resolved above, next to
 * the header that reports which world this run is in.
 */
if (DURABLE) {
  // A supervisor, because worker.py says so and nothing was playing the part:
  // "arq's own poll loop has no reconnect of its own, so a Redis that stays down
  // past redis-py's retry ends the worker process; the supervisor is expected to
  // restart it, and an interrupted job comes back resumable."
  //
  // Bounded, because an unbounded restart loop against a permanently broken
  // install is a scrolling wall of the same traceback. After the last attempt it
  // says what was lost — durability — rather than dying silently.
  const MAX_RESTARTS = 5;
  let restarts = 0;

  const startWorker = () => {
    run("worker", 33, PYTHON, ["-m", "arq", "engine.worker.WorkerSettings"], {
      cwd: join(ROOT, "apps/engine"),
      onExit: (code, signal, label) => {
        if (restarts >= MAX_RESTARTS) {
          console.log(
            `\n${label} stopped for good after ${MAX_RESTARTS} restarts.\n` +
              `${label} renders still run, but inside the engine — they will not ` +
              `survive a restart.\n`,
          );
          return;
        }
        restarts += 1;
        const wait = Math.min(1000 * 2 ** (restarts - 1), 15_000);
        console.log(
          `${label} exited (${signal ?? `code ${code}`}); ` +
            `restarting in ${wait / 1000}s (${restarts}/${MAX_RESTARTS})`,
        );
        // Unref'd: a pending restart must never be the reason Ctrl-C hangs.
        setTimeout(() => {
          if (!shuttingDown) startWorker();
        }, wait).unref?.();
      },
    });
  };

  startWorker();
}

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

  // Two guards, and both are load-bearing, because the callback is `async` while
  // `setInterval` is not: the timer keeps firing during an `await`, so ticks
  // overlap. A probe waits up to 2s and the interval is 700ms, and the very first
  // request to a cold `next dev` is the slow one — it compiles the page — so three
  // or four ticks would be in flight together, all resolve within a moment of each
  // other, and each open a window. `clearInterval` cannot fix that: by the time
  // the first tick resumes after its `await`, the others are already past it.
  //
  // `probing` stops the pile-up; `opened` makes opening happen once even if one
  // forms anyway. Observed as Studio opening three times from one double-click.
  let probing = false;
  let opened = false;

  const poll = setInterval(async () => {
    if (shuttingDown) return clearInterval(poll);
    if (probing || opened) return;

    if (Date.now() - started > deadline) {
      clearInterval(poll);
      console.log(
        `\n  Still starting. Open ${url} yourself once it is ready.\n`,
      );
      return;
    }
    // Any answer means the server is listening — a 404, a 500 or the 307 to
    // /welcome are all something a browser can usefully show, and all beat
    // waiting longer. 0 means it did not answer; the interval is the retry.
    probing = true;
    let status = 0;
    try {
      status = await probe(WEB_PORT);
    } finally {
      probing = false;
    }

    if (status > 0 && !opened) {
      opened = true;
      clearInterval(poll);
      console.log(`\n  Opening ${url}\n`);
      showStudio(url);
    }
  }, 700);
  // Do not let the poll itself hold the process open past a shutdown.
  poll.unref?.();
}
