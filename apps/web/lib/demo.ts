/** Demo data.
 *
 *  The dashboard is fully explorable before the engine is running or any API key is
 *  set — which is the only honest way to evaluate a design. Every shape here matches
 *  the engine's real serialisation, so wiring up is a swap of the data source, not a
 *  rewrite of the views.
 */

import type { Job, Stage } from "./types";

export const DEMO_STAGES: Stage[] = [
  {
    name: "grounding",
    title: "Keyword research",
    status: "done",
    summary: "312 queries · 20 competitors",
    cost_usd: 0.02,
    elapsed_ms: 4200,
    error: null,
    editable: true,
    detail:
      "Primary keyword: “why bridges collapse” · 8.1k/mo, low competition.\nGap: every ranking title leads with the disaster. None explain the engineering.",
  },
  {
    name: "research",
    title: "Research",
    status: "done",
    summary: "8 sources · 14 facts",
    cost_usd: 0.14,
    elapsed_ms: 31400,
    error: null,
    editable: true,
    detail:
      "NTSB report (2024) · ASCE infrastructure grade C− · 42,400 US bridges rated poor · Tacoma Narrows aeroelastic flutter, 1940",
  },
  {
    name: "angle",
    title: "Angle",
    status: "done",
    summary: "Failure is designed in, not accidental",
    cost_usd: 0.04,
    elapsed_ms: 8100,
    error: null,
    editable: true,
  },
  {
    name: "hook",
    title: "Hook",
    status: "done",
    summary: "“Every bridge you drive over…”",
    cost_usd: 0.05,
    elapsed_ms: 6900,
    error: null,
    editable: true,
    variants: [
      {
        label: "contradiction",
        text: "Every bridge you drive over is already failing. On purpose.",
        score: 0.91,
        note: "time to tension: 6 words",
      },
      {
        label: "curiosity gap",
        text: "There's a number engineers use that decides when a bridge dies.",
        score: 0.84,
        note: "time to tension: 8 words",
      },
      {
        label: "stakes",
        text: "42,400 bridges in this country are rated poor. Here's what that means.",
        score: 0.78,
        note: "time to tension: 3 words",
      },
    ],
  },
  {
    name: "beats",
    title: "Structure",
    status: "done",
    summary: "16 beats · 6 chapters",
    cost_usd: 0.08,
    elapsed_ms: 12000,
    error: null,
    editable: true,
  },
  {
    name: "draft",
    title: "Draft",
    status: "done",
    summary: "1,240 words · ~8:20",
    cost_usd: 0.21,
    elapsed_ms: 44000,
    error: null,
    editable: true,
  },
  {
    name: "critique",
    title: "Critique",
    status: "done",
    summary: "3 issues · severity 3",
    cost_usd: 0.09,
    elapsed_ms: 15200,
    error: null,
    editable: true,
    detail:
      "Attention drop at “Now, before we look at the data…” — a stall.\nVague: “much higher than expected” where a figure exists.\nPromise paid at 4:10, later than ideal.",
  },
  {
    name: "revision",
    title: "Script",
    status: "done",
    summary: "1,198 words · ~8:02",
    cost_usd: 0.19,
    elapsed_ms: 39000,
    error: null,
    editable: true,
  },
  {
    name: "voiceover",
    title: "Voiceover",
    status: "running",
    summary: null,
    cost_usd: 0,
    elapsed_ms: 18000,
    error: null,
    editable: false,
  },
  { ...blank("subtitles", "Subtitles") },
  { ...blank("materials", "Materials") },
  { ...blank("render", "Render") },
  { ...blank("titles", "Titles") },
  { ...blank("description", "Description") },
  { ...blank("tags", "Tags") },
  { ...blank("chapters", "Chapters") },
  { ...blank("thumbnail", "Thumbnail") },
];

function blank(name: string, title: string): Stage {
  return {
    name,
    title,
    status: "pending",
    summary: null,
    cost_usd: 0,
    elapsed_ms: 0,
    error: null,
    editable: false,
  };
}

export const DEMO_JOB: Job = {
  id: "a41f9c22b0e1",
  status: "running",
  topic: "Why bridges collapse",
  format: "long",
  stages: DEMO_STAGES,
  cost_usd: 0.82,
};

export const QUEUE: Job[] = [
  DEMO_JOB,
  {
    id: "77c1de40aa93",
    status: "completed",
    topic: "The airline seat that never sold",
    format: "short",
    stages: [],
    cost_usd: 0.41,
  },
  {
    id: "1b9f0e77c214",
    status: "failed",
    topic: "How salt changed medieval trade",
    format: "long",
    stages: [],
    cost_usd: 1.94,
  },
  {
    id: "c0d8a12e5b6f",
    status: "completed",
    topic: "Why elevators have mirrors",
    format: "short",
    stages: [],
    cost_usd: 0.38,
  },
];

