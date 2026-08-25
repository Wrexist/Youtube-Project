"use client";

import { useEffect, useMemo } from "react";

/** How long the whole moment lasts. Long enough to land, short enough that it
 *  never delays the approval gate it celebrates reaching. */
const DURATION_MS = 2600;

/** One confetti burst and a toast, for the moments that earn one — a finished
 *  render, a publish. The play layer's rule (docs/UI-DESIGN.md): celebration is
 *  a *moment*, never chrome — it plays once, disappears completely, and blocks
 *  nothing (`pointer-events-none` throughout). Under `prefers-reduced-motion`
 *  the global override collapses the confetti to nothing and the toast simply
 *  appears and goes. */
export function Celebration({
  label,
  detail,
  onDone,
}: {
  label: string;
  /** The reward line — "+100 XP", "Level 4". Playful, but never invented. */
  detail?: string;
  onDone: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDone, DURATION_MS);
    return () => clearTimeout(timer);
  }, [onDone]);

  // Deterministic per mount, random across mounts — computed once so re-renders
  // don't reshuffle mid-fall.
  const pieces = useMemo(
    () =>
      Array.from({ length: 24 }, (_, i) => ({
        left: Math.random() * 100,
        delay: Math.random() * 0.4,
        duration: 1.4 + Math.random() * 0.9,
        color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
        tilt: Math.random() * 360,
        drift: (Math.random() - 0.5) * 120,
      })),
    [],
  );

  return (
    <div aria-hidden="false" className="pointer-events-none fixed inset-0 z-50">
      <div aria-hidden className="absolute inset-0 overflow-hidden">
        {pieces.map((p, i) => (
          <span
            key={i}
            className="confetti-piece"
            style={{
              left: `${p.left}%`,
              background: p.color,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.duration}s`,
              ["--confetti-tilt" as string]: `${p.tilt}deg`,
              ["--confetti-drift" as string]: `${p.drift}px`,
            }}
          />
        ))}
      </div>

      <div
        role="status"
        aria-live="polite"
        className="toast-pop absolute inset-x-0 top-24 mx-auto w-fit rounded-[var(--radius-modal)] border border-[var(--color-line)] bg-[var(--color-raised)] px-5 py-3 text-center"
      >
        <p className="text-[14px] font-semibold">{label}</p>
        {detail && (
          <p className="mono mt-0.5 text-[12px] text-[var(--color-ok)]">{detail}</p>
        )}
      </div>
    </div>
  );
}

/** Token colors only — the burst is loud for two seconds, not off-palette. */
const CONFETTI_COLORS = [
  "var(--color-accent)",
  "var(--color-ok)",
  "var(--color-warn)",
  "var(--color-ink)",
];
