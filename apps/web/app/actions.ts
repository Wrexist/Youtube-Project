"use server";

/**
 * Server Actions — the mutation half of the web/engine seam.
 *
 * These run on the server, so the engine never has to be reachable from the
 * browser for a write. Reads are plain Server Component fetches in `lib/engine`;
 * the split is CLAUDE.md's ("Data fetching in Server Components; mutations via
 * Server Actions").
 *
 * Every action returns a result object rather than throwing. A thrown Server
 * Action becomes an opaque "an error occurred" in production builds, and the
 * whole point of the approval gate's blocker list is that the user can read it.
 */

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";

import {
  EngineError,
  beginYouTubeAuth,
  cancelJob,
  createJob,
  refineBrief,
  getDiagnostics,
  getIdeaSuggestions,
  getThumbnails,
  regenerateThumbnail,
  sharpenThumbnailInstruction,
  getDiagnosticReport,
  getPlaylists,
  isLive,
  publishJob,
  saveKeys,
  saveStyle,
  applySchedulePlan,
  editStage,
  rerunStage,
  scheduleVideo,
  unscheduleVideo,
  recommendRoutes,
  resetRoutes,
  setAllRoutes,
  setRoute,
} from "@/lib/engine";
import { ONBOARDED_COOKIE, ONBOARDED_MAX_AGE } from "@/lib/onboarding";
import type {
  Brief,
  Diagnostics,
  JobRequest,
  Playlist,
  PublishRequest,
  Sharpened,
  Style,
  StyleUpdate,
  Suggestion,
  Thumbnails,
} from "@studio/contracts";

export interface ActionResult<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
  /** Publish blockers, each with a readable reason. Shown as a list, never summarised. */
  blockers?: { code: string; message: string }[];
}

/**
 * Sharpen what the creator typed into a topic the pipeline can research.
 *
 * The Create screen's one AI affordance before Generate. Everything downstream
 * inherits this string — keyword grounding seeds autocomplete with it, research
 * searches for it, the angle and hook stages are handed it as the premise — so a
 * vague one does not fail loudly, it produces a competent video about nothing in
 * particular.
 */
