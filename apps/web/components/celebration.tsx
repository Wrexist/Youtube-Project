"use client";

import { useEffect } from "react";

/** How long the whole moment lasts. Long enough to land, short enough that it
 *  never delays the approval gate it celebrates reaching. */
const DURATION_MS = 2600;

interface Piece {
  left: number;
  delay: number;
  duration: number;
  color: string;
  tilt: number;
  drift: number;
}

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

  const pieces = PIECES;

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

/**
 * The scatter, computed once at module load from an irrational-number sequence.
 *
 * Deterministic rather than random, for three reasons that all point the same
 * way. `Math.random()` in a render pass is impure and React may re-run the pass,
 * which would reshuffle the pieces mid-fall; moving it to an effect trades that
 * for a `setState` in an effect, which the same lint config rejects; and a
 * deterministic scatter renders identically on the server and the client, so it
 * can never be a hydration mismatch.
 *
 * The golden-ratio and √2 multiples are the standard trick for a low-discrepancy
 * sequence: successive values never clump the way a small random sample does, so
 * 24 pieces cover the width evenly without any two landing on top of each other.
 * The result looks scattered and is in fact a fixed, designed pattern.
 */
const PIECES: Piece[] = Array.from({ length: 24 }, (_, i) => {
  const golden = (i * 0.6180339887) % 1;
  const root2 = (i * 0.4142135624) % 1;
  const root3 = (i * 0.7320508076) % 1;
  return {
    left: golden * 100,
    delay: root2 * 0.4,
    duration: 1.4 + root3 * 0.9,
    color: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
    tilt: golden * 360,
    drift: (root3 - 0.5) * 120,
  };
});