/** 28 days of views. Deterministic — a chart that reshuffles on every render is
 *  impossible to review. */
export const VIEWS_28D = [
  4100, 3900, 4600, 5200, 4800, 6100, 7400, 6900, 6200, 7100, 8300, 9100, 8700,
  9400, 11200, 10600, 9900, 10400, 12100, 13800, 12900, 12200, 13500, 15100,
  14400, 16200, 17800, 19400,
];

export const CTR_28D = [
  4.1, 4.0, 4.3, 4.6, 4.4, 4.9, 5.2, 5.0, 4.8, 5.1, 5.4, 5.7, 5.5, 5.6, 6.0,
  5.8, 5.7, 5.9, 6.2, 6.5, 6.3, 6.1, 6.4, 6.7, 6.6, 6.9, 7.1, 7.3,
];

export const AVD_28D = [
  182, 178, 190, 201, 195, 210, 224, 218, 205, 214, 231, 240, 236, 244, 258,
  249, 241, 247, 262, 271, 265, 259, 268, 279, 274, 283, 291, 298,
];

export const SUBS_28D = [
  12, 9, 14, 18, 15, 22, 31, 27, 21, 26, 34, 41, 38, 44, 52, 47, 43, 46, 55,
  63, 58, 54, 61, 69, 66, 72, 78, 84,
];

/** Retention curve for one video, sampled every 2% of its runtime. */
export const RETENTION = [
  100, 94, 88, 84, 81, 79, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 64,
  62, 58, 54, 52, 51, 50, 49, 48, 47, 46, 45, 45, 44, 43, 42, 41, 40, 39, 37,
  35, 33, 31, 29, 27, 25, 23, 21, 19, 16, 13, 9,
];

/** Script beats mapped onto the retention curve. This overlay is the single most
 *  useful visualisation in the product: a drop-off points at the sentence that
 *  caused it. */
export const RETENTION_BEATS = [
  { at: 0, label: "Hook" },
  { at: 12, label: "Setup" },
  { at: 20, label: "First data point", warn: true },
  { at: 34, label: "Case study" },
  { at: 47, label: "Mid-roll device" },
  { at: 68, label: "Counter-argument" },
  { at: 86, label: "Payoff" },
];

/** Findings carry their verdict, sample size, and p-value.
 *  Only `confirmed` ones are fed back into generation — see engine/feedback.py. */
export const FINDINGS = [
  {
    claim: "Curiosity-gap titles beat number-led titles on click-through rate",
    detail: "6.2% vs 4.1% across 23 and 19 videos",
    verdict: "confirmed" as const,
    lift: 51,
    p: 0.003,
    n: [23, 19] as [number, number],
  },
  {
    claim: "One-focal-point thumbnails beat composed scenes on click-through rate",
    detail: "5.9% vs 4.4% across 18 and 14 videos",
    verdict: "confirmed" as const,
    lift: 34,
    p: 0.019,
    n: [18, 14] as [number, number],
  },
  {
    claim: "Contradiction hooks hold 30-second retention better than questions",
    detail: "74% vs 66% across 11 and 9 videos",
    verdict: "suggestive" as const,
    lift: 12,
    p: 0.082,
    n: [11, 9] as [number, number],
  },
];

/** Dimensions with too little data to compare. Stated rather than hidden — an
 *  absent finding and an untested one look identical otherwise. */
export const SKIPPED = [
  "script_model: needs 2 groups of 8+ videos, has 1 (3 groups total)",
];

/** Retention curve mapped onto script beats. `worst` marks the steepest drop
 *  per unit of runtime, so a long beat isn't flagged just for being long. */
export const RETENTION_BEAT_MAP = [
  { at: 0, label: "Hook", drop: 12, worst: false },
  { at: 12, label: "Setup", drop: 7, worst: false },
  { at: 20, label: "First data point", drop: 23, worst: true },
  { at: 34, label: "Case study", drop: 6, worst: false },
  { at: 47, label: "Mid-roll device", drop: 4, worst: false },
  { at: 68, label: "Counter-argument", drop: 9, worst: false },
  { at: 86, label: "Payoff", drop: 11, worst: false },
];

