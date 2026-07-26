/**
 * View types for the web app.
 *
 * `StageStatus` and `JobStatus` are re-exported from `@studio/contracts`, which
 * generates them from the engine's OpenAPI document — they are not redeclared
 * here, so an engine change surfaces as a type error rather than as drift.
 *
 * `Stage`, `Variant` and `Job` are *view* shapes, not API mirrors. The engine
 * types `/v1/jobs/{id}` as `-> dict`, so its schema says `object` and nothing
 * more; these describe what the screens render, including fields the API does not
 * send yet (`variants`, `detail`). When the engine grows response models these
 * collapse into generated types — see the note in `packages/contracts/src/index.ts`.
 */

import type { JobStatus, StageStatus } from "@studio/contracts";

export type { JobStatus, StageStatus };

export interface Stage {
  name: string;
  title: string;
  status: StageStatus;
  summary: string | null;
  cost_usd: number;
  elapsed_ms: number;
  error: string | null;
  editable: boolean;
  /** Populated for stages that produce pickable alternatives (hook, titles, thumbnail). */
  variants?: Variant[];
  detail?: string;
}

export interface Variant {
  label: string;
  text: string;
  score?: number;
  note?: string;
}

export interface Job {
  id: string;
  status: JobStatus;
  topic: string;
  format: "short" | "long";
  stages: Stage[];
  cost_usd: number;
}
