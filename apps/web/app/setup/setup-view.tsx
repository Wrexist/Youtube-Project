"use client";

import { useMemo, useState, useTransition } from "react";
import { useSearchParams } from "next/navigation";
import { Card, Button } from "@/components/ui";
import { saveCredentials, connectYouTube } from "@/app/actions";
import type { SetupStatus, CredentialStatus } from "@studio/contracts";

/**
 * The interactive half of Setup.
 *
 * Two things it deliberately does not do.
 *
 * **It never receives a credential it did not just take from a form field.** The
 * engine reports `configured` and the last four characters and nothing more, so a
 * key cannot be recovered from this page's HTML, its props, or the network tab.
 * The input for an already-set key is empty with the tail as its placeholder;
 * leaving it empty means "keep what is there", which is why the save below sends
 * only the fields that were actually typed into.
 *
 * **It does not stage changes.** There is one Save, it writes everything pending,
 * and the engine returns the resulting status — so what the screen shows after a
 * save is what is in force, not what the browser hoped it had set.
 */
export function SetupView({ setup }: { setup: SetupStatus }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [pending, startTransition] = useTransition();

  const params = useSearchParams();
  // Google sends the operator back here after consent. Without something saying
  // so, a successful connection looked identical to a cancelled one.
  const justConnected = params.get("connected") === "1";

  const groups = useMemo(() => {
    const out: Record<string, CredentialStatus[]> = {};
    for (const c of setup.credentials) (out[c.group] ??= []).push(c);
    return out;
  }, [setup.credentials]);

  const dirty = Object.keys(drafts).length > 0;

  /**
   * One edit to one field.
   *
   * Retiring the "Saved" confirmation happens here rather than in an effect
   * watching `drafts`: the confirmation is a consequence of the keystroke, not of
   * the state that keystroke produced, and deriving it in an effect costs a
   * second render for something already known at the event.
   */
  function edit(env: string, value: string) {
    setSaved(false);
    setDrafts((d) => {
      const next = { ...d };
      // An emptied field is removed rather than sent as "". Absent means
      // unchanged; "" means clear this key, and someone who typed and then
      // deleted meant the former.
      if (value === "") delete next[env];
      else next[env] = value;
      return next;
    });
  }

  function save() {
    setError(null);
    const values = { ...drafts };
    startTransition(async () => {
      const result = await saveCredentials(values);
      if (!result.ok) {
        setError(result.error ?? "The keys were not saved.");
        return;
      }
      // Cleared only on success. A failed save that emptied the fields would lose
      // a key someone had just pasted, and they would have to fetch it again.
      setDrafts({});
      setSaved(true);
    });
  }

  function connect() {
    setError(null);
    startTransition(async () => {
      const result = await connectYouTube();
      if (!result.ok || !result.data) {
        setError(result.error ?? "Could not start the YouTube connection.");
        return;
      }
      // A full navigation, not a fetch. Google's consent page has to be loaded by
      // the person's own browser, signed in as themselves.
      window.location.href = result.data.url;
    });
  }

  return (
    <>
      <Status setup={setup} justConnected={justConnected} />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 bg-[var(--color-surface)] p-4"
        >
          <p className="text-[13px] text-[var(--color-bad)]">{error}</p>
        </div>
      )}

      {Object.entries(groups).map(([group, credentials]) => (
        <section key={group} className="mb-8">
          <h2 className="pb-1 text-[13px] font-semibold text-[var(--color-muted)]">
            {group}
          </h2>
          <p className="pb-3 text-[12px] text-[var(--color-faint)]">{GROUP_NOTE[group]}</p>
          <div className="grid gap-2.5">
            {credentials.map((c) => (
              <Field
                key={c.env}
                credential={c}
                value={drafts[c.env] ?? ""}
                disabled={pending}
                onChange={(v) => edit(c.env, v)}
              />
            ))}
          </div>

          {group === "Publishing" && (
            <YouTubeConnection setup={setup} pending={pending} onConnect={connect} />
          )}
        </section>
      ))}

      {/* Sticky, because the fields are longer than a viewport and a Save that has
          scrolled out of sight is a Save people do not press. */}
      <div className="sticky bottom-0 -mx-8 border-t border-[var(--color-line)] bg-[var(--color-bg)]/90 px-8 py-4 backdrop-blur">
        <div className="flex items-center gap-4">
          <Button onClick={save} disabled={!dirty || pending}>
            {pending ? "Saving…" : "Save"}
          </Button>
          <p aria-live="polite" className="text-[12px] text-[var(--color-muted)]">
            {saved
              ? "Saved. They take effect immediately."
              : dirty
                ? `${Object.keys(drafts).length} change${Object.keys(drafts).length > 1 ? "s" : ""} to save`
                : "Nothing to save."}
          </p>
          {saved && setup.worker_running && (
            // Only when it is true. The zero-config path runs renders inside the
            // API process, where the change is already live, and a restart notice
            // there would send someone to restart something that is not running.
            <p className="text-[12px] text-[var(--color-warn)]">
              A render worker is running — restart it to pick these up.
            </p>
          )}
        </div>
      </div>

      <p className="mt-6 text-[12px] leading-relaxed text-[var(--color-faint)]">
        Written to <span className="mono">{setup.env_path}</span>, which is
        gitignored and readable only by you. Keys are never sent anywhere but this
        machine, and this screen cannot read one back — it only ever shows the last
        four characters.
      </p>
    </>
  );
}

