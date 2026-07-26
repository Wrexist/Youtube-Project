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

import { useEffect, useReducer, useRef } from "react";

import type { JobEvent, JobStatus } from "@studio/contracts";

import type { Stage } from "./types";

export interface JobStream {
  stages: Stage[];
  status: JobStatus | "connecting";
  /** Set when the stream itself broke, not when a stage failed. */
  error: string | null;
  cost_usd: number;
}

type Action = { type: "event"; event: JobEvent } | { type: "error"; message: string };

function reduce(state: JobStream, action: Action): JobStream {
  if (action.type === "error") return { ...state, error: action.message };

  const event = action.event;
  const stages = [...state.stages];
  const index = event.stage ? stages.findIndex((s) => s.name === event.stage) : -1;

  const patch = (changes: Partial<Stage>) => {
    if (index === -1) {
      if (!event.stage) return;
      // A stage the client has not seen — the engine added one, or this is a
      // resumed job whose graph the page never loaded. Append rather than drop it.
      stages.push({
        name: event.stage,
        title: event.title ?? event.stage,
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
      patch({ status: "done", cost_usd: event.cost_usd ?? 0 });
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
      return { ...state, status: "failed", stages };
  }

  return { ...state, stages };
}

export function useJobStream(jobId: string | null, initial: Stage[] = []): JobStream {
  const [state, dispatch] = useReducer(reduce, {
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
  }, [jobId]);

  return state;
}
