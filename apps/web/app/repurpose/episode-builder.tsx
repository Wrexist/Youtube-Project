"use client";

import { useEffect, useState, useTransition } from "react";
import type { OriginalityReport } from "@studio/contracts";

import { Button, Card } from "@/components/ui";
import { buildEpisode, previewEpisode } from "@/app/actions";

export interface BuilderClip {
  id: string;
  handle: string;
  caption: string;
  duration: number;
  lane: string | null;
  cleared: boolean;
}

/** How much of each clip the episode uses, before the workflow refines it. */
const SEGMENT_SECONDS = 20;

const ASPECTS = [
  { id: "9:16" as const, label: "Short 9:16" },
  { id: "16:9" as const, label: "Long-form 16:9" },
];

/**
 * The episode: which clips, in what order, and what the gate will make of it.
 *
 * **Why the preview is split in two.** The rights verdict is real — those grants
 * exist right now, and a clip that is not cleared will stop the run before a byte
 * is fetched. The transformation verdict is a *projection*: narration, cut density
 * and the audio bed are decided by the workflow, and the gate judges the finished
 * edit. Presenting one blended number here would promise something this screen
 * cannot know, which is exactly the failure the report's two verdicts exist to
 * avoid.
 *
 * Ordering is buttons rather than drag. The Calendar drags because a date is a
 * position in a grid; a cut list is a sequence, and "move up" is one keystroke
 * away from working for someone who cannot drag.
 */
