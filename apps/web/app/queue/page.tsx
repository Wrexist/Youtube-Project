import { Header, Page, Card, Button } from "@/components/ui";
import { QUEUE } from "@/lib/demo";

/** Queue. A single column of job cards — no filter sidebar, no table.
 *  A failed job always names the failing stage and offers a targeted retry;
 *  a bare "failed" is not an acceptable state to show. */
export default function QueuePage() {
  return (
    <>
      <Header
        title="Queue"
        meta={<span>{QUEUE.filter((j) => j.status === "running").length} running</span>}
      />
      <Page>
        <div className="flex gap-1 pb-5">
          {["All", "Running", "Needs review", "Failed"].map((f, i) => (
            <button
              key={f}
              className={`rounded-[var(--radius-btn)] px-3 py-1.5 text-[12px] font-semibold transition-colors duration-150 ${
                i === 0
                  ? "bg-[var(--color-raised)] text-[var(--color-ink)]"
                  : "text-[var(--color-muted)] hover:text-[var(--color-ink)]"
              }`}
            >
              {f}
            </button>
          ))}
        </div>

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
                  {job.status === "completed" && "Ready to publish"}
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
              {job.status === "completed" && <Button>Publish</Button>}
            </Card>
          ))}
        </div>
      </Page>
    </>
  );
}
