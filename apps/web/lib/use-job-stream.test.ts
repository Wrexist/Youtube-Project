/**
 * The progress fold.
 *
 * `reduceJobStream` is exported precisely so this can be a pure test — every case
 * here is a sequence of frames the engine really sends, and none of them needs an
 * EventSource. The fraction path is worth pinning because it is invisible when it
 * breaks: the bar simply goes back to shimmering, which looks like a stage that has
 * not reported rather than like a bug.
 */

import { describe, expect, it } from "vitest";

import type { JobEvent } from "@studio/contracts";

import { reduceJobStream, type JobStream } from "./use-job-stream";
import type { Stage } from "./types";

function stage(name: string, over: Partial<Stage> = {}): Stage {
  return {
    name,
    title: name,
    status: "pending",
    summary: null,
    cost_usd: 0,
    elapsed_ms: 0,
    error: null,
    editable: false,
    ...over,
  };
}

function start(stages: Stage[]): JobStream {
  return { stages, status: "running", error: null, cost_usd: 0 };
}

/** Fold a list of frames, the way the hook does. */
function play(state: JobStream, ...events: JobEvent[]): JobStream {
  return events.reduce((s, event) => reduceJobStream(s, { type: "event", event }), state);
}

const render = (over: Partial<JobEvent> = {}): JobEvent => ({
  type: "stage.progress",
  job_id: "j1",
  ...over,
});

describe("progress", () => {
  it("records the fraction against the running stage", () => {
    const after = play(
      start([stage("render", { status: "running" })]),
      render({ message: "encoding", fraction: 0.85 }),
    );

    expect(after.stages[0].progress).toBe(0.85);
    expect(after.stages[0].summary).toBe("encoding");
  });

  it("attributes a fraction to the running stage when the frame names none", () => {
    // `WorkflowContext.progress` sends no `stage` — the engine expects the client
    // to know which one is in flight. Getting this wrong drops every fraction.
    const after = play(
      start([stage("voiceover", { status: "done" }), stage("render", { status: "running" })]),
      render({ message: "placing beats", fraction: 0.72 }),
    );

    expect(after.stages[0].progress).toBeUndefined();
    expect(after.stages[1].progress).toBe(0.72);
  });

  it("keeps the last fraction through a keepalive that carries none", () => {
    // base.py emits "still working — 240s elapsed" with no fraction every few
    // seconds. Writing null on those would drop a determinate bar back to a
    // shimmer for most of a long render.
    const after = play(
      start([stage("render", { status: "running" })]),
      render({ message: "encoding", fraction: 0.85 }),
      render({ message: "still working — 240s elapsed" }),
    );

    expect(after.stages[0].progress).toBe(0.85);
    expect(after.stages[0].summary).toBe("still working — 240s elapsed");
  });

  it("clears the fraction when the stage starts again", () => {
    // A re-run of a stage that previously reached 85% must not open at 85%.
    const after = play(
      start([stage("render", { status: "running" })]),
      render({ fraction: 0.85 }),
      { type: "stage.started", job_id: "j1", stage: "render" },
    );

    expect(after.stages[0].progress).toBeNull();
  });

  it("clears the fraction on a retry", () => {
    const after = play(
      start([stage("render", { status: "running" })]),
      render({ fraction: 0.85 }),
      { type: "stage.retrying", job_id: "j1", stage: "render", attempt: 2, error: "timeout" },
    );

    expect(after.stages[0].progress).toBeNull();
    expect(after.stages[0].status).toBe("running");
  });

  it("ignores a fraction when no stage is running", () => {
    // Nothing to attribute it to, and inventing a row for a frame that carries no
    // stage name would put a phantom entry in the pipeline.
    const after = play(
      start([stage("render", { status: "done" })]),
      render({ message: "orphan", fraction: 0.5 }),
    );

    expect(after.stages).toHaveLength(1);
    expect(after.stages[0].progress).toBeUndefined();
  });
});
