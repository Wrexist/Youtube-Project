"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { Header, Page, Button, Card } from "@/components/ui";
import { Celebration } from "@/components/celebration";
import { Pipeline } from "@/components/pipeline";
import { VideoPreview } from "@/components/video-preview";
import { useJobStream } from "@/lib/use-job-stream";
import { DEMO_JOB } from "@/lib/demo";
import type { Stage } from "@/lib/types";
import type { BacklogIdea, Playlist } from "@studio/contracts";
import {
  ideaBacklog,
  improveTopic,
  loadPlaylists,
  publish,
  refuseIdea,
  rerunFrom,
  startJob,
} from "./actions";

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
export function CreateView({
  ready,
  resumeJobId = null,
  resumeTopic = "",
  resumeFormat = "long",
}: {
  ready: Readiness;
  /** `?job=<id>` — reopen a project instead of starting a blank one. */
  resumeJobId?: string | null;
  /** Fetched server-side: reopening is exactly when the browser has forgotten. */
  resumeTopic?: string;
  resumeFormat?: "short" | "long";
}) {
  const [topic, setTopic] = useState(resumeTopic);
  /** Set once Improve has rewritten the field: what it said, and the way back. */
  const [improved, setImproved] = useState<{
    why: string;
    previous: string;
    previousFormat: "short" | "long";
  } | null>(null);
  const [improving, setImproving] = useState(false);
  /** The standing backlog for this channel's niche. Empty until the engine answers. */
  const [ideas, setIdeas] = useState<BacklogIdea[]>([]);
  const [format, setFormat] = useState<"short" | "long">(resumeFormat);
  const [jobId, setJobId] = useState<string | null>(resumeJobId);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<{ code: string; message: string }[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  /** Bumped by Reconnect; rebuilding a dead EventSource needs a dependency change. */
  const [attempt, setAttempt] = useState(0);
  /** Which variant is picked per stage, so the choice reaches the publish call. */
  const [chosen, setChosen] = useState<Record<string, number>>({});
  /** The playlist to add the upload to, or "" for none. */
  const [playlist, setPlaylist] = useState("");
  const [playlists, setPlaylists] = useState<Playlist[]>([]);

  const stream = useJobStream(jobId, emptyStages(), attempt);

  // Fetched after mount rather than in the Server Component: it costs a model
  // call and an autocomplete sweep per candidate, and blocking the first paint of
  // the app's main screen on that would be the wrong trade. Engine-side it is
  // cached for half an hour, so coming back here is free.
  useEffect(() => {
    if (jobId || demo) return;
    let cancelled = false;
    ideaBacklog().then((r) => {
      if (!cancelled && r.ok && r.data) setIdeas(r.data);
    });
    return () => {
      cancelled = true;
    };
  }, [jobId, demo]);
  // Fetched when the run finishes rather than on mount: it is one quota unit and
  // a round trip that only matters at the approval gate, and a job that fails
  // never reaches it.
  useEffect(() => {
    if (demo || stream.status !== "completed") return;
    let cancelled = false;
    loadPlaylists().then((r) => {
      if (!cancelled && r.ok && r.data) setPlaylists(r.data);
    });
    return () => {
      cancelled = true;
    };
  }, [demo, stream.status]);

  /**
   * Say so when a long render lands.
   *
   * A long-form render is tens of minutes; the tab gets buried and there is no
   * signal. The stream already knows, so this costs a notification and nothing
   * else.
   *
   * Guarded three ways, because every one of them happens: the API is absent in
   * some browsers and all insecure origins, permission may be denied, and the
   * effect re-runs on every stream tick — so `notified` makes it fire once per
   * job rather than once per frame. Nothing is shown while the tab is focused;
   * a notification for something you are already looking at is noise.
   */
  const notified = useRef<string | null>(null);
  useEffect(() => {
    if (demo || !jobId) return;
    if (stream.status !== "completed" && stream.status !== "failed") return;
    if (notified.current === jobId) return;
    notified.current = jobId;

    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    if (typeof document !== "undefined" && document.visibilityState === "visible") return;

    const ok = stream.status === "completed";
    new Notification(ok ? "Your video is ready" : "The run stopped", {
      body: ok ? topic || "Open Studio to review and publish." : (stream.error ?? "A stage failed."),
      tag: jobId, // replaces rather than stacks if one is already showing
    });
  }, [demo, jobId, stream.status, stream.error, topic]);

  const stages: Stage[] = demo ? DEMO_JOB.stages : stream.stages;
  const cost = demo ? DEMO_JOB.cost_usd : stream.cost_usd;

  /**
   * The play layer's moment: confetti once, when a real render lands.
   *
   * Its own ref rather than sharing `notified` — the notification effect bails
   * out early on focused tabs and denied permissions, and the celebration must
   * fire exactly when a job completes regardless of either. Once per job, never
   * for demo runs (celebrating work nobody did teaches you to ignore it), and
   * never for a resumed job that was already complete when the page loaded.
   */
  const [celebrate, setCelebrate] = useState(false);
  const celebrated = useRef<string | null>(resumeJobId);
  useEffect(() => {
    if (demo || !jobId || stream.status !== "completed") return;
    if (celebrated.current === jobId) return;
    celebrated.current = jobId;
    setCelebrate(true);
  }, [demo, jobId, stream.status]);

  /**
   * Sharpen the fragment in the box into a topic the pipeline can research.
   *
   * Deliberately its own state rather than `startTransition`: `pending` gates
   * Generate, and borrowing it here would disable the primary action while a
   * different, optional call was in flight.
   *
   * A failure leaves the field exactly as typed. This is an assist, and an
   * assist that clears your input when the model is unreachable is worse than
   * no assist.
   */
  async function improve() {
    const rough = topic.trim();
    if (rough.length < 3 || improving) return;

    setError(null);
    setImproving(true);
    try {
      const result = await improveTopic(rough, format);
      if (!result.ok || !result.data) {
        setError(result.error ?? "could not improve that — the topic is unchanged");
        return;
      }
      setImproved({ why: result.data.why, previous: topic, previousFormat: format });
      setTopic(result.data.topic);
      if (result.data.format === "short" || result.data.format === "long") {
        setFormat(result.data.format);
      }
    } finally {
      setImproving(false);
    }
  }

  function start() {
    if (topic.trim().length < 3) return;
    setError(null);
    // Asked here and nowhere else. Browsers require a user gesture for this, and
    // Generate is the only moment where wanting to be told when it finishes is
    // self-evident — a prompt on page load is the one everybody denies. The result
    // is deliberately ignored: a refusal is a preference, not an error, and the
    // run is unaffected either way.
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission().catch(() => {});
    }
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
        // `PublishRequest` has accepted this since it was written, and nothing
        // ever sent it — so `PlaylistStage` skipped on every publish this project
        // has ever done. Undefined rather than "" keeps the stage's own skip
        // condition meaningful for "no playlist".
        playlist_id: playlist || undefined,
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
    // Both, or the next video is silently added to the last one's playlist. The
    // list repopulates itself when the new job completes; the *selection* would
    // have survived, and `onPublish` reads it directly.
    setPlaylist("");
    setPlaylists([]);
    setError(null);
    setBlockers([]);
    setNotice(null);
    setTopic("");
    setImproved(null);
    // Drop `?job=` as well, or New clears the screen and the next reload
    // reopens the project that was just left. `replace` rather than `push` so
    // Back does not walk into a blank Create.
    if (typeof window !== "undefined" && window.location.search) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }

  if (jobId || demo) {
    return (
      <>
        {celebrate && (
          <Celebration
            label="Video complete"
            detail="+100 XP"
            onDone={() => setCelebrate(false)}
          />
        )}
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
            <div className="flex items-center gap-2">
              {/* Only once there is something to publish, and only if the channel
                  actually has playlists. An empty select beside Publish is a
                  control that asks a question with no answers. */}
              {playlists.length > 0 && stream.status === "completed" && !demo && (
                <select
                  value={playlist}
                  onChange={(e) => setPlaylist(e.target.value)}
                  aria-label="Add to playlist"
                  className="max-w-[180px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-2.5 py-2 text-[13px] text-[var(--color-muted)] outline-none transition-colors duration-150 hover:border-[var(--color-line-hover)] focus:border-[var(--color-accent)]"
                >
                  <option value="">No playlist</option>
                  {playlists.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title} ({p.count})
                    </option>
                  ))}
                </select>
              )}
              <Button variant="ghost" onClick={reset}>
                New
              </Button>
              <Button
                onClick={onPublish}
                disabled={demo || pending || stream.status !== "completed"}
              >
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

          {/* Above the pipeline, not inside it. Once the render exists it is the
              thing you came for, and burying it in the eleventh stage row of
              seventeen makes you hunt for it. Renders nothing until there is a
              finished video to play. */}
          {!demo && <VideoPreview stages={stages} jobId={jobId} />}

          <Pipeline
            stages={stages}
            jobId={demo ? null : jobId}
            chosen={chosen}
            canRerun={
              !demo && stream.status !== "running" && stream.status !== "connecting"
            }
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
              <p className="flex-1 text-[13px] text-[var(--color-warn)]">
                {stream.error}
              </p>
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
            This job keeps running if you close the tab. Progress is restored on return.
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

        {/* What the model changed, and the way back. Shown only after Improve has
            run, and it says the previous topic in full: this rewrites the field
            someone was about to press Generate on, and a rewrite you cannot undo
            or inspect is one you stop trusting after it guesses wrong once. */}
        {improved && (
          <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="flex-1 text-[13px] leading-relaxed text-[var(--color-muted)]">
              {improved.why}
            </p>
            <button
              onClick={() => {
                setTopic(improved.previous);
                setFormat(improved.previousFormat);
                setImproved(null);
              }}
              className="text-[12px] text-[var(--color-faint)] underline decoration-[var(--color-line-hover)] underline-offset-4 transition-colors duration-150 hover:text-[var(--color-ink)]"
            >
              Undo
            </button>
          </div>
        )}

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

          {/* A chip, not a second Button. Generate is this screen's one primary
              action (UI-DESIGN #1) and two filled buttons side by side is exactly
              the "which do I press?" this design avoids. */}
          <Chip
            active={false}
            onClick={improve}
            disabled={blocked || improving || pending || topic.trim().length < 3}
            label={improving ? "Thinking…" : "Improve with AI"}
          />

          <div className="ml-auto">
            <Button
              onClick={start}
              disabled={blocked || pending || topic.trim().length < 3}
              title={
                blocked ? "Add an LLM key and a footage key in Setup first" : undefined
              }
            >
              {pending ? "Starting…" : "Generate"}
            </Button>
          </div>
        </div>

        {/* Ideas, not predictions. Every number under these comes from YouTube
            autocomplete — queries people actually typed — scored by the same
            `ideas.score_idea` the channel backlog uses. Nothing here claims to know
            what will go viral, because nothing can. Hidden entirely on a first run:
            with no videos to be adjacent to there is no niche to suggest within,
            and generic filler would be worse than blank. */}
        {ideas.length > 0 && (
          <section className="mt-10">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              Worth making next
            </h2>
            <p className="mt-1 text-[12px] text-[var(--color-faint)]">
              Based on what you have made, ranked by real search demand. Making one
              takes it off the list; dismissing it stops it coming back.
            </p>
            <ul className="mt-3 grid gap-1.5">
              {ideas.map((idea) => (
                <li key={idea.id} className="group flex items-center gap-1">
                  <button
                    onClick={() => {
                      setTopic(idea.topic);
                      setImproved(null);
                    }}
                    className="flex min-w-0 flex-1 items-baseline gap-3 rounded-[var(--radius-btn)] border border-transparent px-3 py-2.5 text-left transition-colors duration-150 hover:border-[var(--color-line)] hover:bg-[var(--color-surface)]"
                  >
                    <span className="min-w-0 flex-1 truncate text-[14px] text-[var(--color-ink)]">
                      {idea.topic}
                    </span>
                    <span className="mono shrink-0 text-[11px] text-[var(--color-faint)]">
                      {idea.why}
                    </span>
                  </button>
                  {/* Removed from the list here and refused in the engine, so it is
                      not re-derived from the same published history next week. The
                      row goes immediately rather than after the round trip: this is
                      a list of suggestions, and one that lingers after you dismiss
                      it feels broken in a way a rare failed delete does not. */}
                  <button
                    onClick={() => {
                      setIdeas((current) => current.filter((i) => i.id !== idea.id));
                      void refuseIdea(idea.id).then((result) => {
                        if (result.ok) return;
                        // Put it back. Removing optimistically is right — a list
                        // that lingers after you dismiss one feels broken — but
                        // *keeping* it removed after a failed write is worse: the
                        // engine still has it, so it returns on the next load and
                        // the dismissal looks like it silently un-did itself.
                        setIdeas((current) =>
                          current.some((i) => i.id === idea.id) ? current : [idea, ...current],
                        );
                        setError(result.error ?? "could not dismiss that idea");
                      });
                    }}
                    aria-label={`Not interested in ${idea.topic}`}
                    title="Not interested"
                    className="shrink-0 rounded-[var(--radius-btn)] px-2 py-2 text-[13px] text-[var(--color-faint)] opacity-0 transition-opacity duration-150 hover:text-[var(--color-ink)] focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="mt-8 text-[13px] leading-relaxed text-[var(--color-faint)]">
          Research runs first — the script is built from sources, not from what the model
          already believes. Every stage is editable before anything is published.
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
  disabled = false,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`rounded-full border px-3.5 py-1.5 text-[13px] transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "border-[var(--color-ink)] text-[var(--color-ink)]"
          : "border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-line-hover)]"
      }`}
    >
      {label}
    </button>
  );
}
