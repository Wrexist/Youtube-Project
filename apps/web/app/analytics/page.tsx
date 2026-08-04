import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { MonetisationCard } from "@/components/monetisation";
import { ShortCuts } from "@/components/short-cuts";
import { getMonetisation } from "@/lib/engine";
import { BigNumber, StatTile, RetentionMap } from "@/components/charts";
import {
  VIEWS_28D,
  CTR_28D,
  AVD_28D,
  SUBS_28D,
  RETENTION,
  RETENTION_BEATS,
  RETENTION_BEAT_MAP,
  SHORT_CUTS,
  FINDINGS,
  SKIPPED,
} from "@/lib/demo";

/** Analytics — a narrative, not a wall of charts.
 *
 *  Read top to bottom: the one number that matters, three supporting tiles, then
 *  what the system has learned as plain sentences, then the retention map. The
 *  per-video table is last and collapsed — reference material, not insight.
 *
 *  Every claim carries its sample size and verdict. A finding from 9 videos must not
 *  look like a finding from 90, because only the confirmed ones actually change what
 *  the generator does.
 */
export default async function AnalyticsPage() {
  // The one figure on this screen that is real. Everything below it is still
  // `lib/demo.ts` — the per-video metrics need published videos to attribute, and
  // this needs only a connected channel, so the two arrive at different times and
  // the badges have to say so separately. KNOWN-ISSUES §5.5.
  const monetisation = await getMonetisation();
  const total = VIEWS_28D.reduce((a, b) => a + b, 0);
  const confirmed = FINDINGS.filter((f) => f.verdict === "confirmed");

  return (
    <>
      <Header
        title="Analytics"
        meta={
          <span className="flex items-center gap-2">
            Last 28 days
            {/* Every figure below comes from lib/demo.ts. The Analytics API needs a
                connected channel and published videos; until then these are a
                design, not measurements, and must say so. */}
            <LiveBadge live={false} />
          </span>
        }
      />
      <Page>
        {monetisation && (
          <section className="pb-10">
            <MonetisationCard data={monetisation} />
          </section>
        )}

        <section className="pb-10">
          <BigNumber
            label="Views"
            value={`${(total / 1000).toFixed(0)}k`}
            delta={38.2}
            series={VIEWS_28D}
          />
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            The two most recent days are provisional — YouTube&apos;s data lags 24–48
            hours.
          </p>
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
          <div className="flex items-baseline justify-between">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              What&apos;s working
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {confirmed.length} of {FINDINGS.length} confirmed
            </span>
          </div>

          <div className="mt-3 grid gap-2.5">
            {FINDINGS.map((f) => (
              <Card key={f.claim} className="px-5 py-4">
                <div className="flex items-start gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <VerdictBadge verdict={f.verdict} />
                      <p className="text-[14px] leading-snug">{f.claim}</p>
                    </div>
                    <p className="mono mt-1.5 text-[12px] text-[var(--color-faint)]">
                      {f.detail} · p={f.p.toFixed(3)}
                    </p>
                  </div>
                  <span
                    className="mono shrink-0 text-[13px]"
                    style={{
                      color:
                        f.verdict === "confirmed"
                          ? "var(--color-ok)"
                          : "var(--color-muted)",
                    }}
                  >
                    +{f.lift}%
                  </span>
                </div>
              </Card>
            ))}
          </div>

          <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-faint)]">
            Confirmed findings feed back into title and hook generation
            automatically. Suggestive ones are shown here and deliberately withheld
            from the generator — training on noise makes the system worse, and it
            does it invisibly.
          </p>

          {SKIPPED.length > 0 && (
            <div className="mt-3 rounded-[var(--radius-card)] border border-dashed border-[var(--color-line)] px-4 py-3">
              <p className="text-[12px] font-semibold text-[var(--color-muted)]">
                Not enough data to compare
              </p>
              {SKIPPED.map((s) => (
                <p key={s} className="mono mt-1 text-[11px] text-[var(--color-faint)]">
                  {s}
                </p>
              ))}
            </div>
          )}
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

          <div className="mt-3 grid gap-1.5">
            {RETENTION_BEAT_MAP.map((beat) => (
              <div
                key={beat.label}
                className="flex items-center gap-3 text-[12px]"
                style={{
                  color: beat.worst ? "var(--color-warn)" : "var(--color-faint)",
                }}
              >
                <span className="mono w-10 shrink-0 text-right">{beat.at}%</span>
                <span className="w-40 shrink-0">{beat.label}</span>
                <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--color-raised)]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(beat.drop / 25) * 100}%`,
                      background: beat.worst
                        ? "var(--color-warn)"
                        : "var(--color-line-hover)",
                    }}
                  />
                </div>
                <span className="mono w-16 shrink-0 text-right">
                  −{beat.drop} pts
                </span>
              </div>
            ))}
          </div>

          <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-faint)]">
            Drop is measured per unit of runtime, so a long beat isn&apos;t flagged
            just for being long. The worst beat here is fed into the next script&apos;s
            prompt as an explicit instruction not to repeat it.
          </p>
        </section>

        <section className="mt-10">
          <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
            Worth cutting into a Short
          </h2>
          <Card className="mt-3 p-6">
            <ShortCuts cuts={SHORT_CUTS} />
          </Card>
          <p className="mt-3 max-w-[64ch] text-[12px] leading-relaxed text-[var(--color-faint)]">
            Ranked by how far each stretch rises above the video&apos;s own retention
            decay — not by raw retention, which is highest in the first ten seconds
            of every video ever made and would pick the intro every time.
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
              Populated from the YouTube Analytics API once a channel is connected.
            </Card>
          </details>
        </section>
      </Page>
    </>
  );
}

function VerdictBadge({ verdict }: { verdict: "confirmed" | "suggestive" }) {
  const confirmed = verdict === "confirmed";
  return (
    <span
      className="mono shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase"
      style={{
        color: confirmed ? "var(--color-ok)" : "var(--color-muted)",
        borderColor: confirmed ? "var(--color-ok)" : "var(--color-line-hover)",
      }}
    >
      {verdict}
    </span>
  );
}
