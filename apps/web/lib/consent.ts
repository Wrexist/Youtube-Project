/**
 * Connecting an account, the way an integration is supposed to feel.
 *
 * The shape here is the one Stripe, Slack, Shopify and Google's own sign-in
 * button all converged on, and each part of it exists because of a specific way
 * the naive version breaks:
 *
 * **A popup, not the app's own window.** `window.location.href` navigates
 * whatever window the app happens to be in, and that is the wrong window more
 * often than it looks: Studio is commonly launched through a desktop shortcut,
 * which opens it as an app-mode window with no tabs and no address bar — so
 * consent loads in a frame the person cannot read a URL out of, cannot copy an
 * error from, and which may not carry the browser session they are actually
 * signed in with. A popup leaves the app on screen behind it, so the button
 * they pressed is still there when they come back.
 *
 * **Opened synchronously, before the server action is awaited.** A
 * `window.open` after an `await` is exactly the pattern popup blockers stop. So
 * the window is opened blank on the click and pointed at the provider when the
 * URL arrives.
 *
 * **Never with `noopener`.** This is worth stating because it was the bug:
 * `window.open` returns `null` whenever `noopener` is in the feature string —
 * that is what the flag means, and it is not a detail you notice, because the
 * window still opens. The handle was always null, so the blank window was
 * orphaned on screen forever and every consent URL fell through to the "popup
 * was blocked" path and navigated the app's own window. An abandoned
 * `about:blank` next to a consent page in the wrong window was the whole visible
 * symptom. We need the handle to point the window somewhere, to close it, and to
 * hear back from it, and the opened page is a provider's consent screen, not
 * untrusted content.
 *
 * **Three independent ways to learn the outcome**, because each one alone
 * fails on some real browser:
 *   - a `postMessage` from `/connected`, which is instant and carries the reason;
 *   - polling the engine, which is the only thing that survives
 *     Cross-Origin-Opener-Policy severing `window.opener` — increasingly the
 *     default, and the reason a message-only implementation mysteriously hangs;
 *   - watching for the window closing, which catches someone giving up.
 */

/** What the consent round trip decided. */
export type ConsentOutcome =
  | { status: "connected" }
  | { status: "failed"; reason: string; fromEngine: boolean }
  /** The window went away without an answer, or the operator cancelled. */
  | { status: "abandoned" }
  /** The popup was blocked; this window is navigating to the provider instead. */
  | { status: "redirected" };

/** A window opened synchronously on click, waiting for a URL. */
export interface ConsentSession {
  /** False when the browser blocked it; `send` then navigates in place. */
  readonly opened: boolean;
  /** Point the window at the provider. */
  send(url: string): void;
  /** Close it — the request failed and an empty window helps nobody. */
  abandon(): void;
  /**
   * Resolve once the round trip is over.
   *
   * `poll` is asked, about once a second, whether the account is connected yet.
   * It is what makes this work when the browser has severed `window.opener`, so
   * it is required rather than optional: a caller without one would appear to
   * work and hang on the browsers that matter.
   */
  settled(poll: () => Promise<boolean>): Promise<ConsentOutcome>;
}

/** Recognises our own handoff page and nobody else's. */
const MESSAGE = "studio:consent";

/** How often to ask the engine whether the account arrived. */
const POLL_MS = 1200;
/** How often to check whether the window is still there. */
const CLOSED_MS = 400;
/**
 * How long to keep polling after the window closes.
 *
 * The window closing and the engine's row appearing are not ordered: `/connected`
 * closes itself the moment it has handed the outcome over, which can be before
 * a poll started a moment earlier has come back. Calling it abandoned there
 * would report a failure for a connection that had in fact just succeeded.
 */
const GRACE_MS = 2500;

/**
 * Open a window now, to be pointed somewhere in a moment.
 *
 * Call this synchronously inside the click handler, before any `await`.
 */
export function openConsentWindow(name = "studio-consent"): ConsentSession {
  const target = window.open("about:blank", name, popupFeatures());
  let redirected = false;

  return {
    opened: target !== null,

    send(url: string) {
      if (target) {
        target.location.href = url;
        target.focus();
      } else {
        // Blocked. Navigating in place is worse than a popup, but far better
        // than a button that silently does nothing — which is exactly what a
        // swallowed popup looks like from the outside.
        redirected = true;
        window.location.href = url;
      }
    },

    abandon() {
      target?.close();
    },

    settled(poll: () => Promise<boolean>) {
      if (redirected || !target) return Promise.resolve({ status: "redirected" } as const);
      return watch(target, poll);
    },
  };
}

