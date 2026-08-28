"use client";

import { useEffect, useState } from "react";
import { reportConsent } from "@/lib/consent";

const LABEL = { youtube: "YouTube", tiktok: "TikTok" } as const;

/**
 * Hand the outcome over, then get out of the way.
 *
 * Three things happen here, in this order, and the order is the whole design:
 *
 * 1. Tell the opener. Instant, and it carries the reason — which is what lets
 *    the Setup screen render Google's `access_denied` with the paragraph that
 *    explains what it actually means, in the window the operator is looking at.
 * 2. Close. A window opened by script may close itself; this one was.
 * 3. If there was no opener to tell, this is not a popup — the popup was
 *    blocked and consent loaded in the app's own window, or the browser severed
 *    the link. Forward to the screen the callback used to redirect to, with the
 *    exact query string it used to carry. That path is unchanged, which is why
 *    none of the error rendering had to move.
 *
 * The card below is the fourth case: an opener that heard us, and a `close()`
 * the browser declined. Rare, and the only thing worse than a rare unstyled
 * page is a rare blank one.
 */
export function Handoff({
  provider,
  ok,
  reason,
  source,
  returnTo,
}: {
  provider: "youtube" | "tiktok";
  ok: boolean;
  reason: string;
  source: string;
  returnTo: "setup" | "repurpose";
}) {
  const [stranded, setStranded] = useState(false);

  useEffect(() => {
    const told = reportConsent({
      status: ok ? "ok" : "error",
      reason,
      source,
    });

    if (!told) {
      // Not a popup. Take the operator to the screen with the button on it,
      // speaking the query string that screen already reads.
      window.location.replace(legacyUrl({ provider, ok, reason, source, returnTo }));
      return;
    }

    window.close();
    // Still here a moment later means the browser refused to close us. Show
    // something rather than an empty window the person has to guess about.
    const timer = setTimeout(() => setStranded(true), 400);
    return () => clearTimeout(timer);
  }, [provider, ok, reason, source, returnTo]);

  return (
    <div
      // Over the rail, not beside it. This renders in a bare popup where the
      // app's navigation is not just unnecessary but misleading — there is
      // nowhere to navigate to.
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)] p-8"
    >
      <div
        role="status"
        className="max-w-[46ch] text-center"
        style={{ opacity: stranded ? 1 : 0, transition: "opacity 150ms ease-out" }}
      >
        <p
          className={`text-[15px] font-semibold ${
            ok ? "text-[var(--color-ok)]" : "text-[var(--color-warn)]"
          }`}
        >
          {ok ? `${LABEL[provider]} connected` : `${LABEL[provider]} did not connect`}
        </p>
        <p className="mt-2 text-[13px] leading-relaxed text-[var(--color-muted)]">
          {ok
            ? "You can close this window — Studio already knows."
            : reason || "The connection did not complete."}
        </p>
        <a
          href={legacyUrl({ provider, ok, reason, source, returnTo })}
          className="mt-4 inline-block text-[12px] text-[var(--color-muted)] underline decoration-[var(--color-line-hover)] underline-offset-4 hover:text-[var(--color-ink)]"
        >
          Back to Studio
        </a>
      </div>
    </div>
  );
}

/**
 * The URL this callback used to redirect to directly.
 *
 * Exported so it can be tested without a navigation: jsdom's `window.location`
 * is unforgeable, so the assertion has to be on the URL rather than on the
 * browser going there.
 *
 * Kept exactly as it was, deliberately. The Setup screen's handling of
 * `access_denied` and of an engine-side failure is several paragraphs of hard-won
 * wording, and the no-popup path is the one where a person most needs it — they
 * are already in a window with no obvious way back. Rewriting that contract to
 * suit the popup would have meant maintaining the explanation twice.
 */
export function legacyUrl({
  provider,
  ok,
  reason,
  source,
  returnTo,
}: {
  provider: "youtube" | "tiktok";
  ok: boolean;
  reason: string;
  source: string;
  returnTo: "setup" | "repurpose";
}): string {
  if (provider === "tiktok") {
    const query = ok ? "tiktok=connected" : `tiktok_error=${encodeURIComponent(reason)}`;
    return `/${returnTo}?${query}`;
  }
  if (ok) return "/setup?connected=1";
  const engine = source === "engine" ? "&connect_error_source=engine" : "";
  return `/setup?connect_error=${encodeURIComponent(reason)}${engine}`;
}
