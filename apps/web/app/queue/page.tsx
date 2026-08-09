import Link from "next/link";
import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { fileUrl, getJobs } from "@/lib/engine";
import { QUEUE, REVIEW_QUEUE } from "@/lib/demo";
import type { JobSummary } from "@studio/contracts";

/** Queue — what is running, and what is waiting on a human.
 *
 *  A single column, no filter sidebar, no table. The review section is the one that
 *  matters: nothing publishes unattended unless its series has auto-publish on *and*
 *  the checklist is clean, so this is where a human actually is in the loop.
 *
 *  Reads `GET /v1/jobs`. Until that endpoint existed this screen rendered
 *  `lib/demo.ts` unconditionally, so generating a video changed nothing here — on
 *  the screen you look at immediately after pressing Generate. With no engine it
 *  still falls back to the demo view, but now says so.
 *
 *  The buttons that were here — Approve, Fix, Open, Retry from here — are gone
 *  rather than left inert. None of them was wired to anything, and a button that
 *  does nothing is worse than no button: it says the system can do something it
 *  cannot. They come back with the endpoints behind them.
 */
export default async function QueuePage() {
  const jobs = await getJobs();
  const live = jobs !== null;

  const running = jobs?.filter((j) => j.status === "running") ?? [];
  const finished = jobs?.filter((j) => j.status !== "running") ?? [];

  return (
    <>
      <Header
        title="Queue"
        meta={
          <span className="flex items-center gap-2">
            {live
              ? `${running.length} running · ${finished.length} finished`
              : `${QUEUE.filter((j) => j.status === "running").length} running · ${REVIEW_QUEUE.length} awaiting review`}
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        {live ? (
          <section>
            <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
              Jobs
            </h2>
            {jobs.length === 0 ? (
              <Card className="p-6">
                <p className="text-[14px] text-[var(--color-muted)]">
                  Nothing here yet. Generating a video adds it to this list.
                </p>
              </Card>
            ) : (
              <div className="grid gap-2.5">
                {jobs.map((job) => (
                  <JobRow key={job.id} job={job} />
                ))}
              </div>
            )}
          </section>
        ) : (
          <DemoQueue />
        )}
      </Page>
    </>
  );
}

const TONE: Record<string, string> = {
  completed: "var(--color-ok)",
  failed: "var(--color-bad)",
  cancelled: "var(--color-faint)",
  interrupted: "var(--color-warn)",
};

function JobRow({ job }: { job: JobSummary }) {
  const total = job.stages_total || 1;
  const percent = Math.round((job.stages_done / total) * 100);

  // The whole row is the link. Jobs are persisted and every stage can be
  // re-run, but the id only ever lived in Create's client state — so a project
  // you navigated away from was gone, and this screen listed videos you had
  // paid to make with no way to open any of them. The note above about buttons
  // that do nothing applies just as well to a list that goes nowhere.
  return (
    <Link
      href={`/?job=${encodeURIComponent(job.id)}`}
      className="block rounded-[var(--radius-card)] transition-colors duration-150 hover:bg-[var(--color-raised)]"
    >
      <Card className="flex items-center gap-4 p-4">
        <div
          className={`h-14 w-24 shrink-0 overflow-hidden rounded-[var(--radius-btn)] ${
            job.status === "running" ? "skeleton" : "bg-[var(--color-raised)]"
          }`}
        >
          {job.thumbnail_keys?.[0] && (
            // eslint-disable-next-line @next/next/no-img-element -- served by the engine, not Next's optimiser
            <img
              src={fileUrl(job.thumbnail_keys[0])}
              alt=""
              className="h-full w-full object-cover"
            />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-semibold">{job.topic || "Untitled"}</p>
          <p className="mt-1 text-[12px] text-[var(--color-faint)]">
            {job.status === "running" ? (
              <>
                {job.current_stage ?? "starting"} · {job.stages_done}/{job.stages_total}
              </>
            ) : job.error ? (
              // The reason, not just the state. A bare "failed" is not something to
              // show someone who then has to decide what to do about it.
              <span style={{ color: TONE.failed }}>{job.error}</span>
            ) : (
              <span style={{ color: TONE[job.status] ?? "inherit" }}>{job.status}</span>
            )}
          </p>
          {job.status === "running" && (
            <div
              className="mt-2 h-0.5 w-full overflow-hidden rounded-full bg-[var(--color-raised)]"
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${job.topic || "Job"} progress`}
            >
              <div className="h-full bg-[var(--color-accent)]" style={{ width: `${percent}%` }} />
            </div>
          )}
        </div>

        <span className="mono shrink-0 text-[12px] text-[var(--color-faint)]">
          ${(job.cost_usd ?? 0).toFixed(2)}
        </span>
      </Card>
    </Link>
  );
}

/** The pre-engine view, kept intact so the design stays judgeable with nothing running. */
function DemoQueue() {
  return (
    <>
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
                    <p className="truncate text-[14px] font-semibold">{video.title}</p>
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
                        Checklist clear — sources cited, SEO grounded, thumbnail ready
                      </p>
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
            </Card>
          ))}
        </div>
      </section>
    </>
  );
}