/**
 * Wait for whichever of the three signals arrives first.
 *
 * Written as one promise with one teardown rather than a race of three, because
 * a race leaves the losers running: an interval that outlives the connection it
 * was watching will happily report a *later* sign-in as this one's result.
 */
function watch(target: Window, poll: () => Promise<boolean>): Promise<ConsentOutcome> {
  return new Promise<ConsentOutcome>((resolve) => {
    let done = false;
    let closedAt: number | null = null;

    function finish(outcome: ConsentOutcome) {
      if (done) return;
      done = true;
      window.removeEventListener("message", onMessage);
      clearInterval(pollTimer);
      clearInterval(closedTimer);
      resolve(outcome);
    }

    function onMessage(event: MessageEvent) {
      // Origin first, shape second. Anything on the page can post a message to
      // this window, and a forged "connected" would have the screen claim an
      // account that is not there.
      if (event.origin !== window.location.origin) return;
      const data = event.data as Record<string, unknown> | null;
      if (!data || data.kind !== MESSAGE) return;

      if (data.status === "ok") {
        target.close();
        finish({ status: "connected" });
      } else {
        target.close();
        finish({
          status: "failed",
          reason: String(data.reason || "the connection did not complete"),
          fromEngine: data.source === "engine",
        });
      }
    }

    window.addEventListener("message", onMessage);

    // The one signal that does not depend on the popup being able to talk to
    // us. Everything else here is an optimisation on top of it.
    const pollTimer = setInterval(() => {
      poll().then(
        (connected) => {
          if (connected) {
            target.close();
            finish({ status: "connected" });
          }
        },
        // A failed poll is not an answer. The engine may be restarting, and
        // treating that as "not connected" is already what happens.
        () => undefined,
      );
    }, POLL_MS);

    const closedTimer = setInterval(() => {
      let closed = false;
      try {
        closed = target.closed;
      } catch {
        // COOP can make even reading `.closed` throw. Nothing to do but keep
        // polling; that path resolves this on its own.
        return;
      }
      if (!closed) {
        closedAt = null;
        return;
      }
      // Closed. Give the poll a moment to catch a success that closed the
      // window before its answer came back.
      closedAt ??= Date.now();
      if (Date.now() - closedAt >= GRACE_MS) finish({ status: "abandoned" });
    }, CLOSED_MS);
  });
}

/**
 * A popup that looks like a sign-in window rather than a stray browser.
 *
 * Sized for a consent page — every provider's is a single narrow column — and
 * centred on the window it was opened from, which on a multi-monitor setup is
 * not the same thing as centred on the screen. `screenLeft`/`screenTop` are the
 * outer window's position, so the arithmetic works on the monitor the app is
 * actually on.
 */
function popupFeatures(width = 520, height = 720): string {
  const availWidth = window.outerWidth || window.screen.width;
  const availHeight = window.outerHeight || window.screen.height;
  const left = Math.round((window.screenLeft ?? 0) + Math.max(0, (availWidth - width) / 2));
  const top = Math.round((window.screenTop ?? 0) + Math.max(0, (availHeight - height) / 3));

  return [
    "popup=yes",
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
    // The provider's page needs these; a consent screen with no scrollbar on a
    // short window is a form with an approve button below the fold.
    "scrollbars=yes",
    "resizable=yes",
  ].join(",");
}

/**
 * Post the outcome to whoever opened this window.
 *
 * Called by `/connected` and nowhere else. Returns false when there is no
 * opener to tell — a blocked popup, or a browser that severed the link — which
 * is the signal for that page to forward to a real screen instead.
 */
export function reportConsent(outcome: {
  status: "ok" | "error";
  reason?: string;
  source?: string;
}): boolean {
  let opener: Window | null = null;
  try {
    opener = window.opener;
    if (!opener || opener.closed) return false;
  } catch {
    // COOP again. There may be an opener; we are simply not allowed to see it.
    return false;
  }

  try {
    // Targeted at our own origin rather than "*": the message says whether an
    // account connected, and a wildcard would hand that to whatever page had
    // navigated the opener in the meantime.
    opener.postMessage({ kind: MESSAGE, ...outcome }, window.location.origin);
    return true;
  } catch {
    return false;
  }
}
