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

/** How every video sounds and looks: narrator, music, captions, motion. */
export type Style = Ok<paths["/v1/style"]["get"]>;
export type StyleOptions = Style["options"];
export type StyleVoice = StyleOptions["voices"][number];
/** The partial the Style screen sends — only what changed. */
export type StyleUpdate = components["schemas"]["StyleUpdate"];

/** The connected channel's playlists, so a publish can pick one. */
export type Playlist = Ok<paths["/v1/channels/playlists"]["get"]>[number];

/**
 * Repurpose: discovered clips, and the two gates between one and a published video.
 *
 * `ClipGrant` answers "may we use this" and `Report` answers "is it original
 * enough" — separate types because they are separate questions with separate
 * failure modes, and blending them in the UI would hide the common state of
 * "cleared to use, not yet transformative enough".
 */
export type Clips = Ok<paths["/v1/repurpose/clips"]["get"]>;
export type Clip = Clips["clips"][number];
export type ClipGrant = components["schemas"]["GrantOut"];
export type ClipGrantRequest = components["schemas"]["GrantIn"];
export type ClipLane = components["schemas"]["Lane"];
export type RightsProblem = components["schemas"]["ProblemOut"];
/** The originality report — both verdicts, never blended into one score. */
export type OriginalityReport = Ok<paths["/v1/repurpose/evaluate"]["post"]>;
export type TimelineRequest = components["schemas"]["TimelineIn"];
/** Whether TikTok is configured, and whether an account has signed in. Two
 *  separate questions with two different fixes — see `api/repurpose.py`. */
export type TikTokStatus = Ok<paths["/v1/repurpose/auth/tiktok/status"]["get"]>;
/** The result of a sweep. Carries `configured` and `connected` so an empty result
 *  can say *which* kind of empty it is — the screen has three fixes to offer. */
export type Discovered = Ok<paths["/v1/repurpose/discover"]["post"]>;

/** The standing list of researched ideas, and one of its entries. */
export type Backlog = Ok<paths["/v1/ideas/backlog"]["get"]>;
export type BacklogIdea = Backlog["ideas"][number];

/** What the channel has cost, per day and in total. */
export type Spend = Ok<paths["/v1/spend"]["get"]>;

/** Last Monday's review. `null` until one has been stored. */
export type Review = components["schemas"]["ReviewPayload"];

/** A standing series config, and the weekly plan the run planner derives from it. */
export type Series = components["schemas"]["SeriesOut"];
export type SeriesRequest = components["schemas"]["SeriesIn"];
export type SeriesPatch = components["schemas"]["SeriesPatch"];
export type SeriesPlan = Ok<paths["/v1/series/{series_id}/plan"]["get"]>;
export type SeriesBlocker = SeriesPlan["blocked"][number];

/** A channel-launch design: the async job the New channel screen polls. */
export type Launch = Ok<paths["/v1/channels/launch/{launch_id}"]["get"]>;
export type LaunchRequest = components["schemas"]["LaunchRequest"];
export type LaunchSummary = Ok<paths["/v1/channels/launches"]["get"]>[number];
// Qualified: both `api/channels.py` and `api/publishing.py` define an
// `ApplyRequest`, so the generator disambiguates by module path.
export type LaunchApplyRequest = components["schemas"]["engine__api__channels__ApplyRequest"];

/** Rendered but unpublished — what the Calendar tray drags onto slots.
 *  `PendingVideoOut`, because plain `PendingVideo` is the *request* shape
 *  `POST /v1/calendar/auto` takes. */
export type PendingVideos = Ok<paths["/v1/calendar/pending"]["get"]>;
export type PendingVideoOut = components["schemas"]["PendingVideoOut"];

/** Every published video with metrics and provenance — the Analytics table. */
export type AnalyticsVideos = Ok<paths["/v1/analytics/videos"]["get"]>;
export type AnalyticsVideo = AnalyticsVideos[number];

/** The daily channel curve behind the Analytics tiles. */
export type AnalyticsDaily = Ok<paths["/v1/analytics/daily"]["get"]>;
/** One video's retention curve mapped onto its script beats. */
export type Retention = Ok<paths["/v1/analytics/retention/{video_id}"]["get"]>;
export type ReviewChange = Review["changes"][number];

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
