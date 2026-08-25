"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";

import type { Launch, LaunchSummary, StageStatus } from "@studio/contracts";
import {
  applyChannelLaunch,
  createSeriesFromLaunch,
  designChannel,
  pollLaunch,
} from "@/app/actions";
import { Header, Page, Card, Button } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { StatusGlyph } from "@/components/pipeline";

/** How often to ask a running launch where it is. The whole design costs about
 *  two minutes; polling beats an SSE endpoint the launch flow does not have. */
const POLL_MS = 2500;

export function LaunchView({
  live,
  resumable,
  demo,
}: {
  live: boolean;
  resumable: LaunchSummary[];
  demo: Launch;
}) {
  const [niche, setNiche] = useState("");
  const [launch, setLaunch] = useState<Launch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const running = launch?.status === "running";

  // Poll while the design runs. The interval carries the launch id in a ref so a
  // "Start over" mid-run stops the old poller rather than resurrecting it.
  const pollId = useRef<string | null>(null);
  useEffect(() => {
    if (!running || !launch) return;
    pollId.current = launch.id;
    const timer = setInterval(async () => {
      const id = pollId.current;
      if (!id) return;
      const result = await pollLaunch(id);
      if (result.ok && result.data && pollId.current === id) {
        setLaunch(result.data);
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, launch]);

  const start = useCallback(() => {
    if (!live) {
      setLaunch(demo);
      return;
    }
    startTransition(async () => {
      setError(null);
      const result = await designChannel(niche.trim());
      if (result.ok && result.data) setLaunch(result.data);
      else setError(result.error ?? "The engine refused to start the design.");
    });
  }, [live, niche, demo]);

  const resume = useCallback((id: string) => {
    startTransition(async () => {
      setError(null);
      const result = await pollLaunch(id);
      if (result.ok && result.data) setLaunch(result.data);
      else setError(result.error ?? "Could not load that design.");
    });
  }, []);

  if (!launch) {
    return (
      <>
        <Header
          title="New channel"
          meta={
            <span className="flex items-center gap-2">
              <LiveBadge live={live} />
            </span>
          }
        />
        <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[680px] flex-col justify-center px-8 pb-24">
          <label htmlFor="niche" className="text-[15px] text-[var(--color-muted)]">
            What should the channel be about?
          </label>

          <input
            id="niche"
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && niche.trim().length > 2 && start()}
            autoFocus
            placeholder="how large structures fail"
            className="mt-3 w-full border-b border-[var(--color-line)] bg-transparent pb-3 text-[28px] font-semibold outline-none transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-accent)]"
          />

          <div className="mt-6 flex items-center justify-between">
            <p className="text-[13px] text-[var(--color-faint)]">
              Name, handle, About text, keywords, look, series and 30 video ideas.
            </p>
            <Button onClick={start} disabled={pending || niche.trim().length < 3}>
              Design it
            </Button>
          </div>

          {error && (
            <p role="alert" className="mt-4 text-[13px] text-[var(--color-bad)]">
              {error}
            </p>
          )}

          {live && resumable.length > 0 && (
            <div className="mt-10">
              <p className="text-[12px] text-[var(--color-faint)]">
                Or pick up a finished design — the manual steps take days, so these
                survive a restart.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {resumable.map((l) => (
                  <button
                    key={l.id}
                    onClick={() => resume(l.id)}
                    className="rounded-full border border-[var(--color-line)] px-3 py-1 text-[12px] text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)]"
                  >
                    {l.niche}
                  </button>
                ))}
              </div>
            </div>
          )}

          <p className="mt-8 text-[13px] leading-relaxed text-[var(--color-faint)]">
            Grounded in what people actually search for in this niche and what already
            ranks — not invented. YouTube has no API for creating a channel, so the
            last few clicks are yours; everything else is done here.
          </p>
        </div>
      </>
    );
  }

  if (launch.status === "running" || launch.status === "failed" || launch.status === "interrupted") {
    return (
      <Progress
        launch={launch}
        live={live}
        onStartOver={() => {
          pollId.current = null;
          setLaunch(null);
        }}
      />
    );
  }

  return (
    <Result
      launch={launch}
      live={live}
      onStartOver={() => {
        pollId.current = null;
        setLaunch(null);
      }}
    />
  );
}

/** The seven-stage design as a quiet pipeline. Same rows as the Create screen. */
function Progress({
  launch,
  live,
  onStartOver,
}: {
  launch: Launch;
  live: boolean;
  onStartOver: () => void;
}) {
  const failed = launch.status !== "running";
  return (
    <>
      <Header
        title="New channel"
        meta={
          <span className="flex items-center gap-2">
            {launch.niche}
            <LiveBadge live={live} />
          </span>
        }
        action={
          failed ? (
            <Button variant="ghost" onClick={onStartOver}>
              Start over
            </Button>
          ) : undefined
        }
      />
      <Page>
        <Card className="p-2">
          <ul>
            {launch.stages.map((stage) => (
              <li
                key={stage.name}
                className={`flex items-center gap-4 border-b border-[var(--color-line)] px-4 py-3.5 last:border-0 ${
                  stage.status === "running" ? "bg-[var(--color-raised)]" : ""
                }`}
              >
                <StatusGlyph status={stage.status as StageStatus} />
                <span
                  className={`w-[132px] shrink-0 text-[13px] font-semibold ${
                    stage.status === "pending" ? "text-[var(--color-faint)]" : ""
                  }`}
                >
                  {stage.title}
                </span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--color-muted)]">
                  {stage.error ?? stage.summary}
                </span>
              </li>
            ))}
          </ul>
        </Card>
        {launch.status === "failed" && (
          <p role="alert" className="mt-4 text-[13px] text-[var(--color-bad)]">
            {launch.error ?? "The design failed."}
          </p>
        )}
        {launch.status === "interrupted" && (
          <p className="mt-4 text-[13px] text-[var(--color-warn)]">
            The engine restarted while this design was running. Start it again — a
            design is cheap to regenerate.
          </p>
        )}
      </Page>
    </>
  );
}

