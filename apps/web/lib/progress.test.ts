/**
 * The play layer must never invent progress. Every number the chip shows is a
 * pure function of real jobs, and these tests pin the arithmetic — especially
 * the boundaries, because an XP bar that jumps a level early reads as a bug and
 * one that jumps late reads as theft.
 */

import { describe, expect, it } from "vitest";

import type { JobSummary } from "@studio/contracts";
import { computeProgress } from "./progress";

function job(overrides: Partial<JobSummary>): JobSummary {
  return {
    id: "j",
    status: "completed",
    topic: "t",
    workflow: "video",
    cost_usd: 1.2,
    stages_done: 17,
    stages_total: 17,
    ...overrides,
  } as JobSummary;
}

describe("computeProgress", () => {
  it("starts at level 1 with nothing done and nothing earned", () => {
    const p = computeProgress([]);
    expect(p.xp).toBe(0);
    expect(p.level).toBe(1);
    expect(p.title).toBe("Newcomer");
    expect(p.achievements.every((a) => !a.earned)).toBe(true);
  });

  it("counts only completed video jobs, not running or failed ones", () => {
    const p = computeProgress([
      job({ id: "a" }),
      job({ id: "b", status: "running" }),
      job({ id: "c", status: "failed" }),
    ]);
    expect(p.xp).toBe(100);
  });

  it("a publish is worth more than a render — publishing is the point", () => {
    const p = computeProgress([
      job({ id: "a" }),
      job({ id: "pub", workflow: "publish" }),
    ]);
    expect(p.xp).toBe(250);
  });

  it("levels follow the triangular ladder exactly at the boundary", () => {
    // Level 2 starts at 100 XP: one video is enough.
    expect(computeProgress([job({ id: "a" })]).level).toBe(2);
    // Level 3 starts at 300 XP: two videos (200) are not.
    expect(computeProgress([job({ id: "a" }), job({ id: "b" })]).level).toBe(2);
  });

  it("earned achievements say what actually happened", () => {
    const jobs = [
      ...Array.from({ length: 5 }, (_, i) => job({ id: `v${i}` })),
      job({ id: "pub", workflow: "publish" }),
      job({ id: "cheap", cost_usd: 0.4 }),
    ];
    const p = computeProgress(jobs);
    const earned = new Set(p.achievements.filter((a) => a.earned).map((a) => a.id));
    expect(earned).toContain("first_render");
    expect(earned).toContain("shipped");
    expect(earned).toContain("five_in_the_can");
    expect(earned).toContain("penny_pincher");
    expect(earned).not.toContain("double_digits");
    expect(earned).not.toContain("publish_streak");
  });

  it("a free (zero-cost) video does not count as penny-pinching", () => {
    // cost 0 usually means a demo or a stub provider, not thrift.
    const p = computeProgress([job({ id: "a", cost_usd: 0 })]);
    expect(p.achievements.find((a) => a.id === "penny_pincher")?.earned).toBe(false);
  });

  it("the bar's floor and ceiling bracket the current xp", () => {
    const p = computeProgress(Array.from({ length: 4 }, (_, i) => job({ id: `v${i}` })));
    expect(p.xp).toBe(400);
    expect(p.levelFloor).toBeLessThanOrEqual(p.xp);
    expect(p.nextLevel).toBeGreaterThan(p.xp);
  });
});
