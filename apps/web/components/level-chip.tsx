"use client";

import { useEffect, useRef, useState } from "react";

import { getJobs } from "@/lib/engine";
import { computeProgress, type StudioProgress } from "@/lib/progress";

/** The play layer's one piece of persistent chrome: a small level chip at the
 *  bottom of the rail. Everything it shows is derived from real jobs — see
 *  docs/UI-DESIGN.md § The play layer for the rules it lives under. Renders
 *  nothing until the engine has answered: a level computed from demo data would
 *  be a score for work nobody did. */
export function LevelChip({ expanded }: { expanded: boolean }) {
  const [progress, setProgress] = useState<StudioProgress | null>(null);
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getJobs().then((jobs) => {
      if (!cancelled && jobs) setProgress(computeProgress(jobs));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Escape and click-away both close the achievements panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  if (!progress) return null;

  const span = progress.nextLevel - progress.levelFloor;
  const into = progress.xp - progress.levelFloor;
  const pct = span > 0 ? Math.min(100, Math.round((into / span) * 100)) : 0;
  const earned = progress.achievements.filter((a) => a.earned);

  return (
    <div ref={panelRef} className="relative px-3 pb-4">
      {open && (
        <div className="absolute bottom-full left-3 z-20 mb-2 w-64 rounded-[var(--radius-modal)] border border-[var(--color-line)] bg-[var(--color-raised)] p-4">
          <p className="text-[13px] font-semibold">
            Level {progress.level} · {progress.title}
          </p>
          <p className="mono mt-1 text-[11px] text-[var(--color-faint)]">
            {progress.xp} XP · {progress.nextLevel - progress.xp} to next level
          </p>
          <ul className="mt-3 grid gap-2">
            {progress.achievements.map((a) => (
              <li key={a.id} className="flex items-start gap-2.5">
                <span
                  aria-hidden
                  className="mono mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border text-[10px]"
                  style={{
                    color: a.earned ? "var(--color-ok)" : "var(--color-faint)",
                    borderColor: a.earned ? "var(--color-ok)" : "var(--color-line)",
                  }}
                >
                  {a.earned ? "✓" : ""}
                </span>
                <div className="min-w-0">
                  <p
                    className="text-[12px] font-semibold"
                    style={{ color: a.earned ? undefined : "var(--color-faint)" }}
                  >
                    {a.title}
                  </p>
                  <p className="text-[11px] leading-snug text-[var(--color-faint)]">
                    {a.detail}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`Level ${progress.level}, ${progress.title}. ${earned.length} of ${progress.achievements.length} achievements. Show details`}
        className="flex w-full items-center gap-3 rounded-[var(--radius-card)] px-2 py-2 transition-colors duration-150 hover:bg-[var(--color-raised)]"
      >
        <span className="mono flex size-6 shrink-0 items-center justify-center rounded-full border border-[var(--color-line-hover)] text-[11px] font-semibold">
          {progress.level}
        </span>
        {expanded && (
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-[12px] font-semibold">
              {progress.title}
            </span>
            <span
              className="mt-1 block h-1 overflow-hidden rounded-full bg-[var(--color-raised)]"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Progress to next level"
            >
              <span
                className="block h-full rounded-full bg-[var(--color-ok)] transition-[width] duration-400"
                style={{ width: `${pct}%` }}
              />
            </span>
          </span>
        )}
      </button>
    </div>
  );
}