function Result({
  launch,
  live,
  onStartOver,
}: {
  launch: Launch;
  live: boolean;
  onStartOver: () => void;
}) {
  const [pending, startTransition] = useTransition();
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [seriesCreated, setSeriesCreated] = useState(false);
  const [applied, setApplied] = useState(false);

  const c = launch.identity;
  if (!c) {
    // A completed launch always assembles an identity; this is the belt for a
    // stored row from a build that disagreed.
    return (
      <Page>
        <Card className="p-5 text-[13px] text-[var(--color-faint)]">
          This design has no identity to show. Start it again.
        </Card>
      </Page>
    );
  }

  const plannedSeries = seriesOf(launch);
  const disabledReason = live ? undefined : "The engine is not running.";

  const createSeries = () =>
    startTransition(async () => {
      setNotice(null);
      setFailure(null);
      const result = await createSeriesFromLaunch(launch.niche, plannedSeries);
      if (result.ok) {
        setSeriesCreated(true);
        setNotice(
          `${result.data?.created ?? plannedSeries.length} series created — they are on the Series screen.`,
        );
      } else {
        setFailure(result.error ?? "The engine refused.");
      }
    });

  const apply = () =>
    startTransition(async () => {
      setNotice(null);
      setFailure(null);
      const result = await applyChannelLaunch(launch.id);
      if (result.ok) {
        setApplied(true);
        setNotice("Description, keywords and country pushed to the connected channel.");
      } else {
        // The 409s carry the exact next step — "create the channel first",
        // "connect via OAuth" — and are worth showing verbatim.
        setFailure(result.error ?? "The engine refused.");
      }
    });

  return (
    <>
      <Header
        title={c.name}
        meta={
          <span className="mono flex items-center gap-2">
            @{c.handle}
            {launch.cost_usd > 0 && <span>· ${launch.cost_usd.toFixed(2)}</span>}
            <LiveBadge live={live} />
          </span>
        }
        action={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onStartOver}>
              Start over
            </Button>
            <Button
              disabled={!live || pending || seriesCreated || plannedSeries.length === 0}
              title={disabledReason}
              onClick={createSeries}
            >
              {seriesCreated ? "Series created" : "Create series"}
            </Button>
          </div>
        }
      />
      <Page>
        {notice && (
          <p role="status" className="pb-4 text-[13px] text-[var(--color-ok)]">
            {notice}
          </p>
        )}
        {failure && (
          <p role="alert" className="pb-4 text-[13px] text-[var(--color-bad)]">
            {failure}
          </p>
        )}

        {launch.problems.length > 0 && (
          <Card className="mb-4 border-[var(--color-bad)]/40 p-5">
            <h2 className="text-[14px] font-semibold">The design breaks a real limit</h2>
            <ul className="mt-2 grid gap-1">
              {launch.problems.map((p) => (
                <li key={`${p.field}:${p.message}`} className="text-[12px] text-[var(--color-muted)]">
                  <span className="mono text-[var(--color-faint)]">{p.field}</span> —{" "}
                  {p.message}
                  {p.fatal && (
                    <span className="ml-1 text-[var(--color-bad)]">(blocks apply)</span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {/* The manual steps come first. They are the blocking path, and burying them
            below the generated content would be quietly dishonest. */}
        <Card className="border-[var(--color-warn)]/40 p-5">
          <h2 className="text-[14px] font-semibold">Do these yourself first</h2>
          <p className="mt-1 text-[12px] text-[var(--color-faint)]">
            YouTube has no API for any of this. Everything below is already done.
          </p>
          <ol className="mt-4 grid gap-3">
            {launch.manual_steps.map((step, i) => (
              <li key={step.id} className="flex gap-3">
                <span className="mono mt-0.5 size-5 shrink-0 rounded-full border border-[var(--color-line-hover)] text-center text-[11px] leading-[18px] text-[var(--color-muted)]">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold">
                    {step.url ? (
                      <a
                        href={step.url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline decoration-[var(--color-line-hover)] underline-offset-2 hover:decoration-[var(--color-ink)]"
                      >
                        {step.title}
                      </a>
                    ) : (
                      step.title
                    )}
                  </p>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--color-faint)]">
                    {step.detail}
                  </p>
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex items-center gap-3">
            <Button
              variant="ghost"
              disabled={!live || pending || applied || launch.blocked}
              title={
                launch.blocked
                  ? "Fix the fatal problems above first."
                  : (disabledReason ??
                    "Only after the channel exists and OAuth is connected.")
              }
              onClick={apply}
            >
              {applied ? "Applied" : "Apply description & keywords"}
            </Button>
            <p className="text-[11px] text-[var(--color-faint)]">
              Pushes what the API can set — description, keywords, country. Name,
              handle, avatar and banner stay manual permanently.
            </p>
          </div>
        </Card>

        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <Field label="Handle" value={`@${c.handle}`} note={`${c.handle.length}/30`} />
          <Field label="Tagline" value={c.tagline} note={`${c.tagline.length}/60`} />
        </section>

        <section className="mt-4">
          <Block
            label="About"
            note={`${c.description.length}/1000 characters`}
            body={c.description}
          />
        </section>

        <section className="mt-4">
          <Block
            label="Channel keywords"
            note={`${c.keywords_string.length}/500 characters · ${c.keywords.length} keywords`}
            body={c.keywords_string}
            mono
          />
        </section>

        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <Card className="p-5">
            <p className="text-[12px] text-[var(--color-faint)]">Avatar</p>
            <div className="mt-3 flex items-center gap-4">
              <div
                className="size-[98px] shrink-0 rounded-full"
                style={{ background: c.palette[0] }}
              />
              <p className="text-[12px] leading-relaxed text-[var(--color-muted)]">
                {c.avatar_concept}
              </p>
            </div>
            <p className="mt-3 text-[11px] text-[var(--color-faint)]">
              Shown at true size — 98px is where it actually lives.
            </p>
          </Card>

          <Card className="p-5">
            <p className="text-[12px] text-[var(--color-faint)]">Banner</p>
            <div
              className="relative mt-3 aspect-[2048/1152] w-full overflow-hidden rounded"
              style={{ background: c.palette[1] }}
            >
              <div
                className="absolute inset-0 m-auto border border-dashed border-white/40"
                style={{ width: "60.3%", height: "29.3%" }}
              />
            </div>
            <p className="mt-3 text-[11px] text-[var(--color-faint)]">
              Dashed box is the safe area — the only part visible on every device.
            </p>
          </Card>
        </section>

        <section className="mt-8">
          <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
            Series
          </h2>
          <div className="grid gap-2.5">
            {plannedSeries.map((s) => (
              <Card key={s.name} className="flex items-center gap-4 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-semibold">{s.name}</p>
                  <p className="mt-1 text-[12px] text-[var(--color-faint)]">{s.pattern}</p>
                </div>
                <span className="mono shrink-0 text-[12px] text-[var(--color-muted)]">
                  {s.per_week}/week · {s.format}
                </span>
              </Card>
            ))}
          </div>
        </section>

        <section className="mt-8">
          <div className="flex items-baseline justify-between pb-2.5">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              First ideas
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {launch.backlog.filter((b) => !b.duplicate_of).length} usable
            </span>
          </div>
          <div className="grid gap-1.5">
            {launch.backlog.map((idea, i) => (
              <Card key={idea.topic} className="flex items-center gap-4 px-4 py-2.5">
                <span className="mono w-6 shrink-0 text-[12px] text-[var(--color-faint)]">
                  {i + 1}
                </span>
                <p className="min-w-0 flex-1 truncate text-[13px]">{idea.topic}</p>
                <span className="mono shrink-0 text-[12px] text-[var(--color-muted)]">
                  {idea.duplicate_of ? "duplicate" : idea.score.toFixed(2)}
                </span>
              </Card>
            ))}
          </div>
        </section>
      </Page>
    </>
  );
}

/** The series plan out of the launch's LLM-shaped stage output, defensively. */
function seriesOf(
  launch: Launch,
): { name: string; format: string; pattern: string; per_week: number }[] {
  const raw = (launch.series as { series?: unknown } | null)?.series;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(
      (s): s is { name: string; format: string; pattern?: string; per_week?: number } =>
        !!s && typeof s === "object" && typeof (s as { name?: unknown }).name === "string",
    )
    .map((s) => ({
      name: s.name,
      format: s.format === "long" ? "long" : "short",
      pattern: s.pattern ?? "",
      per_week: typeof s.per_week === "number" ? s.per_week : 1,
    }));
}

function Field({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between">
        <p className="text-[12px] text-[var(--color-faint)]">{label}</p>
        <span className="mono text-[11px] text-[var(--color-faint)]">{note}</span>
      </div>
      <p className="mt-2 text-[16px] font-semibold">{value}</p>
    </Card>
  );
}

function Block({
  label,
  note,
  body,
  mono = false,
}: {
  label: string;
  note: string;
  body: string;
  mono?: boolean;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between">
        <p className="text-[12px] text-[var(--color-faint)]">{label}</p>
        <span className="mono text-[11px] text-[var(--color-faint)]">{note}</span>
      </div>
      <p
        className={`mt-2.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[var(--color-muted)] ${mono ? "mono" : ""}`}
      >
        {body}
      </p>
    </Card>
  );
}
