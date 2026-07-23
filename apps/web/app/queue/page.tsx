import { Header, Page, Card, Button } from "@/components/ui";
import { QUEUE, REVIEW_QUEUE } from "@/lib/demo";

/** Queue — running jobs, then the review gate.
 *
 *  A single column, no filter sidebar, no table. The review section is the one that
 *  matters: nothing publishes unattended unless its series has auto-publish on *and*
 *  the checklist is clean, so this is where a human actually is in the loop.
 *
 *  Every blocker states its reason. A bare "blocked" is not an acceptable state to
 *  show someone.
 */
export default function QueuePage() {
  const running = QUEUE.filter((j) => j.status === "running").length;
  const clear = REVIEW_QUEUE.filter((v) => v.blockers.length === 0);

  return (
    <>
      <Header
        title="Queue"
        meta={
          <span>
            {running} running · {REVIEW_QUEUE.length} awaiting review
          </span>
        }
        action={
          clear.length > 0 ? (
            <Button>Approve {clear.length} clear</Button>
          ) : undefined
        }
      />
      <Page>
        <section>
          <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
            Needs review
          </h2>
          <div className="grid gap-2.5">
            {REVIEW_QUEUE.map((video) => {
              const blocked = video.blockers.length > 0;
              return (
                <Card key={video.id} className="p-4">
                  <div className="flex items-start gap-4">
                    <div
                      className={`h-14 w-24 shrink-0 rounded-[var(--radius-btn)] bg-[var(--color-raised)] ${blocked ? "opacity-50" : ""}`}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-semibold">
                        {video.title}
                      </p>
                      <p className="mono mt-1 text-[12px] text-[var(--color-faint)]">
                        {video.series} · ${video.cost.toFixed(2)}
                      </p>

                      {blocked ? (
                        <ul className="mt-2.5 grid gap-1.5">
                          {video.blockers.map((b) => (
                            <li
                              key={b.code}
                              className="flex items-start gap-2 text-[12px] text-[var(--color-warn)]"
                            >
                              <span className="mono shrink-0" aria-hidden>
                                !
                              </span>
                              <span>{b.message}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-2 text-[12px] text-[var(--color-ok)]">
                          Checklist clear — sources cited, SEO grounded, thumbnail
                          ready
                        </p>
                      )}
                    </div>

                    <div className="flex shrink-0 gap-2">
                      <Button variant="ghost">Open</Button>
                      {blocked ? (
                        <Button variant="ghost">Fix</Button>
                      ) : (
                        <Button>Approve</Button>
                      )}
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </section>

        <section className="mt-10">
          <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
            In progress
          </h2>
          <div className="grid gap-2.5">
            {QUEUE.map((job) => (
              <Card key={job.id} className="flex items-center gap-4 p-4">
                <div
                  className={`h-14 w-24 shrink-0 rounded-[var(--radius-btn)] ${
                    job.status === "running" ? "skeleton" : "bg-[var(--color-raised)]"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-semibold">{job.topic}</p>
                  <p className="mt-1 text-[12px] text-[var(--color-faint)]">
                    {job.status === "running" && "Voiceover · 62%"}
                    {job.status === "completed" && "Ready for review"}
                    {job.status === "failed" && (
                      <span className="text-[var(--color-bad)]">
                        Failed at Materials — no footage found for beat 7
                      </span>
                    )}
                  </p>
                </div>
                <span className="mono shrink-0 text-[12px] text-[var(--color-faint)]">
                  ${job.cost_usd.toFixed(2)}
                </span>
                {job.status === "failed" && (
                  <Button variant="ghost">Retry from here</Button>
                )}
              </Card>
            ))}
          </div>
        </section>
      </Page>
    </>
  );
}