export async function improveTopic(
  rough: string,
  format: "short" | "long",
): Promise<ActionResult<{ topic: string; format: string; why: string }>> {
  try {
    const brief = await refineBrief(rough, format);
    return { ok: true, data: brief };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

/**
 * Ideas worth making next. Empty on a first run — there is nothing to be
 * adjacent to yet, and inventing a niche for someone who has not chosen one is
 * worse than showing nothing.
 */
export async function ideaSuggestions(): Promise<ActionResult<Suggestion[]>> {
  const data = await getIdeaSuggestions(4);
  if (!data) return { ok: false, error: "the engine did not answer" };
  return { ok: true, data: data.suggestions };
}

// ── thumbnails ──────────────────────────────────────────────────────────────
//
// The thumbnail is the only artifact a viewer sees before deciding whether to
// watch, and the pipeline used to report it as "3 items".

export async function loadThumbnails(jobId: string): Promise<ActionResult<Thumbnails>> {
  const data = await getThumbnails(jobId);
  if (!data) return { ok: false, error: "the engine did not answer" };
  return { ok: true, data };
}

/**
 * Change how videos sound and look.
 *
 * The engine answers with the whole style *in force*, not an acknowledgement, so
 * the screen renders what actually took effect. That distinction is load-bearing
 * here: an already-exported `STUDIO_*` variable outranks the dotenv, so a save can
 * legitimately succeed on disk and change nothing, and only the engine knows.
 */
export async function updateStyle(changes: StyleUpdate): Promise<ActionResult<Style>> {
  try {
    return { ok: true, data: await saveStyle(changes) };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

export async function loadPlaylists(): Promise<ActionResult<Playlist[]>> {
  const data = await getPlaylists();
  // `null` is the engine being unreachable. An empty array is a connected channel
  // with no playlists, or no channel at all — both of which the picker renders as
  // "nothing to add this to", which is true and not an error.
  if (!data) return { ok: false, error: "the engine did not answer" };
  return { ok: true, data };
}

export async function remakeThumbnail(
  jobId: string,
  instruction: string,
  baseIndex: number,
): Promise<ActionResult<Thumbnails>> {
  try {
    return { ok: true, data: await regenerateThumbnail(jobId, instruction, baseIndex) };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

export async function sharpenInstruction(
  jobId: string,
  instruction: string,
): Promise<ActionResult<Sharpened>> {
  try {
    return { ok: true, data: await sharpenThumbnailInstruction(jobId, instruction) };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

export async function startJob(input: {
  topic: string;
  format: "short" | "long";
}): Promise<ActionResult<{ job_id: string }>> {
  const body: JobRequest = {
    topic: input.topic,
    format: input.format,
    // The engine validates this too; sending the matching aspect keeps the two
    // from disagreeing about what "long" means.
    aspect: input.format === "long" ? "16:9" : "9:16",
    workflow: "video",
    voice: null,
    target_seconds: null,
  };

  try {
    const created = await createJob(body);
    return { ok: true, data: created as { job_id: string } };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

export async function stopJob(jobId: string): Promise<ActionResult> {
  try {
    return { ok: true, data: await cancelJob(jobId) };
  } catch (error) {
    return { ok: false, error: message(error) };
  }
}

/**
 * The approval gate.
 *
 * A 409 here is the normal, expected outcome for a video that is not ready — the
 * blockers are the payload, not an error to flatten. They are pulled out so the
 * UI can list them; reducing "no thumbnail, no sources" to "publish failed" would
 * defeat the checklist entirely.
 */
export async function publish(
  jobId: string,
  choices: Partial<PublishRequest> = {},
): Promise<ActionResult> {
  try {
    return { ok: true, data: await publishJob(jobId, choices) };
  } catch (error) {
    if (error instanceof EngineError && error.status === 409) {
      const detail = (error.detail as { detail?: { blockers?: unknown } })?.detail;
      const blockers = (detail as { blockers?: { code: string; message: string }[] })
        ?.blockers;
      if (blockers?.length) {
        return { ok: false, error: "This video is not ready to publish", blockers };
      }
    }
    return { ok: false, error: message(error) };
  }
}

function message(error: unknown): string {
  if (error instanceof EngineError) return error.message;
  if (error instanceof Error && error.message.includes("fetch failed")) {
    return "The engine is not running. Start it on :8080, or see README.md.";
  }
  return error instanceof Error ? error.message : String(error);
}

// ── model routing ───────────────────────────────────────────────────────────
//
// The Models screen used to keep every change in `useState` and send nothing, so
// a routing choice survived until the next reload and the header quoted a monthly
// cost for a configuration that was never in force.

export async function routeTask(task: string, model: string): Promise<ActionResult> {
  try {
    await setRoute(task, model);
    revalidatePath("/models");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — the routing change was not saved.",
    };
  }
}

export async function routeEverything(model: string): Promise<ActionResult> {
  try {
    await setAllRoutes(model);
    revalidatePath("/models");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was changed.",
    };
  }
}

export async function applyRecommendedRoutes(): Promise<ActionResult> {
  try {
    await recommendRoutes();
    revalidatePath("/models");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was changed.",
    };
  }
}

export async function restoreDefaultRoutes(): Promise<ActionResult> {
  try {
    await resetRoutes();
    revalidatePath("/models");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was changed.",
    };
  }
}

// ── stage re-runs ───────────────────────────────────────────────────────────

/**
 * Re-run a stage and everything downstream. The Create screen's "Re-run from
 * here", which until now was a `console.log`.
 *
 * The engine 409s while a job is running, so the caller gates the control on a
 * terminal status — but the check is also made there, because a UI gate is a
 * courtesy and not a guarantee.
 */
export async function rerunFrom(jobId: string, stage: string): Promise<ActionResult> {
  try {
    await rerunStage(jobId, stage);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was re-run.",
    };
  }
}

/** Replace a stage's value and regenerate what depended on it. */
export async function editStageValue(
  jobId: string,
  stage: string,
  value: unknown,
): Promise<ActionResult> {
  try {
    await editStage(jobId, stage, value);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — the edit was not applied.",
    };
  }
}

// ── scheduling ──────────────────────────────────────────────────────────────
//
// The Calendar stored bookings in a single `useState` and called nothing. A drag
// survived until the next reload; `applyPlan` announced "N videos scheduled"
// having sent nothing anywhere; and because `GET /v1/calendar`'s `scheduled` was
// never read, uploads the engine had already booked were invisible — so the same
// day could be double-booked against a ceiling the screen could not see.

export async function scheduleAt(videoId: string, at: string): Promise<ActionResult> {
  try {
    await scheduleVideo(videoId, at);
    revalidatePath("/calendar");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      // The engine's 409 carries the reason — over quota, too close to another
      // upload, in the past. It is shown verbatim rather than reduced to "failed".
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was scheduled.",
    };
  }
}

export async function unscheduleAt(videoId: string): Promise<ActionResult> {
  try {
    await unscheduleVideo(videoId);
    revalidatePath("/calendar");
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was unscheduled.",
    };
  }
}

export async function applyPlanToCalendar(
  assignments: { video_id: string; at: string }[],
): Promise<ActionResult<{ applied: number }>> {
  try {
    const data = await applySchedulePlan(assignments);
    revalidatePath("/calendar");
    return { ok: true, data };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — the plan was not applied.",
    };
  }
}

// ── setup ───────────────────────────────────────────────────────────────────

/**
 * Write credentials to the engine's `.env`.
 *
 * A Server Action rather than a browser fetch specifically because of what it
 * carries. The keys never leave the origin they were typed into: the browser
 * posts them to Next, Next posts them to the engine over the internal network,
 * and no API key is ever present in a URL, in client-side JavaScript, or in
 * anything CORS would let another origin observe.
 *
 * What that argument does *not* cover is the terminal: `next dev` logs every
 * Server Action call with its arguments, so under `npm start` this one used to
 * print the pasted key to the launcher's stdout — which is why
 * `next.config.ts` sets `logging: { serverFunctions: false }`.
 *
 * Only the names present in `values` are touched — the form sends the fields
 * someone typed into and nothing else, so saving a half-filled form cannot blank
 * the keys it never displayed.
 */
export async function saveCredentials(
  values: Record<string, string>,
): Promise<ActionResult<{ saved: string[] }>> {
  try {
    await saveKeys(values);
    revalidatePath("/setup");
    // Everything downstream reads settings: whether Create can start a job,
    // whether the Models screen can reach a provider, whether Publish is offered.
    revalidatePath("/", "layout");
    return { ok: true, data: { saved: Object.keys(values) } };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — nothing was saved.",
    };
  }
}

