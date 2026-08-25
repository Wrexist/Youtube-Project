/** Stretches of a long-form video worth cutting into a Short.
 *
 *  Sits directly under the retention map because it is the same data read for a
 *  different purpose: the map says where attention fell, this says where it held.
 *
 *  Presented as sentences rather than a scored table on purpose. The ranking is
 *  driven by two numbers — how far the stretch rises above the video's own decay,
 *  and whether it keeps viewers all the way through — and neither means anything
 *  to someone deciding whether to cut a clip. The reason does. The numbers stay in
 *  the API for anyone who wants to check the ranking; they do not belong on screen.
 *
 *  There is no "Cut this" button because there is no endpoint behind one yet, and
 *  `queue/page.tsx` already settled what to do about that: a button that does
 *  nothing is worse than no button.
 */

import type { ShortCut } from "@studio/contracts";

/** Only the fields this component draws. Derived from the generated contract
 *  rather than restated, per CLAUDE.md: "Never hand-write a type that mirrors an
 *  API response." */
type Cut = Pick<ShortCut, "start_s" | "end_s" | "duration_s" | "label" | "reason">;

function timestamp(seconds: number) {
  // Rounded first, then split. Flooring the minutes before rounding the remainder
  // renders 59.6 as "0:60" and 119.6 as "1:60".
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  return `${m}:${String(total % 60).padStart(2, "0")}`;
}

export function ShortCuts({ cuts, note }: { cuts: Cut[]; note?: string | null }) {
  if (cuts.length === 0) {
    return (
      <p className="max-w-[60ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
        {note ??
          "Nothing in this video rises far enough above its own retention curve to single out."}
      </p>
    );
  }

  return (
    <ol className="space-y-5">
      {cuts.map((cut) => (
        <li key={cut.start_s} className="flex gap-4">
          <span
            className="shrink-0 pt-px font-mono text-[13px] tabular-nums text-[var(--color-muted)]"
            aria-label={`from ${timestamp(cut.start_s)} to ${timestamp(cut.end_s)}`}
          >
            {timestamp(cut.start_s)}–{timestamp(cut.end_s)}
          </span>
          <div className="min-w-0">
            <p className="text-[14px]">
              {cut.label}
              <span className="ml-2 text-[13px] text-[var(--color-faint)]">
                {Math.round(cut.duration_s)}s
              </span>
            </p>
            <p className="mt-1 max-w-[58ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
              {cut.reason}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
