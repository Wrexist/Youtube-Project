/** Mirrors the engine's stage serialisation in apps/engine/engine/main.py.
 *  Phase 0 generates this from the OpenAPI schema; until then it is hand-kept and
 *  deliberately minimal so drift is obvious. */

export type StageStatus =
  | "pending"
  | "running"
  | "done"
  | "stale"
  | "failed"
  | "skipped";

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
  status: "running" | "completed" | "failed" | "cancelled";
  topic: string;
  format: "short" | "long";
  stages: Stage[];
  cost_usd: number;
}
