import type { NextConfig } from "next";

/**
 * Deliberately almost empty.
 *
 * There used to be an `env` block here mapping `ENGINE_URL` to
 * `NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8080"`. Next folds `env` entries
 * into the bundle at build time, so `process.env.ENGINE_URL` was a frozen string
 * — and it froze to `localhost:8080`, which inside the web container is the web
 * container. That silently defeated the runtime `ENGINE_URL: http://engine:8080`
 * that docker-compose sets, so under `--profile full` every server-side read
 * still failed and fell back to demo data.
 *
 * `lib/engine.ts` reads both variables at runtime, picking the in-network one on
 * the server and the browser-facing one in the client. Nothing needs to be
 * declared here for that to work; declaring it is what broke it.
 */
const config: NextConfig = {
  reactStrictMode: true,

  /**
   * `next dev` prints every Server Action call *with its arguments*, and
   * `saveCredentials` takes API keys as its argument. Left on, pasting a key
   * into Setup or the first-run Welcome flow writes
   *
   *   └─ ƒ saveCredentials({"ANTHROPIC_API_KEY":"sk-ant-…"}) in 23ms app/actions.ts
   *
   * to the launcher's stdout — and from there into a scrollback buffer, a
   * `npm start > log` redirect, or a screenshot pasted into an issue. That is
   * exactly the "secrets are never logged" rule in CLAUDE.md, broken by the dev
   * server rather than by our code, which is why nothing in `actions.ts` or the
   * engine could have prevented it.
   *
   * `serverFunctions: false` rather than `incomingRequests: false`: both
   * suppress the line (the action log is nested inside the incoming-request log
   * in next's `server/dev/log-requests.js`), but the incoming-request switch
   * also takes away every `GET /setup 200 in 808ms` line, which is the main
   * reason to watch that stream at all. This one turns off only the argument
   * dump, for every action — not just the credential one, because the next
   * action to take a secret should not have to remember this file exists.
   *
   * Production is unaffected either way: the logging is `NODE_ENV=development`
   * only, so `next start` and the Dockerfile never printed it.
   */
  logging: { serverFunctions: false },
};

export default config;
