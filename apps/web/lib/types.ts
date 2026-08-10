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
  /**
   * How far through a running stage is, 0..1, or null when it has not said.
   *
   * Only the long stages report it — `compose.py` sends 0.05 downloading, 0.25
   * composing, 0.72 placing beats, 0.75 subtitles, 0.85 encoding — and even those
   * emit keepalives with no fraction in between. So null is normal, not an error,
   * and it means "show an indeterminate bar" rather than "show zero".
   */
  progress?: number | null;
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
