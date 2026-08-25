/**
 * The play layer's arithmetic: XP, levels and achievements, derived entirely
 * from real jobs. Nothing here is stored and nothing is invented — delete the
 * module and no data is lost, which is the test a game layer on a tool has to
 * pass. See docs/UI-DESIGN.md § The play layer.
 *
 * Pure functions over `JobSummary[]` so the whole thing is testable without a
 * DOM or an engine.
 */

import type { JobSummary } from "@studio/contracts";

export interface Achievement {
  id: string;
  title: string;
  detail: string;
  earned: boolean;
}

export interface StudioProgress {
  xp: number;
  level: number;
  /** The level's name — what the chip shows on hover. */
  title: string;
  /** XP where this level started and where the next begins, for the bar. */
  levelFloor: number;
  nextLevel: number;
  achievements: Achievement[];
}

/** What finishing things is worth. Rendering is the work; publishing is the point. */
const XP_PER_VIDEO = 100;
const XP_PER_PUBLISH = 150;

/** Triangular ladder: level n spans n×100 XP, so early levels come quickly and
 *  the climb stretches out — the standard shape because it works. */
function levelFloor(level: number): number {
  return ((level - 1) * level * 100) / 2;
}

const TITLES = [
  "Newcomer",
  "Cutter",
  "Editor",
  "Producer",
  "Showrunner",
  "Director",
  "Studio Head",
  "Mogul",
];

export function computeProgress(jobs: JobSummary[]): StudioProgress {
  const videos = jobs.filter((j) => j.workflow !== "publish" && j.status === "completed");
  const published = jobs.filter((j) => j.workflow === "publish" && j.status === "completed");

  const xp = videos.length * XP_PER_VIDEO + published.length * XP_PER_PUBLISH;

  let level = 1;
  while (levelFloor(level + 1) <= xp) level += 1;

  const cheap = videos.some((j) => j.cost_usd > 0 && j.cost_usd < 0.5);
  const achievements: Achievement[] = [
    {
      id: "first_render",
      title: "First render",
      detail: "One finished video in the library.",
      earned: videos.length >= 1,
    },
    {
      id: "shipped",
      title: "Shipped",
      detail: "Published to a real channel.",
      earned: published.length >= 1,
    },
    {
      id: "five_in_the_can",
      title: "Five in the can",
      detail: "Five videos rendered end to end.",
      earned: videos.length >= 5,
    },
    {
      id: "double_digits",
      title: "Double digits",
      detail: "Ten finished videos.",
      earned: videos.length >= 10,
    },
    {
      id: "penny_pincher",
      title: "Penny pincher",
      detail: "A full video for under fifty cents.",
      earned: cheap,
    },
    {
      id: "publish_streak",
      title: "On a roll",
      detail: "Three publishes.",
      earned: published.length >= 3,
    },
  ];

  return {
    xp,
    level,
    title: TITLES[Math.min(level - 1, TITLES.length - 1)],
    levelFloor: levelFloor(level),
    nextLevel: levelFloor(level + 1),
    achievements,
  };
}
