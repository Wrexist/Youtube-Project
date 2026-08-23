"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Button } from "@/components/ui";
import { saveCredentials, connectYouTube, finishOnboarding } from "@/app/actions";
import type { SetupStatus, CredentialStatus } from "@studio/contracts";

/**
 * The onboarding sequence.
 *
 * Four steps, each with exactly one thing to do and a way past it. The order is
 * by consequence, not by category: the two keys that decide whether anything
 * works at all come first, and everything after them is explicitly optional and
 * says so.
 *
 * Each step saves as you leave it, rather than collecting everything and writing
 * once at the end. Someone who gets three steps in and closes the tab should keep
 * what they entered — an all-or-nothing wizard punishes exactly the people most
 * likely to be interrupted.
 */
export function WelcomeFlow({ setup }: { setup: SetupStatus | null }) {
  const [step, setStep] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const byGroup = useMemo(() => {
    const out: Record<string, CredentialStatus[]> = {};
    for (const c of setup?.credentials ?? []) (out[c.group] ??= []).push(c);
    return out;
  }, [setup]);

  if (!setup) return <EngineDown />;

  /**
   * Write whatever is typed on the current step. Returns false if the save failed,
   * in which case the caller must not move on.
   *
   * Shared by advance() and finish() rather than living inside advance(): Done is
   * the only way off the last step, so when this was inlined in advance() the
   * Google client ID and secret pasted on Publishing were dropped on the floor —
   * the one step whose keys nothing else prompts for.
   */
  async function persist(): Promise<boolean> {
    const values = { ...drafts };
    if (Object.keys(values).length === 0) return true;
    const result = await saveCredentials(values);
    if (!result.ok) {
      setError(result.error ?? "Those keys were not saved.");
      return false;
    }
    setDrafts({});
    // Re-read the setup status the page was rendered with, so "Already set" and
    // the Connect YouTube button update in place. Without it, saving the client
    // ID and secret left Connect disabled until a round-trip through /setup.
    router.refresh();
    return true;
  }

  /** Save whatever was typed on this step, then advance. */
  function advance(to: number) {
    setError(null);
    startTransition(async () => {
      if (!(await persist())) return;
      setStep(to);
    });
  }

  /**
   * Leave onboarding for good. Records it so this does not reappear.
   *
   * `save` is Skip's contract, made explicit: Done writes what is on screen first,
   * Skip discards it. Skip carrying a silent save would be worse than either — the
   * one button whose label promises nothing happens is the wrong place to write
   * credentials.
   */
  function finish(destination: string, save: boolean) {
    setError(null);
    startTransition(async () => {
      if (save) {
        if (!(await persist())) return;
      } else {
        setDrafts({});
      }
      await finishOnboarding();
      router.push(destination);
      router.refresh();
    });
  }

  const edit = (env: string, value: string) =>
    setDrafts((d) => {
      const next = { ...d };
      if (value === "") delete next[env];
      else next[env] = value;
      return next;
    });

  const STEPS = ["Welcome", "Essentials", "Quality", "Publishing"];

  return (
    <div className="mx-auto flex min-h-screen max-w-[720px] flex-col px-8 py-10">
      <Progress steps={STEPS} current={step} />

      {/* Vertically centred rather than top-aligned. A four-field step against a
          full-height column left most of the screen empty below it, which reads as
          a page that failed to finish loading. */}
      {error && (
        <div
          role="alert"
          className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 p-4"
        >
          <p className="text-[13px] text-[var(--color-bad)]">{error}</p>
        </div>
      )}

      <div className="flex flex-1 flex-col justify-center py-4">
        {step === 0 && <Intro />}

        {step === 1 && (
          <Step
            title="The two that matter"
            blurb="One model to write with, one source for footage. Both are free, and this
                   is the whole list — nothing after this step is required."
          >
            {(byGroup.Required ?? []).map((c) => (
              <Field
                key={c.env}
                credential={c}
                value={drafts[c.env] ?? ""}
                disabled={pending}
                onChange={(v) => edit(c.env, v)}
              />
            ))}
          </Step>
        )}

        {step === 2 && (
          <Step
            title="Better output, if you want it"
            blurb="Thumbnails are what decide whether a video gets clicked, and a second
                   footage source means one provider having a bad day is not fatal.
                   Skip this and everything still works."
          >
            {(byGroup.Recommended ?? []).map((c) => (
              <Field
                key={c.env}
                credential={c}
                value={drafts[c.env] ?? ""}
                disabled={pending}
                onChange={(v) => edit(c.env, v)}
              />
            ))}
          </Step>
        )}

        {step === 3 && (
          <Step
            title="Publishing to YouTube"
            blurb="The only part that takes real time — it needs a Google Cloud project,
                   about fifteen minutes. Skip it and you still get a finished MP4 to
                   upload yourself; you can come back to this on the Setup screen."
          >
            {(byGroup.Publishing ?? []).map((c) => (
              <Field
                key={c.env}
                credential={c}
                value={drafts[c.env] ?? ""}
                disabled={pending}
                onChange={(v) => edit(c.env, v)}
              />
            ))}
            <Connect
              setup={setup}
              pending={pending}
              onError={setError}
              typed={(byGroup.Publishing ?? []).some(
                (c) => (drafts[c.env] ?? "").trim() !== "",
              )}
              satisfied={
                setup.can_connect ||
                ((byGroup.Publishing ?? []).length > 0 &&
                  (byGroup.Publishing ?? []).every(
                    (c) => c.configured || (drafts[c.env] ?? "").trim() !== "",
                  ))
              }
              onSave={persist}
            />
          </Step>
        )}
      </div>

      <Footer
        step={step}
        last={STEPS.length - 1}
        pending={pending}
        dirty={Object.keys(drafts).length > 0}
        onBack={() => setStep(step - 1)}
        onNext={() => advance(step + 1)}
        onFinish={() => finish(setup.can_render ? "/" : "/setup", true)}
        onSkipAll={() => finish("/", false)}
      />
    </div>
  );
}

