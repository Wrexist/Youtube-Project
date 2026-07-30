"use client";

import Link from "next/link";
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
  // Google's refusal, handed here by the engine's callback rather than rendered as
  // an API error on a blank page. See `ConnectError` for why `access_denied` gets
  // a paragraph of its own.
  const connectError = params.get("connect_error");

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

      {connectError && <ConnectError code={connectError} />}

      {Object.entries(groups).map(([group, credentials]) => (
        <section key={group} className="mb-8">
          <h2 className="pb-1 text-[13px] font-semibold text-[var(--color-muted)]">
            {group}
          </h2>
          <p className="pb-3 text-[12px] text-[var(--color-faint)]">
            {GROUP_NOTE[group]}
          </p>
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
            <>
              <GoogleCloudSteps />
              <YouTubeConnection
                setup={setup}
                pending={pending}
                onConnect={connect}
              />
            </>
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
          <p
            aria-live="polite"
            className="text-[12px] text-[var(--color-muted)]"
          >
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
        gitignored and readable only by you. Keys are never sent anywhere but
        this machine, and this screen cannot read one back — it only ever shows
        the last four characters.{" "}
        <Link
          href="/welcome"
          className="underline decoration-[var(--color-line-hover)] underline-offset-4 hover:text-[var(--color-muted)]"
        >
          Walk through the guided setup again
        </Link>
        .
      </p>
    </>
  );
}

const GROUP_NOTE: Record<string, string> = {
  Required:
    "Without these, nothing renders. Both are free and take under five minutes.",
  Recommended:
    "Each one makes the output better. None of them is needed to start.",
  Publishing:
    "Only needed to upload to YouTube. Everything else works without it — you download the file instead.",
};

/**
 * Google refused the connection, and what to do about it.
 *
 * `access_denied` is worth this much space because its name is a lie in the
 * commonest case: it is not "you clicked cancel", it is "this Cloud project is
 * still in Testing and the account that just signed in is not on its test-user
 * list". Google's own page says so in a paragraph that reads like a dead end, and
 * the fix is one button on a console page most people have never opened.
 */
function ConnectError({ code }: { code: string }) {
  const denied = code === "access_denied";

  return (
    <div
      role="alert"
      className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-warn)]/40 bg-[var(--color-surface)] p-5"
    >
      <p className="text-[14px] font-semibold text-[var(--color-warn)]">
        Google did not complete the connection
        <span className="mono ml-2 text-[11px] font-normal text-[var(--color-faint)]">
          {code}
        </span>
      </p>

      {denied ? (
        <>
          <p className="mt-2 max-w-[70ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
            Despite the name, this is almost never someone pressing cancel. It
            means the Google Cloud project is still in <em>Testing</em>, and the
            account you signed in with is not one of its test users.
          </p>
          <p className="mt-3 max-w-[70ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
            Fix it on the{" "}
            <a
              href="https://console.cloud.google.com/auth/audience"
              target="_blank"
              rel="noreferrer noopener"
              className="text-[var(--color-ink)] underline decoration-[var(--color-line-hover)] underline-offset-4"
            >
              Audience page
            </a>{" "}
            — check the project name at the top is the one your client ID came
            from, then either press <strong>Publish app</strong> (recommended:
            it drops the test-user list entirely, and stops Testing mode
            expiring your refresh token every seven days) or add the exact
            address you signed in with under <strong>Test users</strong>. Then
            press Connect YouTube again.
          </p>
        </>
      ) : (
        <p className="mt-2 max-w-[70ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
          That is Google&apos;s own error code, returned instead of an
          authorisation. Nothing was changed here. If it is{" "}
          <span className="mono">redirect_uri_mismatch</span>, the URI
          registered on the OAuth client does not match the one below, character
          for character.
        </p>
      )}
    </div>
  );
}

