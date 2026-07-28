/**
 * The engine client.
 *
 * Two rules shape this file.
 *
 * **The app must build and render with no engine running.** That was a deliberate
 * product decision — a design you cannot look at is a design you cannot judge —
 * and wiring real data must not cost it. So every read goes through `get()`, which
 * returns `null` rather than throwing when the engine is unreachable, and each
 * caller falls back to `lib/demo.ts`. `isLive()` tells a screen which it got, so
 * the UI can say "demo data" instead of quietly implying the numbers are real.
 *
 * **Types come from `@studio/contracts`**, generated from the engine's OpenAPI
 * document. Nothing here declares a response shape.
 *
 * Mutations are the exception: a failed write must surface, so `post()` throws.
 * Silently swallowing a publish that did not happen is far worse than an error.
 */

import type {
  Calendar,
  CalendarSlots,
  Channels,
  Insights,
  JobCreated,
  JobRequest,
  JobStatus,
  JobSummary,
  Models,
  PublishRequest,
  Quota,
  WorkflowGraph,
} from "@studio/contracts";

/**
 * Where the engine is, from wherever this code happens to be running.
 *
 * Two variables because there are two answers. `NEXT_PUBLIC_ENGINE_URL` is baked
 * into the browser bundle and has to be an address the *browser* can reach — the
 * published port on the host. Server Components and Server Actions run inside the
 * web container, where that address is the web container itself.
 *
 * With only the public variable, every server-side read hit localhost:8080 inside
 * the web container, found nothing, and fell back to demo data — so under
 * `docker compose --profile full` the app looked like it was working while nothing
 * had ever reached the engine. `ENGINE_URL` is the in-network address; it is only
 * consulted on the server, so it never ends up in the bundle.
 */
const BASE =
  typeof window === "undefined"
    ? (process.env.ENGINE_URL ??
      process.env.NEXT_PUBLIC_ENGINE_URL ??
      "http://localhost:8080")
    : (process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8080");

/** How long a Server Component waits before falling back to demo data. */
const TIMEOUT_MS = 2500;

export class EngineError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

/**
 * Read from the engine, or `null` if it is not there.
 *
 * `cache: "no-store"` because every one of these is live operational state —
 * quota remaining, jobs running. A cached quota figure is a wrong quota figure,
 * and CLAUDE.md forbids caching anything carrying OAuth state.
 */
export async function get<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${BASE}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers: { accept: "application/json" },
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    // Unreachable, timed out, or serving nonsense. The caller falls back.
    return null;
  }
}

/** Write to the engine. Throws — a mutation that silently failed is a lie. */
export async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new EngineError(
      describe(payload) ?? `${path} failed with ${response.status}`,
      response.status,
      payload,
    );
  }
  return payload as T;
}

/** FastAPI's `detail` is a string, or a list of validation errors, or an object. */
function describe(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    return String((detail as { detail: unknown }).detail);
  }
  if (Array.isArray(detail)) {
    return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join("; ");
  }
  return null;
}

/** Is the engine answering? Used to label a screen as live or demo. */
export async function isLive(): Promise<boolean> {
  return (await get<unknown>("/health")) !== null;
}

// ── reads ───────────────────────────────────────────────────────────────────

export const getQuota = () => get<Quota>("/v1/quota");
export const getCalendar = () => get<Calendar>("/v1/calendar");
export const getSlots = (days = 14) => get<CalendarSlots>(`/v1/calendar/slots?days=${days}`);
export const getChannels = () => get<Channels>("/v1/channels");
export const getInsights = () => get<Insights>("/v1/insights");
export const getModels = () => get<Models>("/v1/models");
export const getWorkflow = (name: string) => get<WorkflowGraph>(`/v1/workflows/${name}`);
export const getJob = (id: string) => get<Record<string, unknown>>(`/v1/jobs/${id}`);

/**
 * Every job, newest first. Optionally filtered to one status.
 *
 * The Queue and the Library are both views over this. Before the endpoint existed
 * they rendered `lib/demo.ts` unconditionally, so generating a video changed
 * neither screen — the two screens someone looks at immediately after pressing
 * Generate.
 */
export const getJobs = (status?: JobStatus) =>
  get<JobSummary[]>(`/v1/jobs${status ? `?status=${status}` : ""}`);

// ── writes ──────────────────────────────────────────────────────────────────

export const createJob = (body: JobRequest) => post<JobCreated>("/v1/jobs", body);
export const cancelJob = (id: string) => post<unknown>(`/v1/jobs/${id}/cancel`);

/**
 * The approval gate. Refuses on quality blockers, a missing channel, or exhausted
 * quota — the thrown message carries the reasons, which the UI must show rather
 * than reduce to "failed".
 */
export const publishJob = (id: string, body: Partial<PublishRequest> = {}) =>
  post<unknown>(`/v1/jobs/${id}/publish`, body);

/**
 * A browser-reachable URL for a generated artifact — a thumbnail, a render.
 *
 * Must go through `BASE`: a bare `/v1/files/...` resolves against the web app's own
 * origin (:3000), where nothing serves it. The engine owns these files.
 */
export const fileUrl = (key: string) => `${BASE}/v1/files/${key}`;

/** Where a browser subscribes for live progress. */
export const eventsUrl = (id: string) => `${BASE}/v1/jobs/${id}/events`;
