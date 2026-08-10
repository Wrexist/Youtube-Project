import { Card } from "@/components/ui";
import type { Review, ReviewChange } from "@studio/contracts";

/**
 * What changed in what the system believes, since last week.
 *
 * The cron has run every Monday at 06:00 UTC since it was written and nothing
 * could read the result: the payload went into arq's result store, which keeps
 * results for an hour, and the only alternative was to run a fresh review — which
 * consumes the baseline the real weekly diff compares against, so looking at this
 * week's destroyed next week's.
 *
 * **Quiet weeks are the normal case and the card says so.** `worth_reading` is
 * false most weeks by design — the sample sizes here move slowly, `MIN_PER_GROUP`
 * is eight — and a card that manufactures an insight every Monday to look busy is
 * worse than no card, because it teaches you to skip it. So a week with no changes
 * gets one sentence and no list.
 *
 * The changes are already sentences when they arrive. `Change.sentence()` in the
 * engine composes them, including the difficult ones — a reversal reads as a
 * contradiction rather than as an add and a remove — and re-deriving that here
 * would be two implementations of the same judgement, drifting.
 */
export function WeeklyReview({ review }: { review: Review }) {
  const when = new Date(review.generated_at);
  const changes = review.changes ?? [];

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[15px] font-semibold">Since last week</h2>
        <span className="mono text-[12px] text-[var(--color-faint)]">
          {/* A fixed locale, not the reader's. A date rendered on the server in one
              locale and hydrated in another is a React mismatch, and this is a
              Server Component. */}
          {when.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ·{" "}
          {review.video_count} video{review.video_count === 1 ? "" : "s"}
        </span>
      </div>

      {review.is_first ? (
        // Distinct from a quiet week on purpose. Both have no changes and they mean
        // opposite things: this one has nothing to compare against yet.
        <p className="mt-3 text-[13px] leading-relaxed text-[var(--color-muted)]">
          First review — there is nothing to compare against yet.{" "}
          {review.confirmed_count > 0
            ? `${review.confirmed_count} finding${review.confirmed_count === 1 ? " is" : "s are"} already confirmed.`
            : "Nothing is confirmed yet."}
        </p>
      ) : changes.length === 0 ? (
        <p className="mt-3 text-[13px] leading-relaxed text-[var(--color-muted)]">
          Nothing changed. The findings below are the same ones as last week — which
          is what most weeks look like, and is not a failure to learn.
        </p>
      ) : (
        <ul className="mt-4 grid gap-2.5">
          {changes.map((change, i) => (
            <li key={`${change.kind}-${i}`} className="flex items-start gap-3">
              <ChangeBadge kind={change.kind} />
              <p className="min-w-0 flex-1 text-[13px] leading-relaxed">
                {change.sentence}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * What kind of change this is, as a word.
 *
 * A word rather than a coloured dot: `docs/UI-DESIGN.md` requires every state to be
 * readable without colour, and "reversed" is the one a reader must not miss — it
 * means the system is contradicting itself and both sides should be distrusted.
 */
function ChangeBadge({ kind }: { kind: ReviewChange["kind"] }) {
  const tone: Record<string, string> = {
    promoted: "var(--color-ok)",
    appeared: "var(--color-ok)",
    demoted: "var(--color-warn)",
    disappeared: "var(--color-faint)",
    reversed: "var(--color-bad)",
  };

  return (
    <span
      className="mono mt-0.5 w-[86px] shrink-0 text-[11px] uppercase tracking-wide"
      style={{ color: tone[kind] ?? "var(--color-faint)" }}
    >
      {kind}
    </span>
  );
}

/** Shown in place of the card when no review has ever been stored. */
export function NoReviewYet({ workerRunning }: { workerRunning: boolean }) {
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">Since last week</h2>
      <p className="mt-3 text-[13px] leading-relaxed text-[var(--color-muted)]">
        No review has run yet. It runs Monday at 06:00 UTC and reports what changed
        in what the system believes.{" "}
        {workerRunning ? (
          <>It will run on the next Monday after this install has published videos.</>
        ) : (
          // The honest reason, not a generic empty state. The cron is an arq job, so
          // with no worker it has never fired and never will — and nothing else in
          // the product says so.
          <>
            It needs the render worker, which is not running — a review cannot fire
            without one. See <span className="mono text-[12px]">README.md</span>,
            &ldquo;Durable renders&rdquo;.
          </>
        )}
      </p>
    </Card>
  );
}