/** Where you are, and how much is left. Four dots, not a percentage. */
function Progress({ steps, current }: { steps: string[]; current: number }) {
  return (
    <nav aria-label="Setup progress" className="mb-10 flex items-center gap-2">
      {steps.map((label, i) => (
        <div key={label} className="flex flex-1 items-center gap-2">
          <span
            aria-current={i === current ? "step" : undefined}
            className="text-[11px] font-semibold tracking-wide uppercase"
            style={{
              color:
                i === current
                  ? "var(--color-ink)"
                  : i < current
                    ? "var(--color-ok)"
                    : "var(--color-faint)",
            }}
          >
            {label}
          </span>
          <span
            aria-hidden
            className="h-px flex-1 rounded"
            style={{
              background: i < current ? "var(--color-ok)" : "var(--color-line)",
            }}
          />
        </div>
      ))}
    </nav>
  );
}

function Intro() {
  return (
    <div>
      <h1 className="text-[30px] leading-tight font-semibold">
        Studio turns an idea into a published video.
      </h1>
      <p className="mt-4 max-w-[58ch] text-[15px] leading-relaxed text-[var(--color-muted)]">
        You type a topic. It researches it, writes a script grounded in real
        sources, narrates it, cuts footage to the narration, burns subtitles,
        designs thumbnails, and writes the SEO package. Nothing is published
        without you approving it.
      </p>

      <dl className="mt-8 grid gap-4">
        <Fact term="What you need" detail="Two API keys. Both free. About five minutes." />
        <Fact
          term="What it costs"
          detail="A few cents of model usage per video. Every video's cost is tracked and shown."
        />
        <Fact
          term="What it does not do"
          detail="Publish behind your back. Approval is a gate you pass through by hand until you turn it off for a specific series."
        />
      </dl>
    </div>
  );
}

function Fact({ term, detail }: { term: string; detail: string }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-[var(--color-line)] pt-4">
      {/* Wide enough for the longest term on one line — at 140px "What it does not
          do" orphaned its last word into a second row. */}
      <dt className="w-[168px] shrink-0 text-[13px] font-semibold">{term}</dt>
      <dd className="flex-1 text-[13px] leading-relaxed text-[var(--color-muted)]">{detail}</dd>
    </div>
  );
}

function Step({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h1 className="text-[24px] font-semibold">{title}</h1>
      <p className="mt-2 max-w-[60ch] text-[14px] leading-relaxed text-[var(--color-muted)]">
        {blurb}
      </p>
      <div className="mt-8 grid gap-5">{children}</div>
    </div>
  );
}

/**
 * One credential.
 *
 * The same contract as the Setup screen's field: an already-set key shows only
 * its last four characters as a placeholder, and leaving the input empty means
 * "keep what is there".
 */
function Field({
  credential,
  value,
  disabled,
  onChange,
}: {
  credential: CredentialStatus;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = `welcome-${credential.env}`;
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <label htmlFor={id} className="text-[14px] font-semibold">
          {credential.label}
        </label>
        {credential.configured && (
          <span className="text-[11px] font-semibold text-[var(--color-ok)]">Already set</span>
        )}
        <a
          href={credential.url}
          target="_blank"
          rel="noreferrer noopener"
          className="ml-auto text-[12px] text-[var(--color-muted)] underline decoration-[var(--color-line-hover)] underline-offset-4 hover:text-[var(--color-ink)]"
        >
          Get a key — {credential.effort}
        </a>
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-muted)]">
        {credential.unlocks}
      </p>
      <input
        id={id}
        type={credential.secret ? "password" : "text"}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          credential.configured
            ? `Set — ends ${credential.tail || "••••"}. Type to replace.`
            : credential.secret
              ? "Paste your key"
              : "Paste the URL"
        }
        className="mono mt-2.5 w-full rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2.5 text-[13px] transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-accent)] focus:outline-none disabled:opacity-50"
      />
    </div>
  );
}

