"use client";

import { useState } from "react";
import type { Clip, ClipGrantRequest } from "@studio/contracts";
import { Button, Card, Empty } from "@/components/ui";
import { saveGrant } from "@/app/actions";
import { EpisodeBuilder } from "./episode-builder";
import { REPURPOSE_CLIPS, REPURPOSE_REPORT } from "@/lib/demo";

type DemoClip = (typeof REPURPOSE_CLIPS)[number];
type Report = typeof REPURPOSE_REPORT;

/**
 * The lanes, in the order they matter.
 *
 * Only two are being built, and they do different jobs: Lane A (own) grows the
 * channel with zero rights risk, Lane B (campaign) earns, because the rights
 * holder funds the clipping and pays per verified view. The other three exist in
 * the engine and are not offered here yet — an option that leads nowhere is worse
 * than one that is absent.
 */
const LANES = [
  {
    id: "own",
    label: "My own clip",
    blurb: "Your account. No counterparty, nothing to evidence.",
  },
  {
    id: "campaign",
    label: "Paid campaign",
    blurb: "A funded clip programme. Enrolment is the permission — and it pays per view.",
  },
] as const;

/** How a rights state reads at a glance. Never colour alone — each carries a mark
 *  and a word, per the accessibility rule in docs/UI-DESIGN.md. */
function RightsChip({ lane, cleared }: { lane: string | null; cleared: boolean }) {
  const [mark, label, tone] = !lane
    ? ["○", "no rights", "var(--color-faint)"]
    : cleared
      ? ["✓", lane === "own" ? "yours" : "cleared", "var(--color-ok)"]
      : ["!", "lapsed", "var(--color-bad)"];

  return (
    <span className="mono inline-flex items-center gap-1.5 text-[12px]" style={{ color: tone }}>
      <span aria-hidden>{mark}</span>
      {label}
    </span>
  );
}

function severityTone(severity: string) {
  if (severity === "block") return "var(--color-bad)";
  if (severity === "warn") return "var(--color-warn)";
  return "var(--color-ok)";
}

function severityMark(severity: string) {
  if (severity === "block") return "✕";
  if (severity === "warn") return "!";
  return "✓";
}

