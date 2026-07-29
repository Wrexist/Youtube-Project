"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { Card, Button } from "@/components/ui";
import { runDiagnostics } from "@/app/actions";
import type { Diagnostics, DiagnosticCheck } from "@studio/contracts";

/**
 * The health checks, with a button.
 *
 * These are the same checks `scripts/doctor.py` runs — one implementation, so the
 * terminal and the screen cannot disagree. Until now they existed only as a
 * script, which meant the answer to "why did my render fail" was behind
 * remembering a virtualenv path, on the machine of someone who by definition has
 * just failed to set this up.
 *
 * The initial report is rendered by the server without the network probe, so the
 * page is not held for six seconds waiting on YouTube's autocomplete. Pressing
 * Run checks includes it.
 */
export function DiagnosticsPanel({ initial }: { initial: Diagnostics | null }) {
  const [report, setReport] = useState<Diagnostics | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [ranFull, setRanFull] = useState(false);
  const [pending, start] = useTransition();

  function check() {
    setError(null);
    start(async () => {
      const result = await runDiagnostics(true);
      if (!result.ok || !result.data) {
        setError(result.error ?? "The checks could not be run.");
        return;
      }
      setReport(result.data);
      setRanFull(true);
    });
  }

  return (
    <section className="mb-8">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 pb-1">
        <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">System check</h2>
        <p className="text-[12px] text-[var(--color-faint)]">
          Everything Studio needs in order to run, and what to do about anything
          missing.
        </p>
      </div>

      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-4">
          <Button variant="ghost" onClick={check} disabled={pending}>
            {pending ? "Checking…" : ranFull ? "Check again" : "Run checks"}
          </Button>
          <p aria-live="polite" className="text-[12px] text-[var(--color-muted)]">
            {!report ? "The engine did not answer." : summarise(report)}
          </p>
        </div>

        {error && (
          <p role="alert" className="mb-4 text-[12px] text-[var(--color-bad)]">
            {error}
          </p>
        )}

        {report && (
          <ul className="grid gap-2.5">
            {report.checks.map((c) => (
              <Row key={c.key} check={c} />
            ))}
          </ul>
        )}

        {!ranFull && report && (
          // Said plainly rather than silently omitted: a green report that skipped
          // a check is not the same as a green report.
          <p className="mt-4 text-[11px] text-[var(--color-faint)]">
            The keyword-grounding probe is skipped on load because it waits up to
            six seconds on a network call. Run checks includes it.
          </p>
        )}
      </Card>
    </section>
  );
}

/**
 * The one-line verdict.
 *
 * Counted rather than phrased around `ready`, which was "All 12 checks pass, 3
 * optional items noted" — a sentence that contradicts itself, since those three
 * are warnings and did not pass. On a screen whose entire job is telling someone
 * the truth about their install, that is the wrong place to be sloppy.
 */
function summarise(report: Diagnostics): string {
  const passing = report.checks.length - report.blockers - report.warnings;
  const optional =
    report.warnings > 0
      ? `, ${report.warnings} optional item${report.warnings > 1 ? "s" : ""} to look at`
      : "";

  if (report.blockers > 0) {
    return `${report.blockers} thing${report.blockers > 1 ? "s" : ""} must be fixed before anything runs${optional}.`;
  }
  return `Nothing is blocking — ${passing} of ${report.checks.length} checks clean${optional}.`;
}

const TONE: Record<string, string> = {
  ok: "var(--color-ok)",
  warn: "var(--color-warn)",
  fail: "var(--color-bad)",
};

const MARK: Record<string, string> = { ok: "✓", warn: "!", fail: "✗" };

function Row({ check }: { check: DiagnosticCheck }) {
  return (
    <li className="flex gap-3">
      {/* The mark carries the state as well as the colour — every state has to be
          readable without relying on hue. */}
      <span
        className="mono mt-px w-3 shrink-0 text-[12px] font-semibold"
        style={{ color: TONE[check.level] }}
        aria-hidden
      >
        {MARK[check.level]}
      </span>
      <span className="sr-only">
        {check.level === "ok" ? "Passing" : check.level === "warn" ? "Warning" : "Failing"}:
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-[13px]">
          <span className="font-semibold">{check.name}</span>
          {check.detail && (
            <span className="text-[var(--color-muted)]"> — {check.detail}</span>
          )}
        </p>

        {check.fix && (
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-faint)]">
            {check.fix}
          </p>
        )}

        {check.command && <Command text={check.command} />}

        {check.href && (
          <Link
            href={check.href}
            className="mt-1.5 inline-block text-[12px] text-[var(--color-muted)] underline decoration-[var(--color-line-hover)] underline-offset-4 hover:text-[var(--color-ink)]"
          >
            Fix this above
          </Link>
        )}
      </div>
    </li>
  );
}

/**
 * A command, with a copy button.
 *
 * Copy rather than run. A button in a browser that executes shell commands on the
 * host is a remote-code-execution surface on a process holding every API key on
 * the machine, and no amount of "it's only localhost" makes that a good trade —
 * any page in any tab can POST to localhost. The few things that genuinely need a
 * terminal stay in the terminal; this just removes the transcription errors.
 */
function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <code className="mono flex-1 overflow-x-auto rounded bg-[var(--color-raised)] px-2 py-1.5 text-[11px] text-[var(--color-muted)]">
        {text}
      </code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        }}
        className="shrink-0 rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2 py-1 text-[11px] text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)]"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