const GROUP_NOTE: Record<string, string> = {
  Required: "Without these, nothing renders. Both are free and take under five minutes.",
  Recommended: "Each one makes the output better. None of them is needed to start.",
  Publishing:
    "Only needed to upload to YouTube. Everything else works without it — you download the file instead.",
};

/** The headline: can this install do the thing, and if not, what is missing. */
function Status({ setup, justConnected }: { setup: SetupStatus; justConnected: boolean }) {
  if (justConnected && setup.can_publish) {
    return (
      <Card className="mb-6 border-[var(--color-ok)]/40 p-5">
        <h2 className="text-[15px] font-semibold text-[var(--color-ok)]">
          YouTube connected
        </h2>
        <p className="mt-1.5 text-[13px] text-[var(--color-muted)]">
          {setup.channels.join(", ")} — publishing is available from the approval
          gate on any finished video.
        </p>
      </Card>
    );
  }

  if (setup.can_render) {
    return (
      <Card className="mb-6 border-[var(--color-ok)]/40 p-5">
        <h2 className="text-[15px] font-semibold text-[var(--color-ok)]">
          Ready to make videos
        </h2>
        <p className="mt-1.5 max-w-[64ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
          {setup.can_publish
            ? "Everything is configured, including publishing to YouTube. Go to Create and type a topic."
            : "Go to Create and type a topic. Publishing to YouTube needs the section below; everything up to a finished MP4 does not."}
        </p>
      </Card>
    );
  }

  // Counted, not hardcoded. "Two keys away" stayed on screen after one of the two
  // had been saved, which reads as a save that did not take.
  const outstanding = setup.credentials.filter((c) => c.required && !c.configured);
  const one = outstanding.length === 1;

  return (
    <Card className="mb-6 border-[var(--color-warn)]/40 p-5">
      <h2 className="text-[15px] font-semibold text-[var(--color-warn)]">
        {one
          ? `One key away — ${outstanding[0].label}`
          : `${outstanding.length || "A few"} keys away from a working install`}
      </h2>
      <p className="mt-1.5 max-w-[64ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
        {one
          ? outstanding[0].unlocks
          : "A model writes the script and a stock-footage provider sources what it is cut against."}{" "}
        Free, and below. Nothing else on this page is needed to generate a video.
      </p>
    </Card>
  );
}

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
  const id = `cred-${credential.env}`;
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <label htmlFor={id} className="text-[14px] font-semibold">
          {credential.label}
        </label>
        {credential.configured ? (
          <span className="text-[11px] font-semibold text-[var(--color-ok)]">Set</span>
        ) : credential.required ? (
          <span className="text-[11px] font-semibold text-[var(--color-warn)]">
            Required
          </span>
        ) : (
          <span className="text-[11px] text-[var(--color-faint)]">Optional</span>
        )}
        <span className="mono ml-auto text-[11px] text-[var(--color-faint)]">
          {credential.env}
        </span>
      </div>

      <p className="mt-1.5 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
        {credential.unlocks}{" "}
        {!credential.configured && (
          <span className="text-[var(--color-faint)]">{credential.without_it}</span>
        )}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <input
          id={id}
          type="password"
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            credential.configured
              ? `Set — ends ${credential.tail || "••••"}. Type to replace.`
              : "Paste your key"
          }
          className="mono min-w-[260px] flex-1 rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[12px] transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-line-hover)] focus:outline-none disabled:opacity-50"
        />
        <a
          href={credential.url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-[12px] text-[var(--color-muted)] underline decoration-[var(--color-line-hover)] underline-offset-4 hover:text-[var(--color-ink)]"
        >
          Get one
        </a>
        <span className="text-[11px] text-[var(--color-faint)]">{credential.effort}</span>
      </div>
    </Card>
  );
}

/** The one control on this screen that is not a text field. */
function YouTubeConnection({
  setup,
  pending,
  onConnect,
}: {
  setup: SetupStatus;
  pending: boolean;
  onConnect: () => void;
}) {
  return (
    <Card className="mt-2.5 p-4">
      <p className="text-[14px] font-semibold">YouTube channel</p>
      <p className="mt-1.5 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
        {setup.can_publish
          ? `Connected to ${setup.channels.join(", ")}. Reconnecting replaces it — do that if you moved to a different channel, or after changing the OAuth client above.`
          : setup.can_connect
            ? "Opens Google's consent page. Studio asks for upload and analytics access to the channel you pick, and stores only a refresh token, encrypted."
            : "Save the client ID and secret above first — the consent page cannot be built without them."}
      </p>
      <div className="mt-3">
        <Button
          variant={setup.can_publish ? "ghost" : "primary"}
          onClick={onConnect}
          disabled={!setup.can_connect || pending}
        >
          {setup.can_publish ? "Reconnect" : "Connect YouTube"}
        </Button>
      </div>
    </Card>
  );
}
