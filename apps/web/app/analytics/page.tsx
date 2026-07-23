import { Header, Page, Card } from "@/components/ui";
import { BigNumber, StatTile, RetentionMap } from "@/components/charts";
import {
  VIEWS_28D,
  CTR_28D,
  AVD_28D,
  SUBS_28D,
  RETENTION,
  RETENTION_BEATS,
  FINDINGS,
} from "@/lib/demo";

/** Analytics — a narrative, not a wall of charts.
 *
 *  Read top to bottom: one number that matters, three supporting tiles, then plain
 *  sentences stating what the system has learned, then the retention map. The
 *  per-video table is last and collapsed, because it is reference material rather
 *  than insight.
 */
export default function AnalyticsPage() {
  const total = VIEWS_28D.reduce((a, b) => a + b, 0);

  return (
    <>
      <Header title="Analytics" meta={<span>Last 28 days</span>} />
      <Page>
        <section className="pb-10">
          <BigNumber
            label="Views"
            value={`${(total / 1000).toFixed(0)}k`}
            delta={38.2}
            series={VIEWS_28D}
          />
        </section>

        <section className="grid gap-4 sm:grid-cols-3">
          <StatTile
            label="Click-through rate"
            value={`${CTR_28D.at(-1)}%`}
            delta={22.4}
            series={CTR_28D}
            highlight
          />
          <StatTile
            label="Average view duration"
            value={`${Math.floor(AVD_28D.at(-1)! / 60)}:${String(AVD_28D.at(-1)! % 60).padStart(2, "0")}`}
            delta={9.1}
            series={AVD_28D}
          />
          <StatTile
            label="Subscribers gained"
            value={SUBS_28D.reduce((a, b) => a + b, 0).toLocaleString()}
            delta={41.6}
            series={SUBS_28D}
          />
        </section>

        {/* The payoff of the whole system: what it has learned, in sentences. */}
        <section className="mt-10">
          <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
            What&apos;s working
          </h2>
          <div className="mt-3 grid gap-2.5">
            {FINDINGS.map((f) => (
              <Card key={f.claim} className="px-5 py-4">
                <div className="flex items-start gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-[14px] leading-snug">{f.claim}</p>
                    <p className="mt-1 text-[12px] text-[var(--color-faint)]">
                      {f.against} · across {f.n} videos
                    </p>
                  </div>
                  <span
                    className="mono shrink-0 text-[13px]"
                    style={{ color: "var(--color-ok)" }}
                  >
                    +{f.lift}%
                  </span>
                </div>
              </Card>
            ))}
          </div>
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            These patterns feed back into title and hook generation automatically.
          </p>
        </section>

        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              Retention · Why bridges collapse
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">8:02</span>
          </div>
          <Card className="mt-3 p-6">
            <RetentionMap curve={RETENTION} beats={RETENTION_BEATS} />
          </Card>
          <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-faint)]">
            Beats are overlaid on the curve, so a drop-off points at the sentence that
            caused it. The steepest fall here lands on the first data point — the
            script states a figure without setting up why it matters.
          </p>
        </section>

        <section className="mt-10">
          <details className="group">
            <summary className="cursor-pointer list-none text-[13px] font-semibold text-[var(--color-muted)] hover:text-[var(--color-ink)]">
              Per-video breakdown
              <span className="ml-2 text-[var(--color-faint)] group-open:hidden">
                show
              </span>
            </summary>
            <Card className="mt-3 p-5 text-[13px] text-[var(--color-faint)]">
              Wired to the YouTube Analytics API in Phase 8. Data lags 24–48 hours;
              the two most recent days are always marked provisional.
            </Card>
          </details>
        </section>
      </Page>
    </>
  );
}
