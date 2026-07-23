"use client";

import { useState } from "react";
import { Header, Page, Button } from "@/components/ui";
import { Pipeline } from "@/components/pipeline";
import { DEMO_JOB } from "@/lib/demo";
import type { Job } from "@/lib/types";

/** Create — the screen that matters.
 *
 *  Before submit: one input and three quiet chips. Nothing else is visible, because
 *  nothing else is a decision the user needs to make yet.
 *
 *  After submit: the input becomes the pipeline. Each stage collapses to one
 *  informative line as it finishes, and expands to show and edit what it produced.
 */
export default function CreatePage() {
  const [topic, setTopic] = useState("");
  const [format, setFormat] = useState<"short" | "long">("long");
  const [job, setJob] = useState<Job | null>(null);

  function start() {
    if (topic.trim().length < 3) return;
    // Wired to POST /v1/jobs + the SSE stream once the engine is running; the demo
    // job has the identical shape, so that swap touches only this function.
    setJob({ ...DEMO_JOB, topic, format });
  }

  if (job) {
    return (
      <>
        <Header
          title={job.topic}
          meta={
            <span className="mono">
              {job.format === "long" ? "16:9" : "9:16"} · ${job.cost_usd.toFixed(2)}
            </span>
          }
          action={
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setJob(null)}>
                New
              </Button>
              <Button>Publish</Button>
            </div>
          }
        />
        <Page>
          <Pipeline
            stages={job.stages}
            onRerun={(name) => console.log("re-run from", name)}
          />
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
            <Button onClick={start} disabled={topic.trim().length < 3}>
              Generate
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
