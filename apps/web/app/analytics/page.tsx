import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { MonetisationCard } from "@/components/monetisation";
import { ShortCuts } from "@/components/short-cuts";
import { SpendCard } from "@/components/spend-card";
import { NoReviewYet, WeeklyReview } from "@/components/weekly-review";
import {
  getAnalyticsDaily,
  getAnalyticsVideos,
  getInsights,
  getMonetisation,
  getRetention,
  getReview,
  getSetup,
  getShorts,
  getSpend,
} from "@/lib/engine";

/** Ninety days: long enough to see a month-over-month change, short enough that
 *  the daily bars stay legible at this width. */
const SPEND_DAYS = 90;
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
 *
 *  Live where the engine has data, demo where it does not — per section, because
 *  the sections arrive at different times. Monetisation needs a connected channel;
 *  the tiles need Analytics API rows; findings, retention and Short cuts need
 *  published videos with provenance. Each demo section carries its own badge.
 */
export default async function AnalyticsPage() {
  // Together rather than in sequence: independent reads, and awaiting them one
  // after another makes the slowest page on the app the sum of them.
  const [monetisation, review, setup, spend, daily, videos, insights] =
    await Promise.all([
      getMonetisation(),
      getReview(),
      getSetup(),
      getSpend(SPEND_DAYS),
      getAnalyticsDaily(28),
      getAnalyticsVideos(),
      getInsights(),
    ]);

  const days = daily?.days ?? [];
  const tilesLive = days.length > 0;
  const published = videos ?? [];

  // The newest published video anchors the retention map and the Shorts panel;
  // the newest long-form is the one worth cutting. Fetched only when they exist.
  const retentionTarget = published[0] ?? null;
  const longForm = published.find((v) => v.format === "long") ?? null;
  const [retention, shorts] = await Promise.all([
    retentionTarget ? getRetention(retentionTarget.video_id) : Promise.resolve(null),
    longForm ? getShorts(longForm.video_id) : Promise.resolve(null),
  ]);
  const retentionLive = (retention?.curve?.length ?? 0) > 1;

  const viewsSeries = tilesLive ? days.map((d) => d.views) : VIEWS_28D;
  const avdSeries = tilesLive ? days.map((d) => d.avd_seconds) : AVD_28D;
  const subsSeries = tilesLive ? days.map((d) => d.subscribers_gained) : SUBS_28D;
  // The daily endpoint has no CTR dimension; live CTR comes from the per-video
  // rows, ordered oldest to newest so the sparkline reads left to right.
  const ctrSeries =
    tilesLive && published.length > 0
      ? published.map((v) => Math.round(v.ctr * 1000) / 10).reverse()
      : CTR_28D;

  const total = viewsSeries.reduce((a, b) => a + b, 0);
  const lastAvd = avdSeries.at(-1) ?? 0;

  const findings = insights
    ? insights.findings.map((f) => ({
        claim: f.sentence,
        detail: `${f.winner} vs ${f.loser} · n=${f.n_winner}/${f.n_loser}`,
        verdict: f.verdict as "confirmed" | "suggestive" | "insufficient",
        lift: f.lift,
        p: f.p_value,
      }))
    : FINDINGS.map((f) => ({
        claim: f.claim,
        detail: f.detail,
        verdict: f.verdict as "confirmed" | "suggestive" | "insufficient",
        lift: f.lift,
        p: f.p,
      }));
  const skipped = insights ? insights.skipped : SKIPPED;
  const confirmed = findings.filter((f) => f.verdict === "confirmed");
  const findingsLive = insights !== null;

  const beatRows = retentionLive
    ? (retention?.beats ?? []).map((b) => ({
        at: b.at_percent,
        label: b.label,
        drop: b.drop,
        worst: Boolean(b.worst),
      }))
    : RETENTION_BEAT_MAP.map((b) => ({ ...b, worst: Boolean(b.worst) }));
  const curve = retentionLive ? (retention?.curve ?? []) : RETENTION;
  const curveBeats = retentionLive
    ? (retention?.beats ?? []).map((b) => ({
        at: b.at_percent,
        label: b.label,
        warn: Boolean(b.worst),
      }))
    : RETENTION_BEATS;

  const cuts = shorts?.candidates?.length ? shorts.candidates : SHORT_CUTS;
  const cutsLive = Boolean(shorts?.candidates?.length);

  return (
    <>
      <Header
        title="Analytics"
        meta={
          <span className="flex items-center gap-2">
            Last 28 days
            <LiveBadge live={tilesLive} />
          </span>
        }
      />
      <Page>
        {monetisation && (
          <section className="pb-10">
            <MonetisationCard data={monetisation} />
          </section>
        )}

        {/* Real, like monetisation — the numbers come from the jobs table, which
            exists whether or not a channel is connected. Rendered only when the
            engine answered; a spend card reading $0 because a request failed is a
            lie about money. */}
        {spend && (
          <section className="pb-10">
            <SpendCard spend={spend} days={SPEND_DAYS} />
          </section>
        )}

        {/* Above the charts, below monetisation. It is the only thing on this
            screen that says what *changed* — everything else is a level, and a
            level you have already seen is not news. Rendered even when there is
            nothing yet, because "no review has run" has a cause worth naming. */}
        <section className="pb-10">
          {review ? (
            <WeeklyReview review={review} />
          ) : (
            // `null` when the engine did not answer, so the card can say that
            // rather than blaming a worker it could not ask about. `?? false`
            // here would have reported a stopped worker on every failed request.
            <NoReviewYet workerRunning={setup === null ? null : setup.worker_running} />
          )}
        </section>

        <section className="pb-10">
          <BigNumber
            label="Views"
            value={total >= 1000 ? `${(total / 1000).toFixed(0)}k` : String(total)}
            delta={change(viewsSeries)}
            series={viewsSeries}
          />
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            The two most recent days are provisional — YouTube&apos;s data lags 24–48
            hours.
          </p>
        </section>

        <section className="grid gap-4 sm:grid-cols-3">
          <StatTile
            label="Click-through rate"
            value={`${ctrSeries.at(-1) ?? 0}%`}
            delta={change(ctrSeries)}
            series={ctrSeries}
            highlight
          />
          <StatTile
            label="Average view duration"
            value={`${Math.floor(lastAvd / 60)}:${String(Math.round(lastAvd) % 60).padStart(2, "0")}`}
            delta={change(avdSeries)}
            series={avdSeries}
          />
          <StatTile
            label="Subscribers gained"
            value={subsSeries.reduce((a, b) => a + b, 0).toLocaleString()}
            delta={change(subsSeries)}
            series={subsSeries}
          />
        </section>

        {/* The payoff of the whole system: what it has learned, in sentences. */}
        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold text-[var(--color-muted)]">
              What&apos;s working
              <LiveBadge live={findingsLive} />
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {confirmed.length} of {findings.length} confirmed
            </span>
          </div>

          <div className="mt-3 grid gap-2.5">
            {findings.length === 0 && (
              <Card className="px-5 py-4 text-[13px] text-[var(--color-faint)]">
                Nothing yet — findings appear once enough published videos share a
                title strategy, hook device or thumbnail concept to compare.
              </Card>
            )}
            {findings.map((f) => (
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

          {skipped.length > 0 && (
            <div className="mt-3 rounded-[var(--radius-card)] border border-dashed border-[var(--color-line)] px-4 py-3">
              <p className="text-[12px] font-semibold text-[var(--color-muted)]">
                Not enough data to compare
              </p>
              {skipped.map((s) => (
                <p key={s} className="mono mt-1 text-[11px] text-[var(--color-faint)]">
                  {s}
                </p>
              ))}
            </div>
          )}
        </section>

        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold text-[var(--color-muted)]">
              Retention · {retentionLive ? retentionTarget?.title : "Why bridges collapse"}
              <LiveBadge live={retentionLive} />
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {retentionLive ? avdLabel(retentionTarget?.avd_seconds ?? 0) : "8:02"}
            </span>
          </div>

          <Card className="mt-3 p-6">
            <RetentionMap curve={curve} beats={curveBeats} />
          </Card>

          <div className="mt-3 grid gap-1.5">
            {beatRows.map((beat) => (
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
                      width: `${Math.min(100, (beat.drop / 25) * 100)}%`,
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
          <h2 className="flex items-center gap-2 text-[13px] font-semibold text-[var(--color-muted)]">
            Worth cutting into a Short
            <LiveBadge live={cutsLive} />
          </h2>
          <Card className="mt-3 p-6">
            <ShortCuts cuts={cuts} />
          </Card>
          <p className="mt-3 max-w-[64ch] text-[12px] leading-relaxed text-[var(--color-faint)]">
            Ranked by how far each stretch rises above the video&apos;s own retention
            decay — not by raw retention, which is highest in the first ten seconds
            of every video ever made and would pick the intro every time.
          </p>
        </section>

        <section className="mt-10">
          <details className="group" open={published.length > 0}>
            <summary className="cursor-pointer list-none text-[13px] font-semibold text-[var(--color-muted)] hover:text-[var(--color-ink)]">
              Per-video breakdown
              <span className="ml-2 text-[var(--color-faint)] group-open:hidden">
                show
              </span>
            </summary>
            {published.length === 0 ? (
              <Card className="mt-3 p-5 text-[13px] text-[var(--color-faint)]">
                Populated from the YouTube Analytics API once a channel is connected
                and a video has been published.
              </Card>
            ) : (
              <Card className="mt-3 overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-[var(--color-line)] text-left text-[11px] uppercase text-[var(--color-faint)]">
                      <th className="px-4 py-2.5 font-semibold">Video</th>
                      <th className="px-4 py-2.5 text-right font-semibold">Views</th>
                      <th className="px-4 py-2.5 text-right font-semibold">CTR</th>
                      <th className="px-4 py-2.5 text-right font-semibold">AVD</th>
                      <th className="px-4 py-2.5 font-semibold">Title strategy</th>
                      <th className="px-4 py-2.5 font-semibold">Thumbnail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {published.map((v) => (
                      <tr
                        key={v.video_id}
                        className="border-b border-[var(--color-line)] last:border-0"
                      >
                        <td className="max-w-[32ch] truncate px-4 py-2.5">{v.title}</td>
                        <td className="mono px-4 py-2.5 text-right">
                          {v.views.toLocaleString()}
                        </td>
                        <td className="mono px-4 py-2.5 text-right">
                          {(v.ctr * 100).toFixed(1)}%
                        </td>
                        <td className="mono px-4 py-2.5 text-right">
                          {avdLabel(v.avd_seconds)}
                        </td>
                        <td className="px-4 py-2.5 text-[12px] text-[var(--color-muted)]">
                          {v.title_strategy || "—"}
                        </td>
                        <td className="px-4 py-2.5 text-[12px] text-[var(--color-muted)]">
                          {v.thumbnail_concept || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          </details>
        </section>
      </Page>
    </>
  );
}

/** Percentage change of the recent half of a series against the earlier half —
 *  derivable from the window we actually have, rather than an invented delta. */
function change(series: number[]): number {
  if (series.length < 4) return 0;
  const half = Math.floor(series.length / 2);
  const prev = series.slice(0, half).reduce((a, b) => a + b, 0);
  const recent = series.slice(-half).reduce((a, b) => a + b, 0);
  if (prev <= 0) return 0;
  return Math.round(((recent - prev) / prev) * 1000) / 10;
}

function avdLabel(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function VerdictBadge({
  verdict,
}: {
  verdict: "confirmed" | "suggestive" | "insufficient";
}) {
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
