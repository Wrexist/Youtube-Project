import { Card } from "@/components/ui";
import type { Spend } from "@studio/contracts";

/**
 * What this channel has cost.
 *
 * Cost was metered per stage and capped per video from the beginning, and nothing
 * answered the only question that decides whether the product is usable at volume:
 * what have I spent. Read off the `jobs` table, where `cost_usd` is written by the
 * same stage boundary that spends the money — not out of `automation.SpendLedger`,
 * which is in-memory, series-scoped and written by nothing.
 *
 * Three numbers, in the order the question is actually asked: this month, per
 * video, and the window total. The per-video figure is the one that forecasts, so
 * it gets the emphasis.
 */
export function SpendCard({ spend, days }: { spend: Spend; days: number }) {
  const bars = spend.days ?? [];
  const peak = Math.max(0.01, ...bars.map((d) => d.usd));

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[15px] font-semibold">Spend</h2>
        <span className="mono text-[12px] text-[var(--color-faint)]">
          last {days} days
        </span>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <Figure label="This month" value={money(spend.month_usd)} />
        <Figure
          label="Per finished video"
          value={spend.per_video_usd === null ? "—" : money(spend.per_video_usd)}
          // The forecasting number: what the next video will cost. Emphasised over
          // the totals, which only describe what already happened.
          emphasis
          note={
            spend.completed_videos === 0
              ? "no finished videos yet"
              : `over ${spend.completed_videos} video${spend.completed_videos === 1 ? "" : "s"}`
          }
        />
        <Figure label="Window total" value={money(spend.total_usd)} />
      </div>

      {bars.length > 0 && (
        <>
          {/* One bar per day that had a job, not per calendar day. A run of empty
              columns says "nothing happened" far less clearly than a gap does, and
              this is a channel that publishes a few times a week at most. */}
          <div
            className="mt-5 flex h-12 items-end gap-[3px]"
            role="img"
            aria-label={`Daily spend across ${bars.length} active day${bars.length === 1 ? "" : "s"}, highest ${money(peak)}`}
          >
            {bars.map((day) => (
              <span
                key={day.date}
                title={`${day.date} · ${money(day.usd)} · ${day.jobs} job${day.jobs === 1 ? "" : "s"}`}
                className="min-w-[3px] flex-1 rounded-t-[2px] bg-[var(--color-line-hover)]"
                style={{ height: `${Math.max(4, (day.usd / peak) * 100)}%` }}
              />
            ))}
          </div>
          <p className="mono mt-2 flex justify-between text-[11px] text-[var(--color-faint)]">
            <span>{bars[0].date}</span>
            <span>{bars[bars.length - 1].date}</span>
          </p>
        </>
      )}

      {bars.length === 0 && (
        <p className="mt-4 text-[12px] text-[var(--color-muted)]">
          Nothing spent in this window. Costs appear here as soon as a run starts —
          every stage records what it spent, including runs that fail.
        </p>
      )}
    </Card>
  );
}

/** Dollars, at the precision the number deserves. */
function money(usd: number): string {
  // Two decimals below ten, none above. "$1.02" is the cost of a video and the
  // cents matter; "$147" is a month and they do not.
  return usd < 10 ? `$${usd.toFixed(2)}` : `$${Math.round(usd).toLocaleString()}`;
}

function Figure({
  label,
  value,
  note,
  emphasis = false,
}: {
  label: string;
  value: string;
  note?: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <p className="text-[12px] text-[var(--color-muted)]">{label}</p>
      <p
        className={`mono mt-1 ${emphasis ? "text-[20px]" : "text-[16px]"} ${
          emphasis ? "" : "text-[var(--color-muted)]"
        }`}
      >
        {value}
      </p>
      {note && <p className="mt-0.5 text-[11px] text-[var(--color-faint)]">{note}</p>}
    </div>
  );
}