/**
 * Ask the engine for Google's consent URL.
 *
 * Returned to the client to navigate to, rather than redirected to from here.
 * A `redirect()` out of a Server Action is a server-side fetch of Google's
 * consent page, which authorises nobody; the person has to make that request
 * themselves, in their own browser, signed in as themselves.
 */
export async function connectYouTube(): Promise<ActionResult<{ url: string }>> {
  try {
    const data = await beginYouTubeAuth();
    return { ok: true, data };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof EngineError
          ? error.message
          : "Could not reach the engine — YouTube was not connected.",
    };
  }
}

/** Record that this browser has been through the welcome flow. */
export async function finishOnboarding(): Promise<ActionResult> {
  const jar = await cookies();
  jar.set(ONBOARDED_COOKIE, "1", {
    path: "/",
    maxAge: ONBOARDED_MAX_AGE,
    sameSite: "lax",
    httpOnly: true,
  });
  revalidatePath("/", "layout");
  return { ok: true };
}

/** Show the tour again. The Setup screen offers this; nothing else does. */
export async function replayOnboarding(): Promise<ActionResult> {
  const jar = await cookies();
  jar.delete(ONBOARDED_COOKIE);
  revalidatePath("/", "layout");
  return { ok: true };
}

/**
 * Re-run the health checks and hand back what they found.
 *
 * An action rather than a client fetch so the browser never needs to reach the
 * engine directly — the same reason every other mutation goes this way. `network`
 * turns on the keyword-grounding probe, which is slow enough that it only belongs
 * behind a button press.
 */
/**
 * The diagnostic report as text, for the Copy report button.
 *
 * A Server Action rather than a fetch from the browser, like everything else
 * here: the web app is the only thing that talks to the engine, and the engine
 * is unauthenticated (KNOWN-ISSUES §6) — reaching it from page JavaScript would
 * mean exposing its address to anything running in the tab.
 */
export async function fetchDiagnosticReport(): Promise<ActionResult<string>> {
  const text = await getDiagnosticReport();
  if (!text) {
    return { ok: false, error: "The engine did not answer. Is it still running?" };
  }
  return { ok: true, data: text };
}

export async function runDiagnostics(network = true): Promise<ActionResult<Diagnostics>> {
  const data = await getDiagnostics(network);
  if (!data) {
    return { ok: false, error: "The engine did not answer. Is it still running?" };
  }
  return { ok: true, data };
}

/**
 * Is the engine answering yet?
 *
 * An action rather than a read, because the caller is a client component that has
 * to ask repeatedly — the engine's state changes underneath a page that has
 * already rendered, which is exactly what a Server Component cannot express. The
 * browser still never talks to the engine directly.
 */
export async function engineReady(): Promise<ActionResult<{ live: boolean }>> {
  return { ok: true, data: { live: await isLive() } };
}
