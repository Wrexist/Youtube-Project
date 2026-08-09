import type { Monetisation } from "@studio/contracts";

/** Progress toward the Partner Programme.
 *
 *  This is the number the whole product is aimed at, and until now no screen showed
 *  it. It goes at the top of Analytics, above views, because "am I getting paid yet"
 *  outranks "how did last week go".
 *
 *  Deliberately two bars and a sentence, not a panel of gauges. Both routes are
 *  reported by the engine, but only the one actually in play is drawn — showing a
 *  10-million-view Shorts bar sitting at 0.001% next to a healthy watch-hours bar
 *  is the cockpit `docs/UI-DESIGN.md` rules out, and it makes the real progress
 *  look worse than it is.
 */

type Threshold = Monetisation["subscribers"];

function Bar({ threshold }: { threshold: Threshold }) {
  const pct = Math.round(threshold.fraction * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-[var(--color-dim)]">
          {threshold.name}
        </span>
        <span className="tabular-nums text-[13px]">
          <span className={threshold.met ? "text-[var(--color-ok)]" : ""}>
            {Math.round(threshold.current).toLocaleString()}
          </span>
          <span className="text-[var(--color-faint)]">
            {" / "}
            {Math.round(threshold.target).toLocaleString()}
          </span>
        </span>
      </div>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--color-line)]"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${threshold.name}: ${pct}% of ${Math.round(threshold.target).toLocaleString()}`}
      >
        <div
          className={`h-full rounded-full transition-[width] ${
            threshold.met ? "bg-[var(--color-ok)]" : "bg-[var(--color-accent)]"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function eta(days: number | null | undefined): string | null {
  if (days == null) return null;
  if (days <= 31) return `about ${days} day${days === 1 ? "" : "s"} at this rate`;
  const months = Math.round(days / 30);
  return `about ${months} month${months === 1 ? "" : "s"} at this rate`;
}

export function MonetisationCard({ data }: { data: Monetisation }) {
  // Only the route in play. `route` is the engine's call, made on whichever is
  // further along by fraction of its own target.
  const content = data.route === "shorts" ? data.shorts_views : data.watch_hours;

  // Only when *nothing* is still outstanding except this bar.
  //
  // This used to be `eta(content.days_remaining) ?? eta(subscribers.days_remaining)`,
  // which was wrong twice over. The fallback was dead — a subscriber count is a
  // running total, so the engine never projects one and that side was always null.
  // Worse, the first half showed the watch-hours date even when subscribers were the
  // blocker: a channel at 4,000 hours and 50 subscribers was told "about 2 months",
  // when the thing actually standing in the way was years out. `blocking` exists to
  // answer this and was not being read.
  const onlyThisBarLeft = data.subscribers.met || content.met;
  const projection = onlyThisBarLeft ? eta(content.days_remaining) : null;

  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--color-line)] p-5">
      <div className="flex items-baseline justify-between gap-3">
        {/* 600, not 500. UI-DESIGN.md allows two weights and this was the only
            `font-medium` in the app — a third weight nobody else uses reads as a
            slightly-wrong heading rather than as a deliberate one. */}
        <h2 className="text-[15px] font-semibold">
          {data.eligible ? "Eligible for monetisation" : "Monetisation"}
        </h2>
        {!data.eligible && projection && (
          <span className="text-[12px] text-[var(--color-faint)]">{projection}</span>
        )}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {/* Not a Bar. The engine sends 0 as a placeholder for a hidden count, and
            a bar reading "0 / 1,000" states a fact about the channel that nobody
            knows — directly contradicting the note below it. */}
        {data.subscriber_count_hidden ? (
          <p className="self-center text-[13px] text-[var(--color-dim)]">
            Subscriber count unavailable
          </p>
        ) : (
          <Bar threshold={data.subscribers} />
        )}
        <Bar threshold={content} />
      </div>

      {!data.eligible && data.blocking.length > 0 && (
        <p className="mt-4 text-[13px] text-[var(--color-dim)]">
          Held up by {data.blocking.join(" and ")}.
        </p>
      )}

      {/* Said once, plainly. Both figures can read higher than Google's own count,
          and a partial window is not a smaller version of the real number. */}
      {data.caveat && (
        <p className="mt-4 text-[12px] text-[var(--color-faint)]">{data.caveat}</p>
      )}

      {data.subscriber_count_hidden && (
        <p className="mt-2 text-[12px] text-[var(--color-faint)]">
          This channel hides its subscriber count, so that figure is unavailable —
          it still counts toward the threshold.
        </p>
      )}
    </div>
  );
}
