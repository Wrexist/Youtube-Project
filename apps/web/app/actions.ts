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

import {
  EngineError,
  cancelJob,
  createJob,
  publishJob,
  editStage,
  rerunStage,
  resetRoutes,
  setAllRoutes,
  setRoute,
} from "@/lib/engine";
import type { JobRequest, PublishRequest } from "@studio/contracts";

export interface ActionResult<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
  /** Publish blockers, each with a readable reason. Shown as a list, never summarised. */
  blockers?: { code: string; message: string }[];
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
      const blockers = (detail as { blockers?: { code: string; message: string }[] })?.blockers;
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

export async function routeTask(
  task: string,
  model: string,
): Promise<ActionResult> {
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