export function RepurposeView({
  clips,
  demoClips,
  demoReport,
}: {
  clips: Clip[] | null;
  demoClips: DemoClip[];
  demoReport: Report;
}) {
  const live = clips !== null;
  // One shape for both paths, so the card markup below has no idea which it got.
  const rows = live
    ? clips.map((c) => ({
        id: c.id,
        handle: c.creator_handle,
        caption: c.caption,
        duration: c.duration_s,
        fit: c.fit_score,
        reasons: c.fit_reasons,
        lane: c.grant?.lane ?? null,
        cleared: c.cleared,
        views: (c.stats as { views?: number })?.views,
      }))
    : demoClips.map((c) => ({
        id: c.id,
        handle: c.creator_handle,
        caption: c.caption,
        duration: c.duration_s,
        fit: c.fit_score,
        reasons: c.fit_reasons,
        lane: c.lane as string | null,
        cleared: c.cleared,
        views: c.stats.views,
      }));

  const [openId, setOpenId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const open = rows.find((r) => r.id === openId) ?? null;

  const buildable = selected.length > 0;

  if (rows.length === 0) {
    return (
      <Empty
        title="No candidates yet"
        hint="Discovery finds clips that fit this channel. Nothing has been swept in yet."
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((clip) => {
          const isSelected = selected.includes(clip.id);
          return (
            // An article with the caption as its accessible name: each card is a
            // self-contained thing about one clip, and it gives the rights chip and
            // the add button something to belong to for a screen reader.
            <article key={clip.id} aria-label={clip.caption}>
              <Card className={isSelected ? "ring-1 ring-[var(--color-accent)]" : ""}>
              <button
                type="button"
                onClick={() => setOpenId(clip.id)}
                className="block w-full p-4 text-left"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="mono truncate text-[12px] text-[var(--color-muted)]">
                    {clip.handle}
                  </span>
                  <RightsChip lane={clip.lane} cleared={clip.cleared} />
                </div>
                <p className="mt-2.5 line-clamp-2 text-[14px] leading-snug font-semibold">
                  {clip.caption}
                </p>
                <p className="mono mt-2 text-[12px] text-[var(--color-faint)]">
                  {Math.round(clip.duration)}s
                  {clip.views ? ` · ${(clip.views / 1000).toFixed(0)}k views` : ""}
                  {` · fit ${Math.round(clip.fit * 100)}`}
                </p>
              </button>
              <div className="border-t border-[var(--color-line)] px-4 py-2.5">
                {/* Disabled-and-explained rather than absent: the control is what
                    tells you what the screen is for, and a button that silently
                    does nothing is the thing queue/page.tsx already rules out. */}
                <button
                  type="button"
                  disabled={!clip.cleared}
                  title={
                    clip.cleared
                      ? undefined
                      : "Record how this clip may be used before building with it"
                  }
                  onClick={() =>
                    setSelected((current) =>
                      current.includes(clip.id)
                        ? current.filter((id) => id !== clip.id)
                        : [...current, clip.id],
                    )
                  }
                  className="text-[13px] font-semibold text-[var(--color-muted)] transition-colors duration-150 not-disabled:hover:text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {isSelected ? "Remove from episode" : "Add to episode"}
                </button>
              </div>
              </Card>
            </article>
          );
        })}
      </div>

      {buildable ? (
        <EpisodeBuilder
          clips={rows
            .filter((r) => selected.includes(r.id))
            .map((r) => ({
              id: r.id,
              handle: r.handle,
              caption: r.caption,
              duration: r.duration,
              lane: r.lane,
              cleared: r.cleared,
            }))}
          live={live}
          onRemove={(id) => setSelected((current) => current.filter((s) => s !== id))}
        />
      ) : (
        <p className="text-[13px] text-[var(--color-muted)]">
          Add a cleared clip to start an episode.
        </p>
      )}

      <OriginalityCard report={demoReport} live={live} />

      {open && (
        <ClipPanel
          clip={open}
          live={live}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}

/**
 * The gate, as a reader sees it.
 *
 * Two verdicts, never one number. The failure this layout prevents is an operator
 * reading "62%" and having no idea whether the problem is a missing licence or a
 * lazy edit — which need entirely different work.
 */
function OriginalityCard({ report, live }: { report: Report; live: boolean }) {
  const blocks = report.transformation.signals.filter((s) => s.severity === "block");
  const warnings = report.transformation.signals.filter((s) => s.severity === "warn");

  return (
    <Card className="p-5">
      <div className="flex items-center gap-3">
        <h2 className="text-[15px] font-semibold">{report.headline}</h2>
        {!live && (
          <span className="mono text-[11px] text-[var(--color-faint)]">example report</span>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-[var(--radius-card)] border border-[var(--color-line)] p-3">
          <dt className="text-[12px] text-[var(--color-faint)]">Rights</dt>
          <dd
            className="mt-1 text-[14px] font-semibold"
            style={{ color: report.rights.cleared ? "var(--color-ok)" : "var(--color-bad)" }}
          >
            {report.rights.cleared ? "✓ cleared" : "✕ not cleared"}
          </dd>
          <p className="mt-1 text-[12px] text-[var(--color-muted)]">
            May we use this footage. Answered by a licence, an enrolment, or ownership.
          </p>
        </div>
        <div className="rounded-[var(--radius-card)] border border-[var(--color-line)] p-3">
          <dt className="text-[12px] text-[var(--color-faint)]">Transformation</dt>
          <dd
            className="mt-1 text-[14px] font-semibold"
            style={{
              color: report.transformation.passed ? "var(--color-ok)" : "var(--color-bad)",
            }}
          >
            {report.transformation.passed ? "✓ original enough" : `✕ ${blocks.length} failing`}
          </dd>
          <p className="mt-1 text-[12px] text-[var(--color-muted)]">
            Is the result monetisable. A licence buys nothing here — permission and
            originality are judged separately.
          </p>
        </div>
      </dl>

      <ul className="mt-4 flex flex-col gap-2">
        {[...blocks, ...warnings].map((signal) => (
          <li key={signal.name} className="flex items-start gap-2.5 text-[13px]">
            <span
              aria-hidden
              className="mono mt-px shrink-0"
              style={{ color: severityTone(signal.severity) }}
            >
              {severityMark(signal.severity)}
            </span>
            <span className="sr-only">
              {signal.severity === "block" ? "Blocking:" : "Warning:"}
            </span>
            <span className="text-[var(--color-muted)]">{signal.message}</span>
          </li>
        ))}
      </ul>

      <p className="mono mt-4 text-[11px] text-[var(--color-faint)]">
        thresholds v{report.thresholds_version} · calibrated to policy language, not to a
        published algorithm
      </p>
    </Card>
  );
}

/**
 * The rights panel. This is the screen's real content.
 *
 * Only the two built lanes are offered. Both write a grant; the difference is what
 * evidence each needs, which is why the form changes rather than growing a
 * conditional field nobody reads.
 */
function ClipPanel({
  clip,
  live,
  onClose,
}: {
  clip: { id: string; handle: string; caption: string; lane: string | null; cleared: boolean };
  live: boolean;
  onClose: () => void;
}) {
  const [lane, setLane] = useState<string>(clip.lane ?? "own");
  const [grantor, setGrantor] = useState("");
  const [evidence, setEvidence] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{
    error?: string;
    blockers?: { code: string; message: string }[];
    saved?: boolean;
  } | null>(null);

  // Lane A has no counterparty, so it needs nothing. Lane B does, and the button
  // stays disabled until it has it — the engine refuses an unevidenced grant, and
  // finding that out after a round trip is a worse version of the same answer.
  const complete = lane === "own" || (grantor.trim() !== "" && evidence.trim() !== "");

  async function submit() {
    setSaving(true);
    setResult(null);
    const outcome = await saveGrant(clip.id, {
      lane: lane as ClipGrantRequest["lane"],
      grantor: grantor.trim(),
      evidence_kind: lane === "campaign" ? "campaign_enrolment" : "self",
      evidence_ref: lane === "campaign" ? evidence.trim() : "self",
      platforms: [],
      rules: "",
      expires_at: null,
    });
    setSaving(false);
    setResult(
      outcome.ok
        ? { saved: true }
        : { error: outcome.error, blockers: outcome.blockers },
    );
  }

  return (
    <div
      role="dialog"
      aria-label={`Rights for ${clip.handle}`}
      className="fixed inset-y-0 right-0 z-20 w-full max-w-md overflow-y-auto border-l border-[var(--color-line)] bg-[var(--color-surface)] p-6"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="mono text-[12px] text-[var(--color-muted)]">{clip.handle}</p>
          <h2 className="mt-1 text-[16px] leading-snug font-semibold">{clip.caption}</h2>
        </div>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>

      <h3 className="mt-7 text-[13px] font-semibold">How may this clip be used?</h3>
      <p className="mt-1 text-[12px] text-[var(--color-muted)]">
        Media is never fetched without an answer here. A clip with no recorded basis stays a
        link and a view count.
      </p>

      <div className="mt-4 flex flex-col gap-2">
        {LANES.map((option) => (
          <label
            key={option.id}
            className={`cursor-pointer rounded-[var(--radius-card)] border p-3 transition-colors duration-150 ${
              lane === option.id
                ? "border-[var(--color-accent)]"
                : "border-[var(--color-line)] hover:border-[var(--color-line-hover)]"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <input
                type="radio"
                name="lane"
                value={option.id}
                checked={lane === option.id}
                onChange={() => setLane(option.id)}
                className="accent-[var(--color-accent)]"
              />
              <span className="text-[13px] font-semibold">{option.label}</span>
            </div>
            <p className="mt-1 pl-6 text-[12px] text-[var(--color-muted)]">{option.blurb}</p>
          </label>
        ))}
      </div>

      {lane === "campaign" && (
        <div className="mt-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] text-[var(--color-faint)]">Who runs the campaign</span>
            <input
              value={grantor}
              onChange={(event) => setGrantor(event.target.value)}
              className="rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)] px-3 py-2 text-[13px]"
              placeholder="@streamer"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] text-[var(--color-faint)]">
              Link to the enrolment or terms
            </span>
            <input
              value={evidence}
              onChange={(event) => setEvidence(event.target.value)}
              className="rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)] px-3 py-2 text-[13px]"
              placeholder="https://…"
            />
            {/* Said once, here, because it is the reason the field is required. */}
            <span className="text-[11px] text-[var(--color-faint)]">
              An unevidenced grant is a claim that you have permission, not a record of it —
              and six months from now is exactly when it gets checked.
            </span>
          </label>
        </div>
      )}

      <div className="mt-6 flex items-center gap-3">
        <Button
          onClick={submit}
          disabled={!live || saving || !complete || result?.saved}
          title={
            !live
              ? "Recording a grant needs the engine running"
              : !complete
                ? "Name who runs the campaign and link the terms"
                : undefined
          }
        >
          {saving ? "Recording…" : result?.saved ? "Recorded" : "Record"}
        </Button>
        {result?.saved && (
          <span className="text-[12px] text-[var(--color-ok)]">
            Cleared to use — the edit is still judged separately.
          </span>
        )}
      </div>

      {/* One line per problem, never summarised. A missing grantor and a missing
          evidence link are two fixes, and collapsing them sends the operator
          round the loop twice. */}
      {result?.error && (
        <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--color-bad)] p-3">
          <p className="text-[13px] font-semibold text-[var(--color-bad)]">{result.error}</p>
          {result.blockers && (
            <ul className="mt-2 flex flex-col gap-1">
              {result.blockers.map((problem) => (
                <li key={problem.code} className="text-[12px] text-[var(--color-muted)]">
                  {problem.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="mt-6 border-t border-[var(--color-line)] pt-4 text-[12px] text-[var(--color-muted)]">
        Recording rights does not make the finished video monetisable. YouTube judges reuse
        separately from copyright, and it applies{" "}
        <em>regardless of whether the creator agreed</em> — so the edit still has to add
        something a viewer can point at.
      </p>
    </div>
  );
}
