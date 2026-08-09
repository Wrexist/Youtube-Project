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
        // The running row is lifted onto `--raised`. In a seventeen-row list the
        // pulsing glyph alone is a small target for the eye, and "which one is it
        // on?" is the second question people ask after "is it stuck?". Not accent:
        // the glyph already spends the accent here, and a whole highlighted row
        // would blow the under-5% budget on its own.
        // Wraps below `sm`, and only below `sm`. Beside a 64px rail a 375px screen
        // leaves the row 247px, which a glyph, a 132px title, a 128px progress bar,
        // a message, a cost and a chevron do not fit into — they were being clipped
        // by the card. Giving the middle column its own line costs one row of
        // height on a phone and nothing at all on a desktop.
        className={`flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5 text-left transition-colors duration-150 sm:flex-nowrap ${
          stage.status === "running" ? "bg-[var(--color-raised)]" : ""
        } ${interactive ? "hover:bg-[var(--color-raised)]" : "cursor-default"}`}
      >
        <StatusGlyph status={stage.status} />

        <span
          className={`w-[132px] shrink-0 text-[13px] font-semibold ${
            stage.status === "pending" ? "text-[var(--color-faint)]" : ""
          }`}
        >
          {stage.title}
        </span>

        {/* Last on a phone so it drops to its own full-width line; in the middle,
            as it has always been, from `sm` up. */}
        <span className="order-last w-full min-w-0 truncate text-[13px] text-[var(--color-muted)] sm:order-none sm:w-auto sm:flex-1">
          {stage.status === "running" ? (
            <RunningBar message={stage.summary} progress={stage.progress} />
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
          // Dropped on a narrow screen. Glyph, title, message, cost, duration and
          // chevron do not fit beside a 64px rail at 375px, and the card clips
          // rather than scrolls — so something has to go, and a duration is the
          // least of these. Cost stays: it is money.
          <span className="mono hidden w-12 shrink-0 text-right text-[11px] text-[var(--color-faint)] sm:inline">
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

/**
 * A running stage's line: how far through, what it is doing, and that it is alive.
 *
 * The message half came first — `stage.progress` and `stage.retrying` land on
 * `summary`, and rendering a bare skeleton meant a twelve-minute render and a
 * stage on its third retry looked identical.
 *
 * The *fraction* was still being dropped. `compose.py` has always emitted one
 * (0.05 downloading, 0.25 composing, 0.72 placing beats, 0.75 subtitles, 0.85
 * encoding) and the reducer threw it away, so the honest question a forty-minute
 * render provokes — "is this failed, or is it working?" — had no answer on screen.
 *
 * Indeterminate is a real state, not a failure to know: the short stages never
 * report a fraction at all, and the long ones send keepalives between the ones
 * they do. So a bar with no number shimmers rather than sitting at zero, which
 * would read as "no progress" for a stage that is simply not instrumented.
 */
function RunningBar({
  message,
  progress,
}: {
  message?: string | null;
  progress?: number | null;
}) {
  const percent =
    typeof progress === "number" && Number.isFinite(progress)
      ? Math.round(Math.min(1, Math.max(0, progress)) * 100)
      : null;

  return (
    <span className="flex items-center gap-2.5">
      <span
        // `.skeleton` already sets position and the raised background, so the
        // determinate track only needs the background — and applying both would
        // put a second shimmer under the fill.
        className={`h-1 w-32 shrink-0 overflow-hidden rounded-full ${
          percent === null ? "skeleton" : "relative bg-[var(--color-raised)]"
        }`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        // Omitted while indeterminate: a screen reader announcing "0 percent"
        // for a stage that has not reported is the audible version of the bug
        // this component exists to fix.
        {...(percent === null ? {} : { "aria-valuenow": percent })}
        aria-valuetext={message || "working"}
      >
        {percent !== null && (
          <span className="progress-fill is-live" style={{ width: `${percent}%` }} />
        )}
      </span>

      <span className="min-w-0 truncate text-[12px] text-[var(--color-faint)]">
        {message || "working…"}
      </span>

      {percent !== null && (
        // Tabular figures, or the number jitters the message left and right every
        // time it ticks past a digit that happens to be narrower.
        <span className="mono shrink-0 text-[11px] text-[var(--color-faint)]">
          {percent}%
        </span>
      )}
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

  // Skipped counts toward the end of the job even though it is not "done": a run
  // that skips a stage would otherwise have a bar that can never fill, which reads
  // as unfinished work rather than as a decision the workflow made.
  const settled = stages.filter(
    (s) => s.status === "done" || s.status === "skipped",
  ).length;
  // Partial credit for the stage in flight, so a seventeen-stage job with one
  // forty-minute render in it advances during that render instead of holding at
  // 16/17 for most of the run — which is precisely when someone asks whether it
  // has failed.
  const running = stages.find((s) => s.status === "running");
  const partial =
    typeof running?.progress === "number" && Number.isFinite(running.progress)
      ? Math.min(1, Math.max(0, running.progress))
      : 0;
  const overall = stages.length
    ? Math.min(1, (settled + partial) / stages.length)
    : 0;

  return (
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-[13px] font-semibold">Pipeline</span>
        <span className="mono text-[12px] text-[var(--color-faint)]">
          {done}/{stages.length}
        </span>
      </div>

      {/* The job's own progress, as the card's dividing line rather than as
          another element. It replaces the border that was here, so it costs no
          vertical space and reads as part of the frame.

          `aria-hidden` on purpose: the count beside "Pipeline" already states this
          for a screen reader, and announcing both would say the same thing twice.
          Green at the end because finishing deserves to look like finishing — and
          the count says 17/17 alongside it, so the meaning never rests on colour. */}
      <div
        className="relative h-0.5 w-full bg-[var(--color-line)]"
        aria-hidden
      >
        <span
          className="progress-fill"
          style={{
            width: `${overall * 100}%`,
            background: overall >= 1 ? "var(--color-ok)" : undefined,
          }}
        />
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
