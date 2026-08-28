"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, Button } from "@/components/ui";
import {
  saveCredentials,
  connectYouTube,
  startTikTokConnection,
  tiktokStatus,
  youtubeConnected,
} from "@/app/actions";
import { openConsentWindow, type ConsentOutcome } from "@/lib/consent";
import type { SetupStatus, CredentialStatus, TikTokStatus } from "@studio/contracts";

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
  // The consent round trip, while it is happening and once it is done. Held here
  // rather than read back out of the URL because the popup means there is no
  // navigation to read from — the window with this component in it never left.
  const [waiting, setWaiting] = useState(false);
  const [outcome, setOutcome] = useState<ConsentOutcome | null>(null);
  const router = useRouter();

  const params = useSearchParams();
  // Google sends the operator back here after consent. Without something saying
  // so, a successful connection looked identical to a cancelled one.
  const justConnected = params.get("connected") === "1";
  // Google's refusal, handed here by the engine's callback rather than rendered as
  // an API error on a blank page. See `ConnectError` for why `access_denied` gets
  // a paragraph of its own.
  const connectError = params.get("connect_error");
  // TikTok's callback lands here too, with its own outcome. Separate parameters
  // from Google's: two connections that report through one pair of names would
  // show a TikTok failure as a YouTube one.
  const tiktokConnected = params.get("tiktok") === "connected";
  const tiktokError = params.get("tiktok_error");
  // Which side produced it. Google's callback carries no such parameter, so its
  // absence means Google; the engine sets it explicitly when its own half fails.
  const connectErrorFromEngine = params.get("connect_error_source") === "engine";

  const groups = useMemo(() => {
    const out: Record<string, CredentialStatus[]> = {};
    for (const c of setup.credentials) (out[c.group] ??= []).push(c);
    return out;
  }, [setup.credentials]);

  const dirty = Object.keys(drafts).length > 0;

  // Two ways the same outcome can arrive, and the screen must not care which.
  // The popup reports through `outcome`; the no-popup fallback reports through
  // the query string, exactly as it did before there was a popup at all.
  const connectedNow = justConnected || outcome?.status === "connected";
  const failure =
    outcome?.status === "failed"
      ? { code: outcome.reason, fromEngine: outcome.fromEngine }
      : connectError
        ? { code: connectError, fromEngine: connectErrorFromEngine }
        : null;

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

  /**
   * Connect a YouTube channel without leaving this screen.
   *
   * Consent happens in a popup and the answer comes back to this component, so
   * nothing here navigates: the card above updates in place, the way connecting
   * an account does in every integration people have used before this one.
   *
   * Not wrapped in `startTransition`. The await below spans however long someone
   * takes to read Google's consent screen, and a transition held open that long
   * would keep Save disabled for the duration.
   */
  async function connect() {
    setError(null);
    setOutcome(null);
    // Opened before the await, or the browser treats it as an unrequested popup.
    const session = openConsentWindow("studio-youtube");
    setWaiting(true);

    const result = await connectYouTube();
    if (!result.ok || !result.data) {
      session.abandon();
      setWaiting(false);
      setError(result.error ?? "Could not start the YouTube connection.");
      return;
    }
    // A window of its own, not this one. Google's consent page has to be loaded
    // by the person's own browser, signed in as themselves — and Studio is often
    // launched as an app-mode window with no address bar, which is both the
    // wrong session and a page they cannot read an error out of.
    session.send(result.data.url);

    const settled = await session.settled(youtubeConnected);
    setWaiting(false);
    // `redirected` means this window is on its way to Google. Saying anything
    // would be a flash of text on a page that is already leaving.
    if (settled.status === "redirected") return;
    setOutcome(settled);
    // The channel list and `can_publish` come from the server component above
    // this one, so a successful connection has to ask for them again. `refresh`
    // re-renders the tree with fresh data and keeps this component's state,
    // which is the entire reason the outcome lives in state rather than a URL.
    if (settled.status === "connected") router.refresh();
  }

  return (
    <>
      <Status setup={setup} justConnected={connectedNow} />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 bg-[var(--color-surface)] p-4"
        >
          <p className="text-[13px] text-[var(--color-bad)]">{error}</p>
        </div>
      )}

      {failure && <ConnectError code={failure.code} fromEngine={failure.fromEngine} />}

      {/* Closed the window without finishing. Not an error — nothing happened —
          but silence after pressing Connect reads as the button being broken. */}
      {outcome?.status === "abandoned" && (
        <p role="status" className="mb-6 text-[13px] text-[var(--color-muted)]">
          The sign-in window closed before the connection finished. Nothing was
          changed; press Connect again whenever you like.
        </p>
      )}

      {tiktokConnected && (
        <p
          role="status"
          className="mb-6 text-[13px] text-[var(--color-ok)]"
        >
          TikTok connected. The Repurpose tab can sweep your own posts now.
        </p>
      )}
      {tiktokError && (
        <div
          role="alert"
          className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 bg-[var(--color-surface)] p-4"
        >
          <p className="text-[13px] text-[var(--color-bad)]">{tiktokError}</p>
        </div>
      )}

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
                waiting={waiting}
                onConnect={connect}
              />
            </>
          )}

          {group === "Repurpose" && <TikTokConnection />}
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
  Repurpose:
    "Only needed to sweep your own TikToks. The tab works without them for clips you add by hand.",
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
function ConnectError({ code, fromEngine }: { code: string; fromEngine: boolean }) {
  const denied = code === "access_denied";

  // Two different things arrive in this one parameter and they read completely
  // differently. Google's refusals are short machine tokens — `access_denied`,
  // `redirect_uri_mismatch`. The engine's own failures are usually sentences,
  // sent here rather than rendered as a bare "Internal Server Error" at an API
  // address the operator has no way back from.
  //
  // Told apart by an explicit flag, not by looking at the text. This tested for
  // a space, which is wrong precisely where it matters: the engine falls back to
  // the exception's class name when the message is empty, so a certificate
  // failure arrives as the single word `ConnectError` and was rendered under
  // "that is Google's own error code".
  if (fromEngine) {
    return (
      <div
        role="alert"
        className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 bg-[var(--color-surface)] p-5"
      >
        <p className="text-[14px] font-semibold text-[var(--color-bad)]">
          The connection could not be completed
        </p>
        <p className="mt-2 max-w-[70ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
          You approved access, but Studio could not finish the exchange with Google.
          Nothing was changed here, and pressing Connect YouTube again is safe once the
          cause below is dealt with.
        </p>
        <p className="mt-3 max-w-[70ch] text-[13px] leading-relaxed text-[var(--color-ink)]">
          {code}
        </p>
      </div>
    );
  }

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

/** The one page every item on the TikTok checklist is fixed on. */
function PortalLink() {
  return (
    <a
      href="https://developers.tiktok.com/"
      target="_blank"
      rel="noreferrer noopener"
      className="whitespace-nowrap text-[var(--color-ink)] underline decoration-[var(--color-line-hover)] underline-offset-4"
    >
      developers.tiktok.com
    </a>
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
  waiting,
  onConnect,
}: {
  setup: SetupStatus;
  pending: boolean;
  waiting: boolean;
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
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant={setup.can_publish ? "ghost" : "primary"}
          onClick={onConnect}
          disabled={!setup.can_connect || pending || waiting}
        >
          {waiting
            ? "Waiting for Google…"
            : setup.can_publish
              ? "Reconnect"
              : "Connect YouTube"}
        </Button>
        {/* Said out loud because the popup can end up behind this window, and a
            disabled button with nothing next to it looks like a hang. */}
        {waiting && (
          <p aria-live="polite" className="text-[12px] text-[var(--color-muted)]">
            Approve it in the Google window. Closing that window cancels.
          </p>
        )}
      </div>
    </Card>
  );
}


/**
 * The TikTok connection, for Lane A of the Repurpose tab.
 *
 * Reads its own status rather than taking it from `setup`: `/v1/setup` describes
 * credentials, and whether somebody has *signed in* is a different question with
 * a different answer — an install can have both keys and no account.
 *
 * The three states are kept distinct on purpose. "Not configured", "configured
 * but nobody signed in" and "connected" have three different next actions, and
 * collapsing them is how "the sweep shows nothing" becomes unanswerable.
 */
function TikTokConnection() {
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    tiktokStatus().then((result) => {
      if (!cancelled && result.ok) setStatus(result.data ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const connected = Boolean(status?.account?.connected);
  const configured = Boolean(status?.configured);

  async function connect() {
    const session = openConsentWindow("studio-tiktok");
    setBusy(true);
    setError(null);

    const result = await startTikTokConnection();
    if (!result.ok || !result.data?.url) {
      session.abandon();
      setBusy(false);
      // The engine refuses before the trip when it can already tell the
      // credentials are wrong, and that message is more use than TikTok's.
      setError(result.error ?? "Could not start the TikTok connection.");
      return;
    }
    // A window of its own, for the same reason the YouTube flow uses one.
    session.send(result.data.url);
    setBusy(false);
    setWaiting(true);

    const settled = await session.settled(tiktokConnectedNow);
    setWaiting(false);
    if (settled.status === "redirected") return;
    if (settled.status === "failed") {
      setError(settled.reason);
      return;
    }
    if (settled.status === "abandoned") {
      setError("The TikTok window closed before it finished. Nothing was changed.");
      return;
    }
    // Connected. Read the account back rather than assuming, so the card names
    // the handle that actually got stored.
    const fresh = await tiktokStatus();
    if (fresh.ok) setStatus(fresh.data ?? null);
  }

  return (
    <Card className="mt-2.5 p-4">
      <p className="text-[14px] font-semibold">TikTok account</p>
      <p className="mt-1.5 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-muted)]">
        {connected
          ? `Connected as ${status?.account?.handle || "your account"}. Studio can list your own posts and nothing else — TikTok's API does not offer other creators' videos.`
          : configured
            ? "Opens TikTok's consent page in its own window, so this screen stays where it is. Studio asks to read your own posts, and stores only a refresh token, encrypted."
            : "Save the client key and secret above first — the consent page cannot be built without them."}
      </p>

      {/* What the engine can tell about these credentials without asking TikTok.
          TikTok's own answer to a bad key is the word `client_key` on an
          otherwise blank page, by which point the browser has left the app — so
          anything knowable is worth saying here, before the trip. */}
      {status?.problem && (
        <p className="mt-2 max-w-[70ch] text-[12px] leading-relaxed text-[var(--color-warn)]">
          {status.problem}
        </p>
      )}
      {configured && !connected && status?.client_key_hint && (
        <details className="mt-3" open>
          <summary className="cursor-pointer list-none text-[12px] font-semibold text-[var(--color-muted)] hover:text-[var(--color-ink)]">
            TikTok says &ldquo;client_key&rdquo;? What it checks
            <span className="ml-2 font-normal text-[var(--color-faint)]">show</span>
          </summary>

          {/* TikTok answers all of these with the same word and no detail, so the
              only way through is to check them in order. Ordered by how often
              each is actually the cause. */}
          <ol className="mt-3 grid gap-3 text-[12px] leading-relaxed text-[var(--color-muted)]">
            <li>
              <span className="font-semibold">Is the account you signed in with a Test User?</span>{" "}
              While the app is unaudited it is in development mode, and only the
              accounts listed under Test Users can authorise it. This is the most
              common cause of this exact error by a distance. Add the account on{" "}
              <PortalLink /> under Manage apps → your app → Test Users, then sign
              in again <em>as that account</em>.
            </li>
            <li>
              <span className="font-semibold">Is the app registered as Desktop rather than Web?</span>{" "}
              TikTok will not accept an <span className="mono">http://</span>{" "}
              redirect URI for a <em>web</em> app — those must be{" "}
              <span className="mono">https</span>. Studio runs on your own
              machine, so its callback is <span className="mono">http://localhost</span>,
              which is only legal under the <em>desktop</em> platform. An app set
              up as Web can never accept the URI below.
            </li>
            <li>
              <span className="font-semibold">Is Login Kit added as a product?</span> The
              key exists as soon as the app does, but authorising against it fails
              until Login Kit is one of the app&apos;s products.
            </li>
            <li>
              <span className="font-semibold">Is the redirect URI registered, character for character?</span>{" "}
              A trailing slash counts as a difference.
              <CopyLine value="http://localhost:8080/v1/repurpose/auth/tiktok/callback" />
            </li>
            <li>
              <span className="font-semibold">
                Is the key from a Sandbox, when the app is in production — or the
                other way round?
              </span>{" "}
              A sandbox is a separate configuration with its own client key. Test
              Users added to a sandbox do not apply to the production app, and a
              production key will not authorise against a sandbox. Whichever one
              your test account is registered on, use <em>that</em> key.
            </li>
            <li>
              <span className="font-semibold">Is that the client key, not the app ID?</span>{" "}
              They sit next to each other on the credentials page and are easy to
              transpose. This is the key Studio is sending — it is a public
              identifier that travels in the URL, so compare it against the app&apos;s
              own page:
              <CopyLine value={status.client_key_hint} />
              {/* The count is here so a truncated paste is visible. Deliberately
                  not a rule that refuses a key: TikTok's are around this length
                  today, but that is an observation about today's keys, and
                  refusing a valid future one would be worse than the error page
                  it replaces. */}
              <span className="mt-1 block text-[11px] text-[var(--color-faint)]">
                {status.client_key_hint.length} characters — if that is shorter than
                the value on TikTok&apos;s page, the paste was truncated.
              </span>
            </li>
          </ol>
        </details>
      )}
      {error && <p className="mt-2 text-[12px] text-[var(--color-bad)]">{error}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant={connected ? "ghost" : "primary"}
          onClick={connect}
          disabled={!configured || busy || waiting}
        >
          {waiting ? "Waiting for TikTok…" : connected ? "Reconnect" : "Connect TikTok"}
        </Button>
        {waiting && (
          <p aria-live="polite" className="text-[12px] text-[var(--color-muted)]">
            Approve it in the TikTok window. Closing that window cancels.
          </p>
        )}
      </div>
    </Card>
  );
}

/**
 * Whether TikTok has landed yet, for the poll that runs while consent is open.
 *
 * Separate from the status fetch above because it answers one question and
 * throws nothing: a poll that rejects mid-flow would otherwise have to be
 * wrapped in a try at every call site.
 */
async function tiktokConnectedNow(): Promise<boolean> {
  const result = await tiktokStatus();
  return Boolean(result.ok && result.data?.account?.connected);
}
