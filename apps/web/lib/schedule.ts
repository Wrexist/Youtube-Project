/** Client-side mirror of the engine's scheduler.
 *
 *  The engine at apps/engine/engine/scheduling.py is authoritative — the calendar
 *  calls POST /v1/calendar/auto whenever it is reachable. This exists only so the
 *  calendar is explorable with no backend running, and so a drag can be validated
 *  instantly instead of round-tripping.
 *
 *  Keep the constants below in sync with scheduling.py. They are duplicated
 *  deliberately and in one place, rather than scattered through the components.
 */

/** The default ceiling. Real installs read `quota.limit` off `GET /v1/quota` and
 *  pass it down — an approved quota extension raises it, and a screen that keeps
 *  insisting on 10,000 would refuse drops the engine would happily accept. */
export const DAILY_LIMIT = 10_000;
export const PUBLISH_COST = 1600 + 50 + 400; // insert + thumbnail + captions
/** How many uploads a day's units buy. 4 at the default ceiling. */
export const uploadsPerDay = (limit = DAILY_LIMIT) => Math.floor(limit / PUBLISH_COST);
export const MIN_GAP_HOURS = 20;
export const PEAK_LEAD_HOURS = 2;

/** Relative audience activity by hour. Replaced by measured analytics in Phase 8;
 *  until then the UI labels every recommendation as estimated. */
const HOURLY = [
  0.15, 0.1, 0.07, 0.05, 0.05, 0.08, 0.18, 0.32, 0.45, 0.48, 0.46, 0.5, 0.58,
  0.55, 0.52, 0.56, 0.68, 0.82, 0.94, 1.0, 0.96, 0.82, 0.58, 0.32,
];
const WEEKDAY = [0.92, 0.9, 0.94, 1.0, 1.04, 1.02, 0.98]; // Mon..Sun

export const CANDIDATE_HOURS = [9, 12, 15, 17, 19, 21];

export function slotScore(at: Date): number {
  const target = (at.getHours() + PEAK_LEAD_HOURS) % 24;
  const weekday = (at.getDay() + 6) % 7; // JS weeks start Sunday
  return HOURLY[target] * WEEKDAY[weekday];
}

export function slotReason(at: Date): string {
  const peak = (at.getHours() + PEAK_LEAD_HOURS) % 24;
  return `estimated audience peak at ${String(peak).padStart(2, "0")}:00`;
}

export interface Scheduled {
  videoId: string;
  at: Date;
}

/** Validate a manual drag. Mirrors `validate_move` in scheduling.py.
 *  A permitted-but-unwise move returns ok with a warning rather than silently
 *  being accepted. */
export function validateMove(
  at: Date,
  existing: Scheduled[],
  quotaByDay: Record<string, number>,
  limit = DAILY_LIMIT,
  now = new Date(),
): { ok: boolean; message: string } {
  if (at <= now) return { ok: false, message: "that time has already passed" };

  // Quota days, not calendar days — see `quotaKey`. Both sides have to agree, so
  // the already-booked count is keyed the same way the ledger is.
  const key = quotaKey(at);
  const used = quotaByDay[key] ?? 0;
  const sameDay = existing.filter((s) => quotaKey(s.at) === key).length;
  if (Math.floor((limit - used) / PUBLISH_COST) <= sameDay) {
    return {
      ok: false,
      message: `no upload quota left on ${at.getDate()} — each upload costs ${PUBLISH_COST.toLocaleString()} of ${limit.toLocaleString()} daily units`,
    };
  }

  const conflict = existing.find(
    (s) => Math.abs(at.getTime() - s.at.getTime()) < MIN_GAP_HOURS * 3600_000,
  );
  if (conflict) {
    const gap = Math.abs(at.getTime() - conflict.at.getTime()) / 3600_000;
    return {
      ok: true,
      message: `only ${Math.round(gap)}h from another upload — they'll compete in the same feeds`,
    };
  }

  return { ok: true, message: "" };
}

/** Best free hour on a given day, given what's already booked. */
export function bestHourOn(day: Date, existing: Scheduled[]): Date | null {
  const options = CANDIDATE_HOURS.map((h) => {
    const at = new Date(day);
    at.setHours(h, 0, 0, 0);
    return at;
  })
    .filter((at) => at > new Date())
    .filter(
      (at) =>
        !existing.some(
          (s) => Math.abs(at.getTime() - s.at.getTime()) < 3600_000,
        ),
    )
    .sort((a, b) => slotScore(b) - slotScore(a));
  return options[0] ?? null;
}

export interface AutoResult {
  assignments: { videoId: string; at: Date; score: number; reason: string }[];
  unplaced: { videoId: string; reason: string }[];
}

