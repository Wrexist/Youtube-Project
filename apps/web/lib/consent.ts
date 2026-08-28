/**
 * Sending the browser to a provider's consent page.
 *
 * `window.location.href` navigates whatever window the app happens to be in,
 * and that is the wrong window more often than it looks. Studio is commonly
 * launched through a desktop shortcut, which opens it as an app-mode window
 * with no tabs and no address bar — so the consent page loads inside a frame
 * the person cannot inspect, cannot copy a URL out of, and, when it fails,
 * cannot read an error from except as an unlabelled screenshot. Worse, an
 * app-mode window may not carry the session of the browser the operator is
 * actually signed into TikTok with, so they are asked to log in again inside a
 * window that looks nothing like a browser.
 *
 * Opening a real tab fixes all of that: it is the profile they are signed into,
 * it has an address bar, and the app keeps its own window so the screen they
 * started from is still there when they come back.
 *
 * The popup-blocker constraint is why this is a module rather than a line: a
 * `window.open` only survives if it happens in the same tick as the click that
 * caused it. Awaiting the server action first and opening afterwards is exactly
 * the pattern browsers block. So the window is opened *immediately*, and the URL
 * is assigned into it once the action returns.
 */

/** A window opened synchronously on click, waiting for a URL. */
export interface ConsentWindow {
  /** Point the opened tab at the provider. */
  send(url: string): void;
  /** Close it — the request failed and an empty tab helps nobody. */
  abandon(): void;
  /** False when the browser blocked it; the caller should fall back. */
  readonly opened: boolean;
}

/**
 * Open a tab now, to be pointed somewhere in a moment.
 *
 * Call this synchronously inside the click handler, before any `await`.
 */
export function openConsentWindow(): ConsentWindow {
  const target = window.open("about:blank", "_blank", "noopener,noreferrer");

  return {
    opened: target !== null,
    send(url: string) {
      if (target) {
        target.location.href = url;
        target.focus();
      } else {
        // Blocked. Navigating the current window is worse than a new tab but
        // far better than a button that silently does nothing, which is what a
        // swallowed popup looks like from the outside.
        window.location.href = url;
      }
    },
    abandon() {
      target?.close();
    },
  };
}
