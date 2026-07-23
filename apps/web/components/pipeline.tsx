"use client";

import { useState } from "react";
import type { Stage, StageStatus, Variant } from "@/lib/types";

/** The stage row — the pipeline primitive.
 *
 *  Four states, each distinguishable without colour (the glyph carries the meaning,
 *  the colour only reinforces it). A completed row collapses to one informative line
 *  and expands on click to show and edit what it produced. */
export function StageRow({
  stage,
  expanded,
  onToggle,
  onRerun,
}: {
  stage: Stage;
  expanded: boolean;
  onToggle: () => void;
  onRerun: (name: string) => void;
}) {
  const interactive = stage.status === "done" || stage.status === "failed";

  return (
    <li className="rise border-b border-[var(--color-line)] last:border-0">
      <button
        onClick={interactive ? onToggle : undefined}
        aria-expanded={interactive ? expanded : undefined}
        disabled={!interactive}
        className={`flex w-full items-center gap-4 px-4 py-3.5 text-left transition-colors duration-150 ${
          interactive ? "hover:bg-[var(--color-raised)]" : "cursor-default"
        }`}
      >
        <StatusGlyph status={stage.status} />

        <span
          className={`w-[132px] shrink-0 text-[13px] font-semibold ${
            stage.status === "pending" ? "text-[var(--color-faint)]" : ""
          }`}
        >
          {stage.title}
        </span>

        <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--color-muted)]">
          {stage.status === "running" ? (
            <RunningBar />
          ) : stage.status === "failed" ? (
            <span className="text-[var(--color-bad)]">{stage.error ?? "failed"}</span>
          ) : (
            (stage.summary ?? "")
          )}
        </span>

        {stage.cost_usd > 0 && (
          <span className="mono shrink-0 text-[11px] text-[var(--color-faint)]">
            ${stage.cost_usd.toFixed(2)}
          </span>
        )}
        {stage.elapsed_ms > 0 && (
          <span className="mono w-12 shrink-0 text-right text-[11px] text-[var(--color-faint)]">
            {(stage.elapsed_ms / 1000).toFixed(1)}s
          </span>
        )}
        {interactive && (
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            className="size-4 shrink-0 text-[var(--color-faint)] transition-transform duration-200"
            style={{ transform: expanded ? "rotate(180deg)" : "none" }}
            aria-hidden
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        )}
      </button>

      {expanded && interactive && (
        <div className="border-t border-[var(--color-line)] bg-[var(--color-bg)] px-4 py-4">
          {stage.variants ? (
            <VariantPicker variants={stage.variants} />
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-[var(--color-muted)]">
              {stage.detail ?? stage.summary ?? "No detail captured for this stage."}
            </pre>
          )}
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={() => onRerun(stage.name)}
              className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2.5 py-1.5 text-[12px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)]"
            >
              Re-run from here
            </button>
            <p className="text-[12px] text-[var(--color-faint)]">
              Everything below this stage regenerates. Nothing above it is touched.
            </p>
          </div>
        </div>
      )}
    </li>
  );
}

function StatusGlyph({ status }: { status: StageStatus }) {
  const map: Record<StageStatus, { glyph: string; color: string; label: string }> = {
    done: { glyph: "✓", color: "var(--color-ok)", label: "completed" },
    running: { glyph: "◐", color: "var(--color-accent)", label: "running" },
    pending: { glyph: "○", color: "var(--color-faint)", label: "pending" },
    stale: { glyph: "↻", color: "var(--color-warn)", label: "stale" },
    failed: { glyph: "✕", color: "var(--color-bad)", label: "failed" },
    skipped: { glyph: "–", color: "var(--color-faint)", label: "skipped" },
  };
  const { glyph, color, label } = map[status];
  return (
    <span
      className={`mono flex size-5 shrink-0 items-center justify-center rounded-full text-[12px] ${
        status === "running" ? "pulse" : ""
      }`}
      style={{ color }}
      role="img"
      aria-label={label}
    >
      {glyph}
    </span>
  );
}

function RunningBar() {
  return (
    <span className="flex items-center gap-2.5">
      <span className="skeleton h-1 w-32 rounded-full" />
      <span className="text-[12px] text-[var(--color-faint)]">working…</span>
    </span>
  );
}

/** Variant picker — side by side, score visible, one click to choose.
 *  Used for hooks, titles, and thumbnails. */
export function VariantPicker({ variants }: { variants: Variant[] }) {
  const [chosen, setChosen] = useState(0);

  return (
    <div className="grid gap-2.5" role="radiogroup" aria-label="Variants">
      {variants.map((variant, i) => {
        const selected = i === chosen;
        return (
          <button
            key={i}
            role="radio"
            aria-checked={selected}
            onClick={() => setChosen(i)}
            className={`rounded-[var(--radius-card)] border px-3.5 py-3 text-left transition-all duration-150 ${
              selected
                ? "border-[var(--color-accent)] bg-[var(--color-raised)]"
                : "border-[var(--color-line)] hover:border-[var(--color-line-hover)]"
            }`}
          >
            <div className="flex items-start gap-3">
              <span
                className="mt-1 size-3 shrink-0 rounded-full border-2"
                style={{
                  borderColor: selected
                    ? "var(--color-accent)"
                    : "var(--color-line-hover)",
                  background: selected ? "var(--color-accent)" : "transparent",
                }}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] leading-snug">{variant.text}</p>
                <p className="mt-1.5 flex flex-wrap items-center gap-x-3 text-[11px] text-[var(--color-faint)]">
                  <span className="uppercase tracking-wide">{variant.label}</span>
                  {variant.note && <span>{variant.note}</span>}
                </p>
              </div>
              {variant.score !== undefined && (
                <span className="mono shrink-0 text-[12px] text-[var(--color-muted)]">
                  {variant.score.toFixed(2)}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}

export function Pipeline({
  stages,
  onRerun,
}: {
  stages: Stage[];
  onRerun: (name: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const done = stages.filter((s) => s.status === "done").length;

  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--color-line)] px-4 py-3">
        <span className="text-[13px] font-semibold">Pipeline</span>
        <span className="mono text-[12px] text-[var(--color-faint)]">
          {done}/{stages.length}
        </span>
      </div>
      <ul>
        {stages.map((stage) => (
          <StageRow
            key={stage.name}
            stage={stage}
            expanded={open === stage.name}
            onToggle={() => setOpen(open === stage.name ? null : stage.name)}
            onRerun={onRerun}
          />
        ))}
      </ul>
    </div>
  );
}
