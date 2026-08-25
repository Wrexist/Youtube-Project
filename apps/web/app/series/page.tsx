import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { getBacklog, getSeries, getSeriesPlan } from "@/lib/engine";
import { SERIES, BACKLOG } from "@/lib/demo";
import { NewSeriesForm, SeriesCards, type SeriesView } from "./series-view";

/** Series — standing instructions to keep making a kind of video at a rate.
 *
 *  Not a settings page. Three numbers per series and two switches; everything else
 *  is derived. The budget bar is the important element, because a series that has
 *  spent its month stops on its own and should say so before you wonder why it went
 *  quiet.
 *
 *  Live against `/v1/series` — the endpoint whose absence this screen spent its
 *  demo era apologising for. Each active card's warning line now comes from the
 *  run planner itself (`/v1/series/{id}/plan`), so "backlog too thin" is the
 *  planner's own verdict rather than a client-side guess at one.
 */
export default async function SeriesPage() {
  const [series, backlog] = await Promise.all([getSeries(), getBacklog(8)]);
  const live = series !== null;

  const cards: SeriesView[] = live
    ? await Promise.all(
        series.map(async (s) => {
          // The planner's verdict for this week. Skipped for paused series —
          // "it is paused" is already on the card as a tag.
          const plan = s.paused ? null : await getSeriesPlan(s.id);
          return {
            id: s.id,
            name: s.name,
            niche: s.niche,
            shortsPerWeek: s.shorts_per_week,
            longPerWeek: s.long_per_week,
            autoPublish: s.auto_publish,
            paused: s.paused,
            monthlyBudget: s.monthly_budget_usd,
            spentThisMonth: s.spent_this_month_usd,
            backlogDepth: s.backlog_depth,
            producedThisWeek: s.produced_this_week,
            blockers: (plan?.blocked ?? []).map((b) => ({
              code: b.code,
              message: b.message,
            })),
          };
        }),
      )
    : SERIES.map((s) => ({
        ...s,
        blockers:
          s.backlogDepth < s.shortsPerWeek + s.longPerWeek
            ? [
                {
                  code: "thin_backlog",
                  message: `Backlog has ${s.backlogDepth} ideas but the cadence needs ${s.shortsPerWeek + s.longPerWeek}. It will produce fewer rather than reach for a weak topic.`,
                },
              ]
            : [],
      }));

  const ideas = live
    ? (backlog?.ideas ?? []).map((i) => ({
        topic: i.topic,
        score: i.score,
        demand: i.demand,
        fit: 0,
        duplicate: null as string | null,
        similarity: 0,
      }))
    : BACKLOG;

  return (
    <>
      <Header
        title="Series"
        action={<NewSeriesForm live={live} />}
        meta={
          <span className="flex items-center gap-2">
            {cards.filter((s) => !s.paused).length} active
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        {cards.length === 0 ? (
          <Card className="p-8 text-center">
            <p className="text-[14px] font-semibold">No series yet</p>
            <p className="mx-auto mt-2 max-w-[48ch] text-[12px] leading-relaxed text-[var(--color-faint)]">
              A series is a repeatable format with its own cadence and budget —
              &ldquo;3 shorts a week about engineering failures&rdquo;. Create one
              and the backlog, the planner and the budget bars all attach to it.
            </p>
          </Card>
        ) : (
          <SeriesCards series={cards} live={live} />
        )}

        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              Backlog
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {ideas.filter((b) => !b.duplicate).length} usable
            </span>
          </div>

          <div className="mt-3 grid gap-1.5">
            {ideas.length === 0 && (
              <Card className="px-4 py-3 text-[12px] text-[var(--color-faint)]">
                Nothing researched yet — ideas land here as the suggestion engine
                and channel launches propose them.
              </Card>
            )}
            {ideas.map((idea) => (
              <Card
                key={idea.topic}
                className={`flex items-center gap-4 px-4 py-3 ${idea.duplicate ? "opacity-60" : ""}`}
              >
                <span className="mono w-10 shrink-0 text-[13px] text-[var(--color-muted)]">
                  {idea.duplicate ? "—" : idea.score.toFixed(2)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px]">{idea.topic}</p>
                  {idea.duplicate ? (
                    <p className="mt-0.5 text-[11px] text-[var(--color-warn)]">
                      {Math.round((idea.similarity ?? 0) * 100)}% overlap with “
                      {idea.duplicate}” — rejected as a duplicate
                    </p>
                  ) : (
                    <p className="mono mt-0.5 text-[11px] text-[var(--color-faint)]">
                      demand {idea.demand.toFixed(2)}
                      {idea.fit > 0 && ` · fit ${idea.fit.toFixed(2)}`}
                    </p>
                  )}
                </div>
              </Card>
            ))}
          </div>

          <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-faint)]">
            Duplicates are kept visible rather than dropped. An automated channel&apos;s
            most likely failure isn&apos;t running dry — it&apos;s making the same video
            four times in different words.
          </p>
        </section>
      </Page>
    </>
  );
}