/** Demo-only mirror of `auto_schedule`. Long-form gets first pick; cadence,
 *  spacing, and the daily ceiling are all enforced. */
export function autoSchedule(
  pending: { id: string; format: "short" | "long" }[],
  existing: Scheduled[],
  quotaByDay: Record<string, number>,
  opts: {
    shortsPerWeek?: number;
    longPerWeek?: number;
    horizonDays?: number;
    dailyLimit?: number;
  } = {},
): AutoResult {
  const { shortsPerWeek = 3, longPerWeek = 1, horizonDays = 28, dailyLimit = DAILY_LIMIT } = opts;
  const perDayCap = uploadsPerDay(dailyLimit);
  const now = new Date();
  const taken = [...existing];
  const result: AutoResult = { assignments: [], unplaced: [] };

  const slots: Date[] = [];
  for (let d = 0; d < horizonDays; d++) {
    for (const h of CANDIDATE_HOURS) {
      const at = new Date(now);
      at.setDate(now.getDate() + d);
      at.setHours(h, 0, 0, 0);
      if (at > now) slots.push(at);
    }
  }
  slots.sort((a, b) => slotScore(b) - slotScore(a));

  const perDay: Record<string, number> = {};
  for (const s of taken) perDay[quotaKey(s.at)] = (perDay[quotaKey(s.at)] ?? 0) + 1;
  const perWeek: Record<string, number> = {};

  // Long-form first: it costs more to make and gains more from a good slot.
  const ordered = [...pending].sort((a, b) =>
    a.format === b.format ? 0 : a.format === "long" ? -1 : 1,
  );

  for (const video of ordered) {
    const cap = video.format === "long" ? longPerWeek : shortsPerWeek;
    const slot = slots.find((at) => {
      if (taken.some((s) => Math.abs(at.getTime() - s.at.getTime()) < MIN_GAP_HOURS * 3600_000))
        return false;
      const key = quotaKey(at);
      if ((perDay[key] ?? 0) >= perDayCap) return false;
      const budget = Math.floor((dailyLimit - (quotaByDay[key] ?? 0)) / PUBLISH_COST);
      if (budget - (perDay[key] ?? 0) <= 0) return false;
      const wk = `${weekOf(at)}:${video.format}`;
      return (perWeek[wk] ?? 0) < cap;
    });

    if (!slot) {
      result.unplaced.push({
        videoId: video.id,
        reason: `no slot in ${horizonDays} days satisfies ${cap} ${video.format}/week, the ${MIN_GAP_HOURS}h gap, and the daily quota`,
      });
      continue;
    }

    result.assignments.push({
      videoId: video.id,
      at: slot,
      score: slotScore(slot),
      reason: slotReason(slot),
    });
    taken.push({ videoId: video.id, at: slot });
    perDay[quotaKey(slot)] = (perDay[quotaKey(slot)] ?? 0) + 1;
    const wk = `${weekOf(slot)}:${video.format}`;
    perWeek[wk] = (perWeek[wk] ?? 0) + 1;
  }

  result.assignments.sort((a, b) => a.at.getTime() - b.at.getTime());
  return result;
}

/** The calendar day a date falls on, locally, as `YYYY-MM-DD`.
 *
 *  Zero-padded because the engine keys everything it sends by `date.isoformat()`
 *  (`api/publishing.py`), and this emitted `2026-1-5` — so `quotaByDay[dayKey(d)]`
 *  missed every day before the 10th of a month and every month before October, and
 *  the calendar reported those days as having no quota spent at all.
 *
 *  Local, not Pacific: this keys grid cells and groups chips into them, and both
 *  sides of that comparison are the viewer's own dates. For looking up what the
 *  *engine* has spent against a specific publish instant, use `quotaKey`. */
export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

/** The engine's quota day for an instant, as `YYYY-MM-DD`.
 *
 *  Google resets quota at midnight Pacific, so `quota.py:quota_day` converts to
 *  `America/Los_Angeles` before taking the date — a 22:00 publish on the US east
 *  coast is charged to the *previous* quota day, and an 09:00 publish in Europe to
 *  the previous one too. Keying a drag off the viewer's local date therefore
 *  checked the wrong day's budget for part of every day, everywhere but Pacific.
 *
 *  `formatToParts` rather than a formatted string: the parts are named, so this
 *  cannot be broken by a locale that reorders or re-punctuates the date. */
const PACIFIC = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Los_Angeles",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function quotaKey(at: Date): string {
  const parts = Object.fromEntries(
    PACIFIC.formatToParts(at).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function weekOf(d: Date): number {
  const start = new Date(d.getFullYear(), 0, 1);
  return Math.floor((d.getTime() - start.getTime()) / (7 * 86_400_000));
}

export function fmtTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
