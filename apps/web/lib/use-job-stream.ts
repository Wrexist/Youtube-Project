"use client";

/**
 * Live job progress over SSE.
 *
 * This is the one place TanStack Query is explicitly *not* used (CLAUDE.md scopes
 * it to SSE-backed views, and an EventSource is already a subscription — wrapping
 * it in a cache would add a second source of truth for the same stream).
 *
 * The engine replays the whole event log to a new subscriber before streaming
 * live ones, so a reload mid-render redraws the full pipeline rather than
 * resuming from a blank screen. That also means this hook does not need an
 * initial fetch: connecting *is* the fetch.
 *
 * Reducing events into stage state rather than re-fetching the job on every tick
 * is deliberate — a long render emits hundreds of progress events, and a GET per
 * event would be pure waste.
 */

import { useCallback, useEffect, useReducer, useRef } from "react";

import type { JobEvent, JobStatus } from "@studio/contracts";

import type { Stage } from "./types";

export interface JobStream {
  stages: Stage[];
  status: JobStatus | "connecting";
  /** Set when the stream itself broke, not when a stage failed. */
  error: string | null;
  cost_usd: number;
}

export interface JobStreamHandle extends JobStream {
  /**
   * Say "this job is running again" before the stream can.
   *
   * Only the caller knows a re-run was just accepted; the rebuilt EventSource
   * takes a round-trip to say so, and everything gated on a terminal status —
   * Publish, "Re-run from here" — must go back to its running state for that
   * whole window, not just after the first frame lands.
   */
  markRunning: () => void;
}

type Action =
  | { type: "event"; event: JobEvent }
  | { type: "error"; message: string }
  /**
   * The job is running again for a reason the stream has not reported yet — a
   * re-run the client just asked for. Without it the hook keeps the terminal
   * status from the *previous* run until the rebuilt EventSource delivers its
   * first frame, and for that window Publish stays enabled over a job whose
   * stages are being regenerated underneath it (CLAUDE.md #3).
   */
  | { type: "running" };

/**
 * The whole of this hook's behaviour, as a pure function.
 *
 * Exported so it can be exercised without standing up an EventSource — every
 * interesting case here (a failure that only ever arrives as `workflow.failed`, a
 * `stage.progress` frame with no stage on it) is a fold over a captured event log.
 */
export function reduceJobStream(state: JobStream, action: Action): JobStream {
  if (action.type === "error") return { ...state, error: action.message };
  if (action.type === "running") return { ...state, status: "running", error: null };

  const event = action.event;
  const stages = [...state.stages];

  // `stage.progress` is the one frame that carries no `stage` — `WorkflowContext.progress`
  // in base.py sends only `message` and `fraction`. Attributing it to whichever stage
  // is currently running is what makes it land at all; without this every "downloading
  // 4/12" and every render percentage was dropped on the floor, and a twelve-minute
  // render showed nothing but "working…" the whole way through.
  const target =
    event.stage ??
    (event.type === "stage.progress"
      ? stages.find((s) => s.status === "running")?.name
      : undefined);
  const index = target ? stages.findIndex((s) => s.name === target) : -1;

  const patch = (changes: Partial<Stage>) => {
    if (index === -1) {
      if (!target) return;
      // A stage the client has not seen — the engine added one, or this is a
      // resumed job whose graph the page never loaded. Append rather than drop it.
      stages.push({
        name: target,
        title: event.title ?? target,
        status: "pending",
        summary: null,
        cost_usd: 0,
        elapsed_ms: 0,
        error: null,
        editable: false,
        ...changes,
      });
    } else {
      stages[index] = { ...stages[index], ...changes };
    }
  };

  switch (event.type) {
    case "workflow.started":
      return { ...state, status: "running", error: null, stages };
    case "stage.started":
      patch({ status: "running", error: null });
      break;
    case "stage.progress":
      patch({ summary: event.message ?? null });
      break;
    case "stage.completed":
      // `summary` and `elapsed_ms` are both on the frame (base.py's `_run_stage`)
      // and were both being dropped, so a finished row showed a blank line and no
      // duration — the one-line collapse the Create screen is built around never
      // had anything in it for a live job.
      //
      // Spread rather than `?? 0`: both fields are optional on `JobEvent`, and a
      // frame that omits one would otherwise reset whatever an earlier frame — a
      // retry, say — had already recorded, so a stage that cost something would
      // finish reading $0.00. Patch what the frame actually carries.
      patch({
        status: "done",
        summary: event.summary ?? null,
        ...(event.cost_usd !== undefined ? { cost_usd: event.cost_usd } : {}),
        ...(event.elapsed_ms !== undefined ? { elapsed_ms: event.elapsed_ms } : {}),
      });
      break;
    case "stage.replayed":
      // Already done in a previous run; the engine is skipping it, not redoing it.
      patch({ status: "done" });
      break;
    case "stage.skipped":
      patch({ status: "skipped" });
      break;
    case "stage.failed":
      patch({ status: "failed", error: event.error ?? "failed" });
      break;
    case "stage.retrying":
      patch({
        status: "running",
        summary: `retrying (attempt ${event.attempt ?? 1}) — ${event.error ?? ""}`,
      });
      break;
    case "workflow.completed":
      return { ...state, status: "completed", cost_usd: event.cost_usd ?? state.cost_usd, stages };
    case "workflow.failed":
      // The engine emits no `stage.failed` when a stage exhausts its retries —
      // `_run_stage` records the error on the state and returns, and `workflow.failed`,
      // which carries both the stage name and the message, is the only frame that
      // follows. Without patching it here the row pulsed "working…" under a job that
      // had already died, and stayed un-expandable (`interactive` is done||failed),
      // so neither the error text nor "Re-run from here" was ever reachable.
      patch({ status: "failed", error: event.error ?? "failed" });
      return { ...state, status: "failed", stages };
    case "stream.closed":
      // The terminal frame carries the job's final status, and it is the only
      // event that does so for a job that ended any way other than completing or
      // failing. A cancelled or interrupted job emits no `workflow.*` terminal
      // event at all, so without this the pipeline sat at "running" — spinner and
      // all — for a job that had already stopped.
      //
      // Guarded: a job that *did* complete or fail has already set its status from
      // the richer event, which also carried the final cost. Don't overwrite it.
      if (state.status === "running" || state.status === "connecting") {
        return { ...state, status: event.status ?? state.status, stages };
      }
      break;
  }

  return { ...state, stages };
}

