"use client";

import { useMemo, useState } from "react";
import {
  DAILY_LIMIT,
  MAX_PER_DAY,
  PUBLISH_COST,
  autoSchedule,
  bestHourOn,
  dayKey,
  fmtTime,
  slotReason,
  slotScore,
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
}: {
  videos: CalendarVideo[];
  quotaByDay: Record<string, number>;
}) {
  const [scheduled, setScheduled] = useState<Scheduled[]>([]);
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
  const weeks = useMemo(() => {
    const today = new Date();
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
  }, []);

  function drop(day: Date) {
    if (!dragging) return;
    setHoverDay(null);

    const others = scheduled.filter((s) => s.videoId !== dragging);
    const at = bestHourOn(day, others);
    if (!at) {
      setNotice({ tone: "bad", text: "no free hour left on that day" });
      return;
    }

    const { ok, message } = validateMove(at, others, quotaByDay);
    if (!ok) {
      setNotice({ tone: "bad", text: message });
      return;
    }

    setScheduled([...others, { videoId: dragging, at }]);
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
    setScheduled(scheduled.filter((s) => s.videoId !== videoId));
    setNotice(null);
  }

  function proposePlan() {
    const pending = tray.map((v) => ({ id: v.id, format: v.format }));
    setPlan(autoSchedule(pending, scheduled, quotaByDay));
  }

  function applyPlan() {
    if (!plan) return;
    setScheduled([
      ...scheduled,
      ...plan.assignments.map((a) => ({ videoId: a.videoId, at: a.at })),
    ]);
    setNotice({ tone: "ok", text: `${plan.assignments.length} videos scheduled` });
    setPlan(null);
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
                    const budget = Math.floor((DAILY_LIMIT - used) / PUBLISH_COST);
                    const full = items.length >= Math.min(budget, MAX_PER_DAY);
                    const past = day < new Date(new Date().setHours(0, 0, 0, 0));
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
                        width: `${Math.min(100, (weekQuota / (DAILY_LIMIT * 7)) * 100)}%`,
                        background:
                          weekQuota > DAILY_LIMIT * 5
                            ? "var(--color-warn)"
                            : "var(--color-muted)",
                      }}
                    />
                  </div>
                  <span className="mono text-[11px] text-[var(--color-faint)]">
                    {weekQuota.toLocaleString()} / {(DAILY_LIMIT * 7).toLocaleString()}{" "}
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
