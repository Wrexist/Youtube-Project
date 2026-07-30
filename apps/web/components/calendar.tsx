"use client";

import { useMemo, useState, useTransition } from "react";
import { applyPlanToCalendar, scheduleAt, unscheduleAt } from "@/app/actions";
import {
  DAILY_LIMIT,
  PUBLISH_COST,
  autoSchedule,
  bestHourOn,
  dayKey,
  fmtTime,
  slotReason,
  slotScore,
  uploadsPerDay,
  validateMove,
  type Scheduled,
} from "@/lib/schedule";

export interface CalendarVideo {
  id: string;
  title: string;
  format: "short" | "long";
  duration: string;
}

/** Calendar with drag-to-schedule.
 *
 *  Three interactions:
 *    - drag a video out of the tray onto a day  → schedules it at that day's best hour
 *    - drag a scheduled chip to another day     → reschedules (50 units, effectively free)
 *    - drag a chip back to the tray             → unschedules
 *
 *  Auto-schedule proposes a plan and does not apply it. Placing a month of uploads
 *  is exactly the kind of action that should be reviewed before it happens.
 */
export function Calendar({
  videos,
  quotaByDay,
  initialScheduled = [],
  now,
  live = false,
  dailyLimit = DAILY_LIMIT,
}: {
  videos: CalendarVideo[];
  quotaByDay: Record<string, number>;
  /** Bookings the engine already holds. `GET /v1/calendar` returns these and the
   *  page destructured only `quota_by_day`, so an upload the engine had already
   *  booked was invisible here — and the same day could be double-booked against
   *  a ceiling this screen could not see. */
  initialScheduled?: Scheduled[];
  /** A reference instant from the server, as an ISO string.
   *
   *  The grid and the "is this day past" test both called `new Date()` during
   *  render, which differs between the server pass and the client pass and is a
   *  hydration mismatch by construction. */
  now?: string;
  /** With no engine, drops are refused rather than silently kept in memory. */
  live?: boolean;
  /** `quota.limit` from the engine. Hardcoding 10,000 here meant an install with an
   *  approved quota extension had its drags refused by a ceiling only this screen
   *  believed in. */
  dailyLimit?: number;
}) {
  const [scheduled, setScheduled] = useState<Scheduled[]>(initialScheduled);
  const [, startTransition] = useTransition();
  const [dragging, setDragging] = useState<string | null>(null);
  const [hoverDay, setHoverDay] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn" | "bad"; text: string } | null>(
    null,
  );
  const [plan, setPlan] = useState<ReturnType<typeof autoSchedule> | null>(null);

  const byId = useMemo(() => new Map(videos.map((v) => [v.id, v])), [videos]);
  const scheduledIds = new Set(scheduled.map((s) => s.videoId));
  const tray = videos.filter((v) => !scheduledIds.has(v.id));

  // A 4-week grid starting on the Monday of the current week.
  const today = useMemo(() => (now ? new Date(now) : new Date()), [now]);
  const startOfToday = useMemo(() => {
    const d = new Date(today);
    d.setHours(0, 0, 0, 0);
    return d;
  }, [today]);

  const weeks = useMemo(() => {
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    monday.setHours(0, 0, 0, 0);
    return [0, 1, 2, 3].map((w) =>
      [0, 1, 2, 3, 4, 5, 6].map((d) => {
        const date = new Date(monday);
        date.setDate(monday.getDate() + w * 7 + d);
        return date;
      }),
    );
  }, [today]);

  function drop(day: Date) {
    if (!dragging) return;
    setHoverDay(null);

    const others = scheduled.filter((s) => s.videoId !== dragging);
    const at = bestHourOn(day, others);
    if (!at) {
      setNotice({ tone: "bad", text: "no free hour left on that day" });
      return;
    }

    const { ok, message } = validateMove(at, others, quotaByDay, dailyLimit);
    if (!ok) {
      setNotice({ tone: "bad", text: message });
      return;
    }

    // Optimistic, then reverted if the engine refuses. The alternative — waiting
    // for the round trip — makes a drag feel broken on a slow connection.
    const previous = scheduled;
    setScheduled([...others, { videoId: dragging, at }]);
    persist(
      () => scheduleAt(dragging, at.toISOString()),
      previous,
      "could not schedule that video",
    );
    setNotice(
      message
        ? { tone: "warn", text: message }
        : {
            tone: "ok",
            text: `${byId.get(dragging)?.title} → ${at.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} at ${fmtTime(at)} · ${slotReason(at)}`,
          },
    );
    setDragging(null);
  }

  function unschedule(videoId: string) {
    const previous = scheduled;
    setScheduled(scheduled.filter((s) => s.videoId !== videoId));
    setNotice(null);
    persist(() => unscheduleAt(videoId), previous, "could not unschedule that video");
  }

  /**
   * Send a change and put the old state back if it is refused.
   *
   * Every mutation on this screen used to call `setScheduled` and stop there, so a
   * booking lasted until the next reload and nothing ever reached the engine. The
   * engine's 409 carries the reason — over quota, too close to another upload, in
   * the past — and it is shown verbatim rather than reduced to "failed".
   */
  function persist(
    action: () => Promise<{ ok: boolean; error?: string }>,
    previous: Scheduled[],
    fallback: string,
  ) {
    if (!live) {
      setScheduled(previous);
      setNotice({ tone: "bad", text: "The engine is not reachable — nothing was saved." });
      return;
    }
    startTransition(async () => {
      const result = await action();
      if (!result.ok) {
        setScheduled(previous);
        setNotice({ tone: "bad", text: result.error ?? fallback });
      }
    });
  }

  function proposePlan() {
    const pending = tray.map((v) => ({ id: v.id, format: v.format }));
    setPlan(autoSchedule(pending, scheduled, quotaByDay, { dailyLimit }));
  }

  function applyPlan() {
    if (!plan) return;
    const previous = scheduled;
    const assignments = plan.assignments;

    setScheduled([...scheduled, ...assignments.map((a) => ({ videoId: a.videoId, at: a.at }))]);
    setNotice({ tone: "ok", text: `${assignments.length} videos scheduled` });
    setPlan(null);

    // This one printed "N videos scheduled" having sent nothing at all.
    persist(
      () =>
        applyPlanToCalendar(
          assignments.map((a) => ({ video_id: a.videoId, at: a.at.toISOString() })),
        ),
      previous,
      "could not apply the plan",
    );
  }

  const proposedByDay = new Map<string, { videoId: string; at: Date; reason: string }[]>();
  for (const a of plan?.assignments ?? []) {
    const key = dayKey(a.at);
    proposedByDay.set(key, [...(proposedByDay.get(key) ?? []), a]);
  }

  return (
    <div className="flex flex-col gap-5 lg:flex-row">
      {/* Tray of finished, unscheduled videos. */}
      <aside className="lg:w-[248px] lg:shrink-0">
        <div className="flex items-baseline justify-between pb-2.5">
          <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
            Ready to publish
          </h2>
          <span className="mono text-[12px] text-[var(--color-faint)]">{tray.length}</span>
        </div>

        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => dragging && scheduledIds.has(dragging) && unschedule(dragging)}
          className="grid min-h-[120px] gap-2 rounded-[var(--radius-card)] border border-dashed border-[var(--color-line)] p-2"
        >
          {tray.length === 0 && (
            <p className="self-center px-2 py-6 text-center text-[12px] text-[var(--color-faint)]">
              Everything is scheduled. Drag a chip back here to unschedule it.
            </p>
          )}
          {tray.map((video) => (
            <article
              key={video.id}
              draggable
              onDragStart={() => setDragging(video.id)}
              onDragEnd={() => setDragging(null)}
              className={`cursor-grab rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-surface)] p-2 transition-opacity duration-150 active:cursor-grabbing ${
                dragging === video.id ? "opacity-40" : ""
              }`}
            >
              <div className="mb-1.5 h-12 rounded-sm bg-[var(--color-raised)]" />
              <p className="line-clamp-2 text-[12px] leading-tight font-semibold">
                {video.title}
              </p>
              <p className="mono mt-1 text-[10px] text-[var(--color-faint)]">
                {video.format === "long" ? "16:9" : "9:16"} · {video.duration}
              </p>
            </article>
          ))}
        </div>

        <div className="mt-3 grid gap-2">
          {plan ? (
            <>
              <button
                onClick={applyPlan}
                className="rounded-[var(--radius-btn)] bg-[var(--color-accent)] px-3 py-2 text-[13px] font-semibold text-white transition-all duration-150 hover:brightness-110"
              >
                Apply {plan.assignments.length} placements
              </button>
              <button
                onClick={() => setPlan(null)}
                className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-3 py-2 text-[13px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:text-[var(--color-ink)]"
              >
                Discard
              </button>
              {plan.unplaced.length > 0 && (
                <p className="text-[11px] leading-relaxed text-[var(--color-warn)]">
                  {plan.unplaced.length} couldn&apos;t be placed:{" "}
                  {plan.unplaced[0].reason}
                </p>
              )}
            </>
          ) : (
            <button
              onClick={proposePlan}
              disabled={tray.length === 0}
              className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-3 py-2 text-[13px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)] disabled:opacity-40"
            >
              Auto-schedule
            </button>
          )}
          <p className="text-[11px] leading-relaxed text-[var(--color-faint)]">
            Placements lead the audience peak by 2h, keep {20}h between uploads, and
            respect the daily quota. Timing is estimated until 28 days of analytics
            exist.
          </p>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <div className="grid grid-cols-7 gap-px pb-2 text-[11px] text-[var(--color-faint)]">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="px-2">
              {d}
            </div>
          ))}
        </div>

        <div className="grid gap-3">
          {weeks.map((week, wi) => {
            const weekQuota = week.reduce((sum, d) => sum + (quotaByDay[dayKey(d)] ?? 0), 0);
            return (
              <div key={wi}>
                <div className="grid grid-cols-7 gap-px overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-line)]">
                  {week.map((day) => {
                    const key = dayKey(day);
                    const items = scheduled.filter((s) => dayKey(s.at) === key);
                    const proposed = proposedByDay.get(key) ?? [];
                    const used = quotaByDay[key] ?? 0;
                    const budget = Math.floor((dailyLimit - used) / PUBLISH_COST);
                    const full = items.length >= Math.min(budget, uploadsPerDay(dailyLimit));
                    const past = day < startOfToday;
                    const active = hoverDay === key && !!dragging;

                    return (
                      <div
                        key={key}
                        onDragOver={(e) => {
                          if (!dragging || full || past) return;
                          e.preventDefault();
                          setHoverDay(key);
                        }}
                        onDragLeave={() => setHoverDay(null)}
                        onDrop={() => drop(day)}
                        className={`min-h-[112px] p-2 transition-colors duration-150 ${
                          past
                            ? "bg-[var(--color-bg)]"
                            : active
                              ? "bg-[var(--color-raised)] ring-1 ring-[var(--color-accent)] ring-inset"
                              : full && dragging
                                ? "bg-[var(--color-surface)] opacity-45"
                                : "bg-[var(--color-surface)]"
                        }`}
                      >
                        <div className="flex items-baseline justify-between">
                          <span
                            className={`mono text-[11px] ${past ? "text-[var(--color-faint)]/50" : "text-[var(--color-faint)]"}`}
                          >
                            {day.getDate()}
                          </span>
                          {dragging && !past && (
                            <span className="mono text-[9px] text-[var(--color-faint)]">
                              {full ? "full" : `${budget - items.length} left`}
                            </span>
                          )}
                        </div>

                        <div className="mt-1 grid gap-1">
                          {items.map((item) => {
                            const video = byId.get(item.videoId);
                            return (
                              <div
                                key={item.videoId}
                                draggable
                                onDragStart={() => setDragging(item.videoId)}
                                onDragEnd={() => setDragging(null)}
                                title={`${fmtTime(item.at)} · ${slotReason(item.at)} · score ${slotScore(item.at).toFixed(2)}`}
                                className="cursor-grab rounded border border-[var(--color-line-hover)] bg-[var(--color-raised)] px-1.5 py-1 active:cursor-grabbing"
                              >
                                <div className="mb-1 h-5 rounded-sm bg-[var(--color-line-hover)]" />
                                <p className="line-clamp-1 text-[10px] leading-tight text-[var(--color-muted)]">
                                  {video?.title}
                                </p>
                                <p className="mono text-[9px] text-[var(--color-faint)]">
                                  {fmtTime(item.at)}
                                </p>
                              </div>
                            );
                          })}

                          {/* Proposed placements render as outlines until applied. */}
                          {proposed.map((p) => (
                            <div
                              key={p.videoId}
                              title={p.reason}
                              className="rounded border border-dashed border-[var(--color-accent)] px-1.5 py-1"
                            >
                              <p className="line-clamp-1 text-[10px] leading-tight text-[var(--color-accent)]">
                                {byId.get(p.videoId)?.title}
                              </p>
                              <p className="mono text-[9px] text-[var(--color-faint)]">
                                {fmtTime(p.at)} · proposed
                              </p>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="flex items-center gap-3 pt-1.5">
                  <div className="h-1 w-28 overflow-hidden rounded-full bg-[var(--color-raised)]">
                    <div
                      className="h-full rounded-full transition-[width] duration-300"
                      style={{
                        width: `${Math.min(100, (weekQuota / (dailyLimit * 7)) * 100)}%`,
                        background:
                          weekQuota > dailyLimit * 5
                            ? "var(--color-warn)"
                            : "var(--color-muted)",
                      }}
                    />
                  </div>
                  <span className="mono text-[11px] text-[var(--color-faint)]">
                    {weekQuota.toLocaleString()} / {(dailyLimit * 7).toLocaleString()}{" "}
                    quota this week
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-4 min-h-[20px]" role="status" aria-live="polite">
          {notice && (
            <p
              className="text-[12px]"
              style={{
                color:
                  notice.tone === "bad"
                    ? "var(--color-bad)"
                    : notice.tone === "warn"
                      ? "var(--color-warn)"
                      : "var(--color-muted)",
              }}
            >
              {notice.text}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