/**
 * `attempt` exists so a dead stream can be reopened.
 *
 * The effect below depends on `[jobId]` alone, so once EventSource gave up there
 * was no way back short of a full reload — the pipeline froze on its skeleton with
 * Publish permanently disabled. Bumping this re-runs the effect and rebuilds the
 * connection; the engine replays the whole event log on connect, so nothing is
 * lost by doing so.
 */
export function useJobStream(
  jobId: string | null,
  initial: Stage[] = [],
  attempt = 0,
): JobStreamHandle {
  const [state, dispatch] = useReducer(reduceJobStream, {
    stages: initial,
    status: "connecting",
    error: null,
    cost_usd: 0,
  });

  // Kept in a ref so re-renders do not tear down and rebuild the connection —
  // reconnecting would replay the whole log and restart every animation.
  const url = useRef<string | null>(null);

  useEffect(() => {
    if (!jobId) return;

    let source: EventSource | null = null;
    let cancelled = false;

    // Imported lazily: `eventsUrl` reads a public env var, and this file is only
    // ever reached in the browser.
    import("./engine").then(({ eventsUrl }) => {
      if (cancelled) return;
      url.current = eventsUrl(jobId);
      source = new EventSource(url.current);

      const handle = (e: MessageEvent) => {
        try {
          dispatch({ type: "event", event: JSON.parse(e.data) as JobEvent });
        } catch {
          // A malformed frame is not worth killing the stream over.
        }
      };

      // The engine names each event, so there is no default-typed message to
      // listen for — every type has to be registered explicitly.
      const types: JobEvent["type"][] = [
        "workflow.started",
        "workflow.completed",
        "workflow.failed",
        "stage.started",
        "stage.progress",
        "stage.completed",
        "stage.failed",
        "stage.retrying",
        "stage.skipped",
        "stage.replayed",
      ];
      for (const type of types) source.addEventListener(type, handle);

      // The engine's terminal frame. Without closing here, EventSource treats the
      // server ending the stream as a dropped connection, reconnects a few seconds
      // later, and replays the entire log — re-dispatching workflow.started and
      // flipping the Publish button's state on a loop that never stops.
      source.addEventListener("stream.closed", (e: MessageEvent) => {
        handle(e);
        source?.close();
      });

      source.onerror = () => {
        // EventSource reconnects on its own; only say something if it gave up.
        if (source?.readyState === EventSource.CLOSED) {
          dispatch({ type: "error", message: "lost connection to the engine" });
        }
      };
    });

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [jobId, attempt]);

  const markRunning = useCallback(() => dispatch({ type: "running" }), []);

  return { ...state, markRunning };
}
