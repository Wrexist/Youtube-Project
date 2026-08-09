/**
 * The engine's contract, as TypeScript.
 *
 * `schema.d.ts` is generated from the engine's OpenAPI document and is a faithful
 * but awkward shape — `paths["/v1/jobs"]["post"]["responses"][202]…` is not
 * something a screen should ever spell out. This module is the thin, hand-written
 * *naming* layer over it: every type below is derived from the generated schema,
 * never redeclared, so a change in the engine surfaces here as a type error
 * rather than as silent drift.
 *
 * CLAUDE.md: "Never hand-write a type that mirrors an API response." Aliasing one
 * is not mirroring it — deleting the alias would not change what the API returns.
 */

import type { components, paths } from "./schema.js";

export type { components, paths };

// ── request bodies ──────────────────────────────────────────────────────────

export type JobRequest = components["schemas"]["JobRequest"];
export type PublishRequest = components["schemas"]["PublishRequest"];
export type EditRequest = components["schemas"]["EditRequest"];
export type ScheduleRequest = components["schemas"]["ScheduleRequest"];
export type AutoScheduleRequest = components["schemas"]["AutoScheduleRequest"];

/** Pulls the 2xx JSON body out of an operation, whichever success code it uses. */
type Ok<T> = T extends { responses: infer R }
  ? R extends { 200: { content: { "application/json": infer B } } }
    ? B
    : R extends { 202: { content: { "application/json": infer B } } }
      ? B
      : never
  : never;

// ── responses ───────────────────────────────────────────────────────────────

export type Health = Ok<paths["/health"]["get"]>;
export type WorkflowGraph = Ok<paths["/v1/workflows/{name}"]["get"]>;
export type JobCreated = Ok<paths["/v1/jobs"]["post"]>;
export type JobDetail = Ok<paths["/v1/jobs/{job_id}"]["get"]>;
export type Quota = Ok<paths["/v1/quota"]["get"]>;
export type Calendar = Ok<paths["/v1/calendar"]["get"]>;
export type CalendarSlots = Ok<paths["/v1/calendar/slots"]["get"]>;
export type Channels = Ok<paths["/v1/channels"]["get"]>;
export type Insights = Ok<paths["/v1/insights"]["get"]>;
/** Progress toward the Partner Programme — the number the product is aimed at. */
export type Monetisation = Ok<paths["/v1/analytics/monetisation"]["get"]>;
/** Stretches of a long-form video worth cutting into a Short. */
export type Shorts = Ok<paths["/v1/analytics/shorts/{video_id}"]["get"]>;
export type ShortCut = Shorts["candidates"][number];
export type Models = Ok<paths["/v1/models"]["get"]>;
/** One row of `GET /v1/jobs` — what the Queue and Library list. */
export type JobSummary = components["schemas"]["JobSummary"];
export type Jobs = Ok<paths["/v1/jobs"]["get"]>;
export type ChannelLimits = Ok<paths["/v1/channels/limits"]["get"]>;

/** What the Setup screen reads. Reports whether each credential is set, never
 *  what it is — the engine has no endpoint that returns a key's value. */
export type SetupStatus = Ok<paths["/v1/setup"]["get"]>;
export type CredentialStatus = components["schemas"]["CredentialStatus"];

/** `scripts/doctor.py` as data — the same checks, so the terminal and the screen
 *  cannot disagree about whether this install works. */
export type Diagnostics = Ok<paths["/v1/setup/diagnostics"]["get"]>;

/** The Create screen's Improve button, and the ideas it suggests. */
export type Brief = Ok<paths["/v1/brief"]["post"]>;
export type Suggestions = Ok<paths["/v1/ideas/suggestions"]["get"]>;
export type Suggestion = Suggestions["suggestions"][number];

/** The thumbnail panel: the variants, and a sharpened instruction. */
export type Thumbnails = Ok<paths["/v1/jobs/{job_id}/thumbnails"]["get"]>;
export type ThumbnailVariant = Thumbnails["variants"][number];
export type Sharpened = Ok<paths["/v1/jobs/{job_id}/thumbnails/sharpen"]["post"]>;
export type DiagnosticCheck = components["schemas"]["DiagnosticCheck"];

// ── things FastAPI types as `object` ────────────────────────────────────────
//
// Every one of these endpoints is declared `-> dict` in the engine, so the schema
// says `object` and nothing more. Narrowing them here would be exactly the
// hand-written mirror the rule forbids — the honest fix is to add response models
// to the engine, at which point these become real generated types. Until then a
// screen that needs a field asserts it at the edge and the looseness is visible.

/** Stage status as the engine serialises it. Mirrors `StageStatus` in base.py. */
export type StageStatus = "pending" | "running" | "done" | "stale" | "failed" | "skipped";

/** Job lifecycle. `interrupted` means the process died mid-run — see repository.py. */
export type JobStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted";

// ── SSE ─────────────────────────────────────────────────────────────────────
//
// Server-sent events are not in the OpenAPI document — the schema describes the
// endpoint, not the event stream it emits. These names come from `emit()` calls
// in main.py and base.py; the union is the closest thing to a contract there is.

export type JobEventType =
  | "workflow.started"
  | "workflow.completed"
  | "workflow.failed"
  | "stage.started"
  | "stage.progress"
  | "stage.completed"
  | "stage.failed"
  | "stage.retrying"
  | "stage.skipped"
  | "stage.replayed"
  // Terminal. The engine sends this immediately before closing the stream, because
  // a stream that just ends is a *reconnect* signal to EventSource — a finished job
  // replayed its whole log every few seconds without it.
  | "stream.closed";

export interface JobEvent {
  type: JobEventType;
  job_id: string;
  stage?: string;
  title?: string;
  message?: string;
  fraction?: number | null;
  error?: string;
  attempt?: number;
  cost_usd?: number;
  /** Only on `stage.completed`: `summarize(output.value)` — the one line the
   *  finished row collapses to. */
  summary?: string;
  /** Only on `stage.completed`: wall time for the stage, in milliseconds. */
  elapsed_ms?: number;
  /** Only on `stream.closed`: the job's final status. */
  status?: JobStatus;
}
