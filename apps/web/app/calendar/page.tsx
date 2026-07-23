import { Header, Page } from "@/components/ui";
import { QuotaBar } from "@/components/charts";

const SCHEDULED: Record<number, string> = {
  3: "Why bridges collapse",
  7: "The airline seat",
  11: "Salt and cities",
  14: "Elevator mirrors",
  18: "The clock that broke physics",
  22: "The map that started a war",
  25: "How glass is made",
};

/** Calendar. The one place the YouTube API ceiling is made visible: a thin quota
 *  bar per week. An upload costs 1,600 of 10,000 daily units, so roughly six a day
 *  is the hard limit — present it, but don't alarm anyone with it. */
export default function CalendarPage() {
  const days = Array.from({ length: 28 }, (_, i) => i + 1);
  const weeks = [0, 1, 2, 3].map((w) => days.slice(w * 7, w * 7 + 7));

  return (
    <>
      <Header title="Calendar" meta={<span>July 2026</span>} />
      <Page>
        <div className="grid grid-cols-7 gap-px pb-2 text-[11px] text-[var(--color-faint)]">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="px-2">
              {d}
            </div>
          ))}
        </div>

        <div className="grid gap-4">
          {weeks.map((week, wi) => (
            <div key={wi}>
              <div className="grid grid-cols-7 gap-px overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-line)]">
                {week.map((day) => (
                  <div
                    key={day}
                    className="min-h-[92px] bg-[var(--color-surface)] p-2 transition-colors duration-150 hover:bg-[var(--color-raised)]"
                  >
                    <span className="mono text-[11px] text-[var(--color-faint)]">
                      {day}
                    </span>
                    {SCHEDULED[day] && (
                      <div className="mt-1.5 cursor-grab rounded border border-[var(--color-line-hover)] bg-[var(--color-raised)] px-1.5 py-1">
                        <div className="mb-1 h-6 rounded-sm bg-[var(--color-line-hover)]" />
                        <p className="line-clamp-2 text-[10px] leading-tight text-[var(--color-muted)]">
                          {SCHEDULED[day]}
                        </p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="pt-2">
                <QuotaBar used={[3200, 4800, 1600, 6400][wi]} total={10000} />
              </div>
            </div>
          ))}
        </div>

        <p className="mt-6 text-[12px] text-[var(--color-faint)]">
          Drag a video to reschedule — that&apos;s a 50-unit update, so it&apos;s
          effectively free. Uploading is the expensive call at 1,600 units.
        </p>
      </Page>
    </>
  );
}
