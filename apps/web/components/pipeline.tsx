"use client";

import { useState } from "react";
import { ThumbnailPanel } from "@/components/thumbnail-panel";
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
  onChoose,
  chosen,
  canRerun = true,
  jobId = null,
}: {
  stage: Stage;
  expanded: boolean;
  onToggle: () => void;
  onRerun: (name: string) => void;
  onChoose?: (stage: string, index: number) => void;
  chosen?: number;
  /** False while the job is running — the engine 409s a re-run on a live job. */
  canRerun?: boolean;
  /** Needed by the stages that fetch their own detail. Null on the demo pipeline. */
  jobId?: string | null;
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
            <RunningBar message={stage.summary} />
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
          {/* The thumbnail is the only artifact a viewer sees before deciding
              whether to watch, and this row reported it as "3 items". It gets a
              panel of its own rather than the generic detail dump. */}
          {stage.name === "thumbnail" && jobId ? (
            <ThumbnailPanel
              jobId={jobId}
              chosen={chosen}
              onChoose={onChoose ? (i) => onChoose(stage.name, i) : undefined}
            />
          ) : stage.variants ? (
            <VariantPicker
              variants={stage.variants}
              chosen={chosen}
              onChoose={onChoose ? (i) => onChoose(stage.name, i) : undefined}
            />
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-[var(--color-muted)]">
              {stage.detail ?? stage.summary ?? "No detail captured for this stage."}
            </pre>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              onClick={() => onRerun(stage.name)}
              disabled={!canRerun}
              className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2.5 py-1.5 text-[12px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Re-run from here
            </button>
            {/* Only on a failure, where there is something worth reporting. The
                row truncates its one line to fit, so the full text was visible
                nowhere — and retyping a stack of provider errors by hand from a
                screenshot is how bug reports end up missing the useful half. */}
            {stage.status === "failed" && (
              <CopyButton
                label="Copy error"
                text={[
                  `stage: ${stage.name}`,
                  `error: ${stage.error ?? "failed"}`,
                  stage.detail ? `detail:\n${stage.detail}` : "",
                ]
                  .filter(Boolean)
                  .join("\n")}
              />
            )}
            <p className="text-[12px] text-[var(--color-faint)]">
              {canRerun
                ? "Everything below this stage regenerates. Nothing above it is touched."
                : "Available once the job finishes."}
            </p>
          </div>
        </div>
      )}
    </li>
  );
}

/**
 * Copy some text, and say so.
 *
 * The confirmation is the whole point: a button that copies silently is
 * indistinguishable from a button that did nothing, and the usual response is to
 * press it again and paste twice. Reverts on a timer rather than staying
 * "Copied", so a second press later reads as a second copy.
 *
 * `navigator.clipboard` needs a secure context, which localhost is. It can still
 * be refused by permissions policy, so the failure is reported rather than
 * swallowed — being told "could not copy" beats pasting the previous clipboard
 * into an issue and not noticing.
 */
export function CopyButton({ label, text }: { label: string; text: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("done");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }

  return (
    <button
      onClick={copy}
      aria-live="polite"
      className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2.5 py-1.5 text-[12px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)]"
    >
      {state === "done" ? "Copied" : state === "failed" ? "Could not copy" : label}
    </button>
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

/** A running stage's line. The skeleton stays — it is what says the job is alive —
 *  but "working…" is only the placeholder for a stage that has said nothing yet.
 *  UI-DESIGN.md #5: long work streams progress, and `stage.progress` /
 *  `stage.retrying` messages land on `summary`. Rendering the skeleton
 *  unconditionally meant a twelve-minute render and a stage on its third retry
 *  looked identical. */
function RunningBar({ message }: { message?: string | null }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="skeleton h-1 w-32 shrink-0 rounded-full" />
      <span className="min-w-0 truncate text-[12px] text-[var(--color-faint)]">
        {message || "working…"}
      </span>
    </span>
  );
}

/** Variant picker — side by side, score visible, one click to choose.
 *  Used for hooks, titles, and thumbnails. */
export function VariantPicker({
  variants,
  chosen: controlled,
  onChoose,
}: {
  variants: Variant[];
  /** Supplied by the Create screen so the choice reaches the publish call. */
  chosen?: number;
  onChoose?: (index: number) => void;
}) {
  // Uncontrolled where nobody is listening — the Library preview still wants a
  // working radio group even though nothing acts on it.
  const [local, setLocal] = useState(0);
  const chosen = controlled ?? local;
  const setChosen = (i: number) => (onChoose ? onChoose(i) : setLocal(i));

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
  onChoose,
  chosen,
  canRerun = true,
  jobId = null,
}: {
  stages: Stage[];
  onRerun: (name: string) => void;
  onChoose?: (stage: string, index: number) => void;
  /** Chosen variant index per stage, so the Create screen can publish the pick. */
  chosen?: Record<string, number>;
  canRerun?: boolean;
  jobId?: string | null;
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
            onChoose={onChoose}
            chosen={chosen?.[stage.name]}
            canRerun={canRerun}
            jobId={jobId}
          />
        ))}
      </ul>
    </div>
  );
}