/** Finished videos waiting for a publish slot. */
export const PENDING_VIDEOS = [
  { id: "p1", title: "Why bridges collapse", format: "long" as const, duration: "8:02" },
  { id: "p2", title: "The airline seat that never sold", format: "short" as const, duration: "0:48" },
  { id: "p3", title: "How salt built cities", format: "long" as const, duration: "9:14" },
  { id: "p4", title: "Why elevators have mirrors", format: "short" as const, duration: "0:52" },
  { id: "p5", title: "The clock that broke physics", format: "short" as const, duration: "0:41" },
  { id: "p6", title: "The map that started a war", format: "long" as const, duration: "11:20" },
  { id: "p7", title: "Why glass is a liquid (it isn't)", format: "short" as const, duration: "0:55" },
];

/** Quota already consumed per day, mostly by competitor research.
 *  Keyed the same way `dayKey` in lib/schedule.ts keys days. */
export const QUOTA_BY_DAY: Record<string, number> = (() => {
  const out: Record<string, number> = {};
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  // A day that is nearly spent, so the "full" and blocked-drop states are reachable
  // without having to schedule four videos first.
  const spend = [400, 1200, 9200, 800, 0, 300, 100, 2400, 0, 600];
  spend.forEach((units, i) => {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    out[`${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`] = units;
  });
  return out;
})();

/** Standing series configs. Auto-publish is off by default and deliberately
 *  awkward to turn on — see engine/automation.py. */
export const SERIES = [
  {
    id: "s1",
    name: "Engineering failures",
    niche: "civil engineering, disasters",
    shortsPerWeek: 3,
    longPerWeek: 1,
    autoPublish: false,
    paused: false,
    monthlyBudget: 120,
    spentThisMonth: 71.4,
    backlogDepth: 14,
    producedThisWeek: 2,
  },
  {
    id: "s2",
    name: "Objects with histories",
    niche: "material history, trade",
    shortsPerWeek: 2,
    longPerWeek: 1,
    autoPublish: true,
    paused: false,
    monthlyBudget: 90,
    spentThisMonth: 84.2,
    backlogDepth: 3,
    producedThisWeek: 3,
  },
  {
    id: "s3",
    name: "Physics oddities",
    niche: "physics, measurement",
    shortsPerWeek: 4,
    longPerWeek: 0,
    autoPublish: false,
    paused: true,
    monthlyBudget: 60,
    spentThisMonth: 0,
    backlogDepth: 9,
    producedThisWeek: 0,
  },
];

/** Videos waiting on a human. Every blocker carries a readable reason —
 *  "blocked" with no explanation is not an acceptable thing to display. */
export const REVIEW_QUEUE = [
  {
    id: "r1",
    title: "The bridge that sang itself apart",
    series: "Engineering failures",
    cost: 2.41,
    blockers: [],
  },
  {
    id: "r2",
    title: "Why concrete keeps getting weaker",
    series: "Engineering failures",
    cost: 3.12,
    blockers: [
      {
        code: "ungrounded",
        message:
          "the script cites no sources — this is what YouTube's inauthentic-content policy targets",
      },
    ],
  },
  {
    id: "r3",
    title: "How a single bolt grounded a fleet",
    series: "Objects with histories",
    cost: 1.88,
    blockers: [
      { code: "no_thumbnail", message: "no thumbnail was produced" },
      {
        code: "weak_script",
        message: "the critique pass rated this 4/5 for problems",
      },
    ],
  },
];

/** Scored backlog. Rejected duplicates are kept visible rather than dropped, so
 *  you can see the generator tried to repeat itself and was stopped. */
export const BACKLOG = [
  { topic: "Why tunnels flood", score: 0.78, demand: 0.85, fit: 0.62, duplicate: null },
  { topic: "The dam that moved a mountain", score: 0.71, demand: 0.7, fit: 0.71, duplicate: null },
  { topic: "How rebar rusts from inside", score: 0.64, demand: 0.6, fit: 0.68, duplicate: null },
  {
    topic: "The reason bridges collapse",
    score: 0.0,
    demand: 0.9,
    fit: 0.9,
    duplicate: "Why bridges collapse",
    similarity: 0.67,
  },
];

export const LIBRARY = [
  { id: "1", title: "Why bridges collapse", views: "184k", ctr: 7.1, dur: "8:02" },
  { id: "2", title: "The airline seat that never sold", views: "92k", ctr: 6.4, dur: "0:48" },
  { id: "3", title: "Why elevators have mirrors", views: "310k", ctr: 8.2, dur: "0:52" },
  { id: "4", title: "The map that started a war", views: "47k", ctr: 5.1, dur: "11:20" },
  { id: "5", title: "How salt built cities", views: "128k", ctr: 6.8, dur: "9:14" },
  { id: "6", title: "The clock that broke physics", views: "221k", ctr: 7.7, dur: "7:35" },
];
