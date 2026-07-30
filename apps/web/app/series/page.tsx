import { Header, Page, Card, Button } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { SERIES, BACKLOG } from "@/lib/demo";

/** Series — standing instructions to keep making a kind of video at a rate.
 *
 *  Not a settings page. Three numbers per series and two switches; everything else
 *  is derived. The budget bar is the important element, because a series that has
 *  spent its month stops on its own and should say so before you wonder why it went
 *  quiet.
 *
 *  Read-only until the engine has a series table. The Pause/Resume and Edit buttons
 *  that were on each card are gone rather than left inert, following the same rule
 *  as the queue screen: a button that does nothing is worse than no button, because
 *  it claims the system can do something it cannot. "New series" survives as a
 *  disabled control with a reason on it — it is what says what this screen is for,
 *  and disabled-and-explained is not the same lie as live-and-inert.
 */
export default function SeriesPage() {
  return (
    <>
      <Header
        title="Series"
        action={
          <Button disabled title="Creating a series needs the series endpoint, which does not exist yet.">
            New series
          </Button>
        }
        meta={
          <span className="flex items-center gap-2">
            {SERIES.filter((s) => !s.paused).length} active
            {/* No endpoint serves series yet — the engine has no series table. */}
            <LiveBadge live={false} />
          </span>
        }
      />
      <Page>
        <div className="grid gap-3">
          {SERIES.map((s) => {
            const pct = Math.min(100, (s.spentThisMonth / s.monthlyBudget) * 100);
            const target = s.shortsPerWeek + s.longPerWeek;
            const thin = s.backlogDepth < target;
            const tight = pct > 80;

            return (
              <Card key={s.id} className="p-5">
                <div className="flex flex-wrap items-start gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-[15px] font-semibold">{s.name}</h2>
                      {s.paused && <Tag tone="muted">paused</Tag>}
                      {s.autoPublish && <Tag tone="accent">auto-publish</Tag>}
                    </div>
                    <p className="mt-1 text-[12px] text-[var(--color-faint)]">
                      {s.niche}
                    </p>

                    <p className="mono mt-3 text-[12px] text-[var(--color-muted)]">
                      {s.shortsPerWeek} shorts + {s.longPerWeek} long-form / week ·{" "}
                      {s.producedThisWeek}/{target} made this week
                    </p>

                    <div className="mt-3 flex items-center gap-3">
                      <div className="h-1 w-40 overflow-hidden rounded-full bg-[var(--color-raised)]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${pct}%`,
                            background: tight
                              ? "var(--color-warn)"
                              : "var(--color-muted)",
                          }}
                        />
                      </div>
                      <span className="mono text-[11px] text-[var(--color-faint)]">
                        ${s.spentThisMonth.toFixed(2)} / ${s.monthlyBudget} this month
                      </span>
                    </div>

                    {thin && (
                      <p className="mt-2.5 text-[12px] text-[var(--color-warn)]">
                        Backlog has {s.backlogDepth} ideas but the cadence needs{" "}
                        {target}. It will produce fewer rather than reach for a weak
                        topic.
                      </p>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>

        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              Backlog · Engineering failures
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {BACKLOG.filter((b) => !b.duplicate).length} usable
            </span>
          </div>

          <div className="mt-3 grid gap-1.5">
            {BACKLOG.map((idea) => (
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
                      demand {idea.demand.toFixed(2)} · fit {idea.fit.toFixed(2)}
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

function Tag({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone: "muted" | "accent";
}) {
  return (
    <span
      className="mono rounded-full border px-2 py-0.5 text-[10px] uppercase"
      style={{
        color: tone === "accent" ? "var(--color-accent)" : "var(--color-faint)",
        borderColor:
          tone === "accent" ? "var(--color-accent)" : "var(--color-line-hover)",
      }}
    >
      {children}
    </span>
  );
}
