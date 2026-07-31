"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { Header, Page, Button, Card } from "@/components/ui";
import { Pipeline } from "@/components/pipeline";
import { useJobStream } from "@/lib/use-job-stream";
import { DEMO_JOB } from "@/lib/demo";
import type { Stage } from "@/lib/types";
import { publish, rerunFrom, startJob } from "./actions";

/**
 * Whether this install can actually make a video, as of the last page load.
 *
 * `known: false` means the engine did not answer — which is not the same as "not
 * set up", and must not be presented as it. In that case the screen behaves as it
 * always did and falls back to the demo pipeline.
 */
export interface Readiness {
  known: boolean;
  canRender: boolean;
  missing: string[];
}

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
 *
 *  `ready` is passed in by the Server Component in `page.tsx` rather than fetched
 *  here. A brand-new install has no keys, and Generate on such an install produced
 *  a job that ran one stage and died on a provider error — the first thing the
 *  product ever did was fail, for a reason it knew about before the click.
 */
export function CreateView({ ready }: { ready: Readiness }) {
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
          {stream.status === "running" && !demo && (
            <div className="mb-4 rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)] px-4 py-3">
              <p className="text-[13px] font-semibold">Generation is running</p>
              <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-faint)]">
                Research and model calls can be quiet for a moment; Studio now sends
                keepalive progress so a healthy job does not look frozen.
              </p>
            </div>
          )}

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
                if (!result.ok) {
                  setError(result.error ?? "could not re-run that stage");
                  return;
                }
                // The stream for this job has already closed — `stream.closed`
                // shuts the EventSource, and the terminal status it left behind
                // is what enables Publish. Both have to be undone here, or the
                // pipeline sits on the old run's rows while the engine rewrites
                // the stages beneath them, with Publish live over a video that
                // is being regenerated (CLAUDE.md #3).
                stream.markRunning();
                setAttempt((a) => a + 1);
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

  // Known-not-ready, which is narrower than "not ready": an unreachable engine
  // leaves `known` false and this screen behaves exactly as it always did.
  const blocked = ready.known && !ready.canRender;

  return (
    <>
      <Header title="Create" />
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[680px] flex-col justify-center px-8 pb-24">
        {blocked && <SetupPrompt missing={ready.missing} />}

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
          {/* "From a series…" was here with an empty onClick. Nothing serves series
              yet, so it was a chip that swallowed the click and changed nothing. */}

          <div className="ml-auto">
            <Button
              onClick={start}
              disabled={blocked || pending || topic.trim().length < 3}
              title={blocked ? "Add an LLM key and a footage key in Setup first" : undefined}
            >
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

/**
 * Shown once, on an install that cannot render yet.
 *
 * Above the input rather than replacing it, so the product still shows what it is
 * for while explaining what it needs — someone evaluating this should be able to
 * see the thing they are being asked to fetch keys for.
 *
 * Generate is disabled alongside it. Leaving it live meant a first click that
 * started a job, ran one stage, and died on a provider error the engine already
 * knew was coming — and cost the person a trip through the job log to find out
 * that the answer was "you have no API key".
 */
function SetupPrompt({ missing }: { missing: string[] }) {
  const one = missing.length === 1;
  return (
    <Card className="mb-8 border-[var(--color-warn)]/40 p-5">
      <h2 className="text-[15px] font-semibold text-[var(--color-warn)]">
        {one ? "One key and this works" : "Two keys and this works"}
      </h2>
      {/* The body follows the count too. Saying "both are free" under a heading
          that says one key is missing reads as a screen that has not noticed the
          key you just saved. */}
      <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-muted)]">
        {one
          ? "One credential is still missing. It is free, and takes a couple of minutes to get."
          : "Studio needs a model to write with and a source for footage. Both are free, and setting them up takes about five minutes."}
      </p>
      {missing.length > 0 && (
        <p className="mono mt-2 text-[11px] text-[var(--color-faint)]">
          Missing: {missing.join(", ")}
        </p>
      )}
      <Link
        href="/setup"
        className="mt-4 inline-block rounded-[var(--radius-btn)] bg-[var(--color-accent)] px-3.5 py-2 text-[13px] font-semibold text-white transition-all duration-150 hover:brightness-110"
      >
        Open Setup
      </Link>
    </Card>
  );
}

/** The pipeline before the first event arrives, so the shape is visible immediately.
 *
 *  Only the graph's shape is borrowed from `DEMO_JOB` — name, title, editable. Every
 *  field is listed rather than spread, because a spread carried the fixtures' own
 *  `detail` and `variants` into a *live* job: expanding Research on a real render
 *  showed the NTSB bridge write-up, and Hook offered three demo variants to pick
 *  between, neither of which the engine had produced. A stage with no detail must
 *  say so (pipeline.tsx renders "No detail captured for this stage."), and any field
 *  added to the fixtures later must not leak in by default. */
function emptyStages(): Stage[] {
  return DEMO_JOB.stages.map((s) => ({
    name: s.name,
    title: s.title,
    editable: s.editable,
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