/** The headline: can this install do the thing, and if not, what is missing. */
function Status({
  setup,
  justConnected,
}: {
  setup: SetupStatus;
  justConnected: boolean;
}) {
  if (justConnected && setup.can_publish) {
    return (
      <Card className="mb-6 border-[var(--color-ok)]/40 p-5">
        <h2 className="text-[15px] font-semibold text-[var(--color-ok)]">
          YouTube connected
        </h2>
        <p className="mt-1.5 text-[13px] text-[var(--color-muted)]">
          {setup.channels.join(", ")} — publishing is available from the
          approval gate on any finished video.
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
  const outstanding = setup.credentials.filter(
    (c) => c.required && !c.configured,
  );
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
        Free, and below. Nothing else on this page is needed to generate a
        video.
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
          <span className="text-[11px] font-semibold text-[var(--color-ok)]">
            Set
          </span>
        ) : credential.required ? (
          <span className="text-[11px] font-semibold text-[var(--color-warn)]">
            Required
          </span>
        ) : (
          <span className="text-[11px] text-[var(--color-faint)]">
            Optional
          </span>
        )}
        <span className="mono ml-auto text-[11px] text-[var(--color-faint)]">
          {credential.env}
        </span>
      </div>

      <p className="mt-1.5 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
        {credential.unlocks}{" "}
        {!credential.configured && (
          <span className="text-[var(--color-faint)]">
            {credential.without_it}
          </span>
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
        <span className="text-[11px] text-[var(--color-faint)]">
          {credential.effort}
        </span>
      </div>
    </Card>
  );
}

/**
 * Where the two Google values come from, in the order you click them.
 *
 * Collapsed, because it is five steps of someone else's console and it is dead
 * weight to anyone already connected. But *present*, and on this screen rather
 * than only in the guided flow — skipping the welcome screen once should not be
 * the thing that permanently hides the instructions. That was the report: "I
 * skipped the link to Google Cloud APIs and now I can't go back."
 *
 * Every link goes straight to the page the step is about, so nobody has to find
 * "APIs & Services" in a console they have never seen.
 */
function GoogleCloudSteps() {
  const steps = [
    {
      body: "Create a Google Cloud project. Any name; nothing is billed.",
      href: "https://console.cloud.google.com/projectcreate",
      link: "New project",
    },
    {
      body: "Enable YouTube Data API v3 — this is the one that uploads.",
      href: "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
      link: "Enable Data API",
    },
    {
      body: "Enable YouTube Analytics API — this is the one that measures.",
      href: "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
      link: "Enable Analytics API",
    },
    {
      body:
        "Consent screen — now called Google Auth Platform. Under Branding, an " +
        "app name and your own email. Under Audience, User type External, then " +
        "Test users → Add users → your own Google account. No verification is " +
        "needed while you are the only user.",
      href: "https://console.cloud.google.com/auth/overview",
      link: "Consent screen",
    },
    {
      body:
        "Create client → Application type Web application (not Desktop app), " +
        "then Authorised redirect URIs → Add URI → the URI below. Google then " +
        "shows the client ID and secret: paste them into the two fields above " +
        "and press Save.",
      href: "https://console.cloud.google.com/auth/clients",
      link: "Create client",
    },
  ];

  return (
    <details className="group mt-2.5">
      <summary className="cursor-pointer list-none text-[13px] font-semibold text-[var(--color-muted)] hover:text-[var(--color-ink)]">
        Where these two values come from
        <span className="ml-2 text-[11px] font-normal text-[var(--color-faint)] group-open:hidden">
          5 steps, about ten minutes, once
        </span>
      </summary>

      <Card className="mt-3 p-5">
        <p className="max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
          These are not a second login. They identify your copy of Studio to
          Google, and they have to be yours because the API&apos;s 10,000 units
          a day — about six uploads — are counted per Google Cloud project. A
          key shipped with Studio would mean sharing those six with everyone who
          installed it.
        </p>

        <ol className="mt-4 grid gap-3">
          {steps.map((step, index) => (
            <li key={step.href} className="flex gap-3">
              <span className="mono mt-px text-[11px] text-[var(--color-faint)]">
                {index + 1}
              </span>
              <p className="max-w-[64ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
                {step.body}{" "}
                <a
                  href={step.href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="whitespace-nowrap text-[var(--color-ink)] underline decoration-[var(--color-line-hover)] underline-offset-4"
                >
                  {step.link}
                </a>
              </p>
            </li>
          ))}
        </ol>

        <div className="mt-4 border-t border-[var(--color-line)] pt-4">
          <p className="text-[12px] text-[var(--color-muted)]">
            The redirect URI for step 5 — it must match character for character,
            or Google answers{" "}
            <span className="mono">redirect_uri_mismatch</span>:
          </p>
          <CopyLine value="http://localhost:8080/v1/auth/google/callback" />
          <p className="mt-3 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-faint)]">
            On the &ldquo;Google hasn&apos;t verified this app&rdquo; screen,
            choose Advanced, then continue. That is what your own unverified
            test app looks like, and it is expected. Leave every permission
            ticked — an unticked upload scope fails when you publish a video,
            not before.
          </p>
          <p className="mt-2 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-faint)]">
            Once it works, press Publish app on the Audience page. A project
            left in Testing expires its refresh token after seven days, which is
            why a connection that worked can quietly need redoing a week later.
            Publishing changes nothing else — it stays unverified, warning
            screen included.
          </p>
        </div>
      </Card>
    </details>
  );
}

/** A value whose whole purpose is to be pasted somewhere else, so it copies. */
function CopyLine({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="mt-2 flex flex-wrap items-center gap-3">
      <code className="mono flex-1 rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[12px] break-all">
        {value}
      </code>
      <Button
        variant="ghost"
        onClick={() => {
          // Best-effort: the clipboard API needs a secure context, and http on a
          // hostname other than localhost is not one. The value is on screen and
          // selectable either way, so a failure changes nothing but the label.
          navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            },
            () => undefined,
          );
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
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