export function EpisodeBuilder({
  clips,
  live,
  onRemove,
}: {
  clips: BuilderClip[];
  live: boolean;
  onRemove: (id: string) => void;
}) {
  const [order, setOrder] = useState<string[]>([]);
  const [aspect, setAspect] = useState<"9:16" | "16:9">("9:16");
  const [topic, setTopic] = useState("");
  const [report, setReport] = useState<OriginalityReport | null>(null);
  const [result, setResult] = useState<{ jobId?: string; error?: string } | null>(null);
  const [building, startBuild] = useTransition();

  // Keep the order in step with the selection without losing an arrangement the
  // operator has already made: existing ids keep their positions, new ones append.
  const ids = clips.map((c) => c.id);
  const ordered = [...order.filter((id) => ids.includes(id)), ...ids.filter((id) => !order.includes(id))];

  const inOrder = ordered
    .map((id) => clips.find((c) => c.id === id))
    .filter((c): c is BuilderClip => c !== undefined);

  const totalSeconds = inOrder.reduce(
    (sum, clip) => sum + Math.min(SEGMENT_SECONDS, clip.duration || SEGMENT_SECONDS),
    0,
  );

  useEffect(() => {
    // No synchronous clear here: `setState` in an effect body cascades renders,
    // and there is nothing to clear anyway — `PreCheck` is rendered from
    // `showPreview` below, which is derived, so a stale report cannot be shown.
    if (!live || inOrder.length === 0) return;

    let cancelled = false;
    previewEpisode({
      segments: inOrder.map((clip) => ({
        start_s: 0,
        end_s: Math.min(SEGMENT_SECONDS, clip.duration || SEGMENT_SECONDS),
        source_id: clip.id,
        // The workflow refuses to build without commentary, so narration over
        // every clip is the accurate prediction rather than an optimistic one.
        narrated: true,
        annotated: false,
      })),
      cuts: Math.max(0, inOrder.length - 1),
      audio_bed_replaced: true,
      watermarked_sources: [],
      attribution_on_screen: true,
      attribution_in_description: true,
      is_compilation: inOrder.length > 1,
      max_similarity: 0,
      template_repeats: 0,
      structure_repeats: 0,
      compared_against: 0,
    }).then((outcome) => {
      if (!cancelled) setReport(outcome.ok ? (outcome.data ?? null) : null);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the cut list, not the objects
  }, [live, ordered.join(","), clips.length]);

  function move(id: string, by: number) {
    const current = [...ordered];
    const from = current.indexOf(id);
    const to = from + by;
    if (from < 0 || to < 0 || to >= current.length) return;
    [current[from], current[to]] = [current[to], current[from]];
    setOrder(current);
  }

  function submit() {
    startBuild(async () => {
      const outcome = await buildEpisode({
        topic: topic.trim(),
        sourceIds: ordered,
        aspect,
        segmentSeconds: SEGMENT_SECONDS,
      });
      setResult(
        outcome.ok
          ? { jobId: (outcome.data as { job_id: string }).job_id }
          : { error: outcome.error },
      );
    });
  }

  const tooShortTopic = topic.trim().length < 3;
  const blocked = !live || inOrder.length === 0 || tooShortTopic || building;

  if (inOrder.length === 0) return null;

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-[15px] font-semibold">Episode</h2>
        <span className="mono text-[12px] text-[var(--color-faint)]">
          {Math.floor(totalSeconds / 60)}:{String(Math.round(totalSeconds % 60)).padStart(2, "0")}{" "}
          from {inOrder.length} clip{inOrder.length === 1 ? "" : "s"}
        </span>
      </div>

      <ol className="mt-4 flex flex-col gap-2">
        {inOrder.map((clip, index) => (
          <li
            key={clip.id}
            className="flex items-center gap-3 rounded-[var(--radius-card)] border border-[var(--color-line)] px-3 py-2.5"
          >
            <span className="mono w-5 shrink-0 text-[12px] text-[var(--color-faint)]">
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px]">{clip.caption}</p>
              <p className="mono text-[11px] text-[var(--color-faint)]">
                {clip.handle} · up to {Math.min(SEGMENT_SECONDS, clip.duration || SEGMENT_SECONDS)}s
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                aria-label={`Move ${clip.caption} earlier`}
                disabled={index === 0}
                onClick={() => move(clip.id, -1)}
                className="rounded-[var(--radius-btn)] px-2 py-1 text-[13px] text-[var(--color-muted)] not-disabled:hover:text-[var(--color-ink)] disabled:opacity-30"
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move ${clip.caption} later`}
                disabled={index === inOrder.length - 1}
                onClick={() => move(clip.id, 1)}
                className="rounded-[var(--radius-btn)] px-2 py-1 text-[13px] text-[var(--color-muted)] not-disabled:hover:text-[var(--color-ink)] disabled:opacity-30"
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={`Remove ${clip.caption} from the episode`}
                onClick={() => onRemove(clip.id)}
                className="rounded-[var(--radius-btn)] px-2 py-1 text-[13px] text-[var(--color-muted)] hover:text-[var(--color-bad)]"
              >
                ✕
              </button>
            </div>
          </li>
        ))}
      </ol>

      <label className="mt-5 flex flex-col gap-1.5">
        <span className="text-[12px] text-[var(--color-faint)]">
          What is this episode about?
        </span>
        <input
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="the mistake all three of these have in common"
          className="rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)] px-3 py-2 text-[13px]"
        />
        <span className="text-[11px] text-[var(--color-faint)]">
          The commentary is written to this. A bag of clips with no argument is the
          shape reviewers read as a compilation.
        </span>
      </label>

      <div className="mt-4 flex items-center gap-2">
        {ASPECTS.map((option) => (
          <button
            key={option.id}
            type="button"
            aria-pressed={aspect === option.id}
            onClick={() => setAspect(option.id)}
            className={`rounded-[var(--radius-btn)] border px-3 py-1.5 text-[12px] transition-colors duration-150 ${
              aspect === option.id
                ? "border-[var(--color-accent)] text-[var(--color-ink)]"
                : "border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-line-hover)]"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {live && report && <PreCheck report={report} />}

      <div className="mt-5 flex items-center gap-3">
        <Button
          onClick={submit}
          disabled={blocked}
          title={
            !live
              ? "Building an episode needs the engine running"
              : tooShortTopic
                ? "Say what the episode is about first"
                : undefined
          }
        >
          {building ? "Starting…" : "Build episode"}
        </Button>
        {result?.jobId && (
          <a
            href={`/?job=${encodeURIComponent(result.jobId)}`}
            className="text-[13px] underline decoration-[var(--color-line)] underline-offset-2 hover:decoration-current"
          >
            Watch it build
          </a>
        )}
        {result?.error && (
          <span className="text-[13px] text-[var(--color-bad)]">{result.error}</span>
        )}
      </div>
    </Card>
  );
}

/**
 * What the gate can already tell you, and what it cannot.
 *
 * Rights are settled now. Everything else depends on an edit that does not exist
 * yet, and saying so is the difference between a useful pre-check and a promise
 * this screen has no way to keep.
 */
function PreCheck({ report }: { report: OriginalityReport }) {
  const structural = report.transformation.signals.filter((signal) =>
    ["segment_count", "corpus"].includes(signal.name),
  );

  return (
    <div className="mt-4 rounded-[var(--radius-card)] border border-[var(--color-line)] p-3">
      <p className="text-[12px] font-semibold">
        Rights:{" "}
        <span
          style={{
            color: report.rights.cleared ? "var(--color-ok)" : "var(--color-bad)",
          }}
        >
          {report.rights.cleared ? "all clips cleared" : "not cleared"}
        </span>
      </p>
      {!report.rights.cleared && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {report.rights.ungranted.map((id) => (
            <li key={id} className="text-[12px] text-[var(--color-muted)]">
              {id}: no grant recorded
            </li>
          ))}
        </ul>
      )}
      {structural.map((signal) => (
        <p key={signal.name} className="mt-1.5 text-[12px] text-[var(--color-muted)]">
          {signal.message}
        </p>
      ))}
      <p className="mt-2 text-[11px] text-[var(--color-faint)]">
        Originality is judged on the finished edit, not on this list — the commentary,
        the cuts and the audio are decided while it builds.
      </p>
    </div>
  );
}