/**
 * The one control on this step that is not a text field.
 *
 * It saves before it connects. Publishing is the last step, so the only button
 * that used to write anything here was Done — which then navigates away, meaning
 * the honest instruction was "press Done and come back from Setup". Saving the two
 * halves inline instead lets the consent flow start from the step that asked for
 * them, which is where someone who just pasted them expects it to work.
 */
function Connect({
  setup,
  pending,
  onError,
  typed,
  satisfied,
  onSave,
}: {
  setup: SetupStatus;
  pending: boolean;
  onError: (message: string) => void;
  /** Something has been pasted into a Publishing field and not yet saved. */
  typed: boolean;
  /** Both OAuth halves will exist once anything typed is saved. */
  satisfied: boolean;
  onSave: () => Promise<boolean>;
}) {
  const [busy, start] = useTransition();
  if (setup.can_publish) {
    return (
      <p className="text-[13px] text-[var(--color-ok)]">
        Connected to {setup.channels.join(", ")}.
      </p>
    );
  }
  return (
    <div className="border-t border-[var(--color-line)] pt-5">
      <p className="text-[13px] text-[var(--color-muted)]">
        {satisfied
          ? typed
            ? "Connect YouTube saves both halves first, then asks Google for consent. Studio requests upload and analytics access, and stores only an encrypted refresh token."
            : "Now pick the channel. Studio asks for upload and analytics access, and stores only an encrypted refresh token."
          : "Paste the client ID and secret above — the consent page cannot be built without them. Press Done to save what you have and finish; the Setup screen has this same button."}
      </p>
      <div className="mt-3">
        <Button
          disabled={!satisfied || pending || busy}
          onClick={() =>
            start(async () => {
              // Anything typed but unsaved has to reach the engine first: it is
              // what builds the consent URL.
              if (typed && !(await onSave())) return;
              const result = await connectYouTube();
              if (!result.ok || !result.data) {
                onError(result.error ?? "Could not start the YouTube connection.");
                return;
              }
              window.location.href = result.data.url;
            })
          }
        >
          Connect YouTube
        </Button>
      </div>
    </div>
  );
}

function Footer({
  step,
  last,
  pending,
  dirty,
  onBack,
  onNext,
  onFinish,
  onSkipAll,
}: {
  step: number;
  last: number;
  pending: boolean;
  dirty: boolean;
  onBack: () => void;
  onNext: () => void;
  onFinish: () => void;
  onSkipAll: () => void;
}) {
  return (
    <div className="mt-12 flex flex-wrap items-center gap-3 border-t border-[var(--color-line)] pt-6">
      {step > 0 && (
        <Button variant="ghost" onClick={onBack} disabled={pending}>
          Back
        </Button>
      )}

      {step < last ? (
        <Button onClick={onNext} disabled={pending}>
          {pending ? "Saving…" : step === 0 ? "Start" : dirty ? "Save and continue" : "Continue"}
        </Button>
      ) : (
        <Button onClick={onFinish} disabled={pending}>
          {pending ? "Finishing…" : "Done"}
        </Button>
      )}

      <button
        type="button"
        onClick={onSkipAll}
        disabled={pending}
        className="ml-auto text-[12px] text-[var(--color-faint)] underline underline-offset-4 transition-colors duration-150 hover:text-[var(--color-muted)] disabled:opacity-50"
      >
        Skip — I&apos;ll do this later
      </button>
    </div>
  );
}

function EngineDown() {
  return (
    <div className="mx-auto flex min-h-screen max-w-[560px] flex-col justify-center px-8">
      <h1 className="text-[22px] font-semibold">The engine is not running</h1>
      <p className="mt-3 text-[14px] leading-relaxed text-[var(--color-muted)]">
        Setup reads and writes the engine&apos;s configuration, so there is nothing
        to fill in until it is up. Stop this and run <span className="mono">npm start</span>,
        which starts both halves together, then reload.
      </p>
      <p className="mt-6 text-[13px]">
        <Link href="/" className="text-[var(--color-muted)] underline underline-offset-4">
          Look around on demo data instead
        </Link>
      </p>
    </div>
  );
}
