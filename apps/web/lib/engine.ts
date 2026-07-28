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
  SetupStatus,
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

/**
 * How long a mutation waits before giving up.
 *
 * Much longer than a read, because a read has somewhere to fall back to and a
 * write does not — and because these endpoints do real work before answering
 * (opening a YouTube upload session, seeding a publish job). But not unbounded:
 * with no timeout at all, an engine that accepted the connection and then stalled
 * left the Server Action pending forever, and the button that triggered it spinning
 * forever with it. Every mutation on every screen shared that failure mode.
 */
const MUTATION_TIMEOUT_MS = 30_000;

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
  return send<T>("POST", path, body);
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      cache: "no-store",
      signal: AbortSignal.timeout(MUTATION_TIMEOUT_MS),
      headers: { "content-type": "application/json", accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // A timeout or a refused connection is not a status code, so it would
    // otherwise escape as a raw TypeError/DOMException and reach the UI as
    // "fetch failed" — which tells someone nothing about what to do next.
    const timedOut = cause instanceof Error && cause.name === "TimeoutError";
    throw new EngineError(
      timedOut
        ? `${path} timed out after ${MUTATION_TIMEOUT_MS / 1000}s. The engine accepted the request but never answered.`
        : `Could not reach the engine at ${BASE}. Is it running?`,
      0,
      cause,
    );
  }

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

/** Same contract as `post`, for the endpoints that are genuinely idempotent
 *  replacements — the model routing table is set, not appended to. */
export async function put<T>(path: string, body?: unknown): Promise<T> {
  return send<T>("PUT", path, body);
}

/** Unscheduling is a DELETE — the slot is removed, not set to nothing. */
export async function del<T>(path: string): Promise<T> {
  return send<T>("DELETE", path);
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

/** Route one task to one model. PUT, not POST — it replaces, it does not append. */
export const setRoute = (task: string, model: string) =>
  put<unknown>("/v1/models/route", { task, model });

/** The "run it all locally" button. */
export const setAllRoutes = (model: string) =>
  put<unknown>("/v1/models/route/all", { model });

export const resetRoutes = () => post<unknown>("/v1/models/route/reset");

/**
 * Re-run one stage and everything below it.
 *
 * Distinct from an edit, which replaces a value and keeps the stage done. The
 * Create screen's "Re-run from here" means this, and its caption already promised
 * it — the button called `console.log`.
 */
export const rerunStage = (id: string, stage: string) =>
  post<{ invalidated: string[]; status: string }>(`/v1/jobs/${id}/rerun`, { stage });

/** Replace a stage's value, keeping it done and regenerating what depended on it. */
export const editStage = (id: string, stage: string, value: unknown) =>
  post<{ invalidated: string[]; status: string }>(`/v1/jobs/${id}/edit`, { stage, value });

// ── scheduling ──────────────────────────────────────────────────────────────
//
// The Calendar's drag-and-drop kept everything in one `useState` and called none
// of these, so a schedule survived until the next reload and "N videos scheduled"
// was printed having sent nothing.

/** Book one video. 409s with a readable reason when the move breaks a rule. */
export const scheduleVideo = (videoId: string, at: string) =>
  post<unknown>("/v1/calendar/schedule", { video_id: videoId, at });

export const unscheduleVideo = (videoId: string) =>
  del<unknown>(`/v1/calendar/schedule/${videoId}`);

export const applySchedulePlan = (assignments: { video_id: string; at: string }[]) =>
  post<{ applied: number }>("/v1/calendar/auto/apply", { assignments });

/** Where a browser subscribes for live progress. */
export const eventsUrl = (id: string) => `${BASE}/v1/jobs/${id}/events`;

// ── setup ───────────────────────────────────────────────────────────────────
//
// The screen that turns a fresh clone into a working install. Everything here is
// deliberately value-free in one direction: `getSetup` never returns a key, and
// `saveKeys` never receives one it did not just take from a form field.

export const getSetup = () => get<SetupStatus>("/v1/setup");

/** Save credentials. Only the names passed are touched; absent means unchanged. */
export const saveKeys = (values: Record<string, string>) =>
  put<SetupStatus>("/v1/setup/keys", { values });

/**
 * Begin the YouTube OAuth round trip.
 *
 * Returns Google's consent URL rather than redirecting, because the redirect has
 * to happen in the browser: a Server Action following it would authorise the
 * server, not the person sitting in front of it.
 *
 * Goes through `send` rather than `get`, even though it is a GET: `get` swallows
 * every failure into `null` so a caller can fall back to demo data, and the whole
 * value of this call when it fails is the 409's message, which names the missing
 * variable. Falling back to nothing here would turn a fixable misconfiguration
 * into a button that does not work.
 */
export const beginYouTubeAuth = () => send<{ url: string }>("GET", "/v1/auth/google");
