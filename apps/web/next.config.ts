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
};

export default config;
