"use client";

import { useState, useTransition } from "react";
import { Header, Page, Button } from "@/components/ui";
import { Pipeline } from "@/components/pipeline";
import { useJobStream } from "@/lib/use-job-stream";
import { DEMO_JOB } from "@/lib/demo";
import type { Stage } from "@/lib/types";
import { publish, rerunFrom, startJob } from "./actions";

/** Create — the screen that matters.
 *
 *  Before submit: one input and three quiet chips. Nothing else is visible, because
 *  nothing else is a decision the user needs to make yet.
 *
 *  After submit: the input becomes the pipeline, fed by the engine's SSE stream.
 *  Each stage collapses to one informative line as it finishes.
 *
 *  With no engine running the same screen runs on `DEMO_JOB`, which is how the
 *  design stayed judgeable before the plumbing existed — but it says "demo" rather
 *  than implying a render actually happened.
 */
export default function CreatePage() {
  const [topic, setTopic] = useState("");
  const [format, setFormat] = useState<"short" | "long">("long");
  const [jobId, setJobId] = useState<string | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<{ code: string; message: string }[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  /** Bumped by Reconnect; rebuilding a dead EventSource needs a dependency change. */
  const [attempt, setAttempt] = useState(0);
  /** Which variant is picked per stage, so the choice reaches the publish call. */
  const [chosen, setChosen] = useState<Record<string, number>>({});

  const stream = useJobStream(jobId, emptyStages(), attempt);
  const stages: Stage[] = demo ? DEMO_JOB.stages : stream.stages;
  const cost = demo ? DEMO_JOB.cost_usd : stream.cost_usd;

  function start() {
    if (topic.trim().length < 3) return;
    setError(null);
    startTransition(async () => {
      const result = await startJob({ topic: topic.trim(), format });
      if (result.ok && result.data) {
        setJobId(result.data.job_id);
      } else {
        // The engine is not there. Show the design on demo data rather than a
        // dead end, and say which it is.
        setDemo(true);
        setError(result.error ?? "could not reach the engine");
      }
    });
  }

  function onPublish() {
    if (!jobId) return;
    setBlockers([]);
    setNotice(null);
    startTransition(async () => {
      const result = await publish(jobId, {
        // The variant picker used to keep its selection in its own useState and
        // hand it to nobody, so choosing a different title changed the highlight
        // and published the first one anyway.
        chosen_title_index: chosen.titles ?? 0,
        chosen_thumbnail_index: chosen.thumbnail ?? 0,
      });
      if (result.ok) {
        setNotice("Publishing — the upload has started.");
      } else {
        setBlockers(result.blockers ?? []);
        setError(result.error ?? "publish failed");
      }
    });
  }

  function reset() {
    setJobId(null);
    setDemo(false);
    setError(null);
    setBlockers([]);
    setNotice(null);
    setTopic("");
  }

  if (jobId || demo) {
    return (
      <>
        <Header
          title={topic || DEMO_JOB.topic}
          meta={
            <span className="mono flex items-center gap-2">
              {format === "long" ? "16:9" : "9:16"} · ${cost.toFixed(2)}
              {demo && (
                <span className="rounded-full border border-[var(--color-line)] px-2 py-0.5 text-[11px] text-[var(--color-faint)]">
                  demo data
                </span>
              )}
            </span>
          }
          action={
            <div className="flex gap-2">
              <Button variant="ghost" onClick={reset}>
                New
              </Button>
              <Button onClick={onPublish} disabled={demo || pending || stream.status !== "completed"}>
                Publish
              </Button>
            </div>
          }
        />
        <Page>
          <Pipeline
            stages={stages}
            chosen={chosen}
            canRerun={!demo && stream.status !== "running" && stream.status !== "connecting"}
            onChoose={(stage, index) => setChosen({ ...chosen, [stage]: index })}
            onRerun={(name) => {
              if (!jobId) return;
              setError(null);
              startTransition(async () => {
                const result = await rerunFrom(jobId, name);
                if (!result.ok) setError(result.error ?? "could not re-run that stage");
              });
            }}
          />

          {/* `stream.error` was produced and read by nobody: a dead stream froze
              the pipeline on its skeleton with Publish disabled forever, and the
              only way back was a full reload. */}
          {stream.error && (
            <div
              role="alert"
              className="mt-4 flex items-center gap-3 rounded-lg border border-[var(--color-warn)]/40 p-4"
            >
              <p className="flex-1 text-[13px] text-[var(--color-warn)]">{stream.error}</p>
              <Button variant="ghost" onClick={() => setAttempt(attempt + 1)}>
                Reconnect
              </Button>
            </div>
          )}

          {notice && (
            <p className="mt-4 text-[13px] text-[var(--color-muted)]">{notice}</p>
          )}

          {/* Each blocker states its reason. A bare "blocked" is not an
              acceptable thing to show someone about their own video. */}
          {blockers.length > 0 && (
            <div className="mt-4 rounded-lg border border-[var(--color-line)] p-4">
              <p className="text-[13px] font-semibold">Not ready to publish</p>
              <ul className="mt-2 space-y-1.5">
                {blockers.map((b) => (
                  <li key={b.code} className="text-[13px] text-[var(--color-muted)]">
                    {b.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && blockers.length === 0 && (
            <p className="mt-4 text-[13px] text-[var(--color-bad)]">{error}</p>
          )}

          <p className="mt-4 text-[12px] text-[var(--color-faint)]">
            This job keeps running if you close the tab. Progress is restored on
            return.
          </p>
        </Page>
      </>
    );
  }

  return (
    <>
      <Header title="Create" />
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[680px] flex-col justify-center px-8 pb-24">
        <label htmlFor="topic" className="text-[15px] text-[var(--color-muted)]">
          What&apos;s the video about?
        </label>

        <input
          id="topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && start()}
          autoFocus
          placeholder="Why bridges collapse"
          className="mt-3 w-full border-b border-[var(--color-line)] bg-transparent pb-3 text-[28px] font-semibold outline-none transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-accent)]"
        />

        <div className="mt-6 flex flex-wrap items-center gap-2">
          <Chip
            active={format === "short"}
            onClick={() => setFormat("short")}
            label="Short 9:16"
          />
          <Chip
            active={format === "long"}
            onClick={() => setFormat("long")}
            label="Long-form 16:9"
          />
          <Chip active={false} onClick={() => {}} label="From a series…" />

          <div className="ml-auto">
            <Button onClick={start} disabled={pending || topic.trim().length < 3}>
              {pending ? "Starting…" : "Generate"}
            </Button>
          </div>
        </div>

        <p className="mt-8 text-[13px] leading-relaxed text-[var(--color-faint)]">
          Research runs first — the script is built from sources, not from what the
          model already believes. Every stage is editable before anything is
          published.
        </p>
      </div>
    </>
  );
}

/** The pipeline before the first event arrives, so the shape is visible immediately. */
function emptyStages(): Stage[] {
  return DEMO_JOB.stages.map((s) => ({
    ...s,
    status: "pending" as const,
    summary: null,
    error: null,
    cost_usd: 0,
    elapsed_ms: 0,
  }));
}

function Chip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-3.5 py-1.5 text-[13px] transition-colors duration-150 ${
        active
          ? "border-[var(--color-ink)] text-[var(--color-ink)]"
          : "border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-line-hover)]"
      }`}
    >
      {label}
    </button>
  );
}
