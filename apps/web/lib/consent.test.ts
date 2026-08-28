import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { openConsentWindow, reportConsent, type ConsentOutcome } from "./consent";

/**
 * The connect flow, which is almost entirely edge cases.
 *
 * Every assertion here stands for a browser that behaves differently from the
 * others: one that blocks the popup, one that severs `window.opener` under
 * Cross-Origin-Opener-Policy, one that refuses `close()`. The happy path is one
 * test; the rest is why this is a module.
 */

/** A stand-in for the window `window.open` hands back. */
function fakeWindow() {
  return {
    location: { href: "" },
    closed: false,
    focus: vi.fn(),
    close: vi.fn(function (this: { closed: boolean }) {
      this.closed = true;
    }),
  };
}

let opened: ReturnType<typeof fakeWindow> | null;
let features: string;

beforeEach(() => {
  vi.useFakeTimers();
  opened = fakeWindow();
  features = "";
  vi.stubGlobal(
    "open",
    vi.fn((_url: string, _name: string, f: string) => {
      features = f;
      return opened;
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Deliver a message as the browser would, from our own origin by default. */
function post(data: unknown, origin = window.location.origin) {
  window.dispatchEvent(new MessageEvent("message", { data, origin }));
}

/** Run the timers until a pending outcome settles, or give up. */
async function settle(promise: Promise<ConsentOutcome>, ms = 10_000) {
  let done: ConsentOutcome | null = null;
  promise.then((o) => (done = o));
  const step = 100;
  for (let elapsed = 0; elapsed < ms && done === null; elapsed += step) {
    await vi.advanceTimersByTimeAsync(step);
  }
  return done as ConsentOutcome | null;
}

describe("opening the window", () => {
  it("never asks for noopener, because that returns a null handle", () => {
    // The regression this file exists for. `window.open` returns null whenever
    // `noopener` is in the feature string — that is what the flag means — so the
    // handle was always null: the blank window was orphaned on screen and every
    // consent URL fell through to the popup-blocked path, navigating the app's
    // own window. Both symptoms at once, from one word.
    openConsentWindow();
    expect(features).not.toContain("noopener");
    expect(features).toContain("popup=yes");
  });

  it("points the opened window at the provider rather than navigating this one", () => {
    const before = window.location.href;
    openConsentWindow().send("https://example.test/consent");
    expect(opened!.location.href).toBe("https://example.test/consent");
    expect(window.location.href).toBe(before);
  });

  it("navigates in place when the popup was blocked", () => {
    opened = null;
    const session = openConsentWindow();
    expect(session.opened).toBe(false);
    // jsdom refuses a real navigation; the assignment is what is under test.
    const assigned = vi.spyOn(window, "location", "get");
    assigned.mockReturnValue({ ...window.location, href: "" } as Location);
    expect(() => session.send("https://example.test/consent")).not.toThrow();
    assigned.mockRestore();
  });

  it("reports a blocked popup as redirected rather than waiting for it", async () => {
    opened = null;
    const session = openConsentWindow();
    try {
      session.send("https://example.test/consent");
    } catch {
      // jsdom's "not implemented: navigation" — irrelevant to the assertion.
    }
    await expect(session.settled(async () => false)).resolves.toEqual({
      status: "redirected",
    });
  });
});

describe("hearing back", () => {
  it("resolves as connected on a message from the handoff page", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    post({ kind: "studio:consent", status: "ok" });
    expect(await outcome).toEqual({ status: "connected" });
    // And tidies up after itself: an empty window left behind is the thing
    // people screenshot and ask about.
    expect(opened!.close).toHaveBeenCalled();
  });

  it("carries the reason and whose fault it was on a failure", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    post({
      kind: "studio:consent",
      status: "error",
      reason: "access_denied",
      source: "",
    });
    expect(await outcome).toEqual({
      status: "failed",
      reason: "access_denied",
      fromEngine: false,
    });
  });

  it("marks an engine-side failure as ours", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    post({ kind: "studio:consent", status: "error", reason: "ConnectError", source: "engine" });
    expect(await outcome).toMatchObject({ fromEngine: true });
  });

  it("ignores a message from another origin", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    // Anything on the page can post to this window. A forged success would have
    // the screen claim an account that is not connected.
    post({ kind: "studio:consent", status: "ok" }, "https://evil.test");
    await vi.advanceTimersByTimeAsync(500);
    expect(opened!.close).not.toHaveBeenCalled();
    // Still waiting, so the only way it can end is the operator giving up.
    opened!.closed = true;
    expect(await outcome).toEqual({ status: "abandoned" });
  });

  it("ignores a message that is not ours", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    // Dev-server traffic posts to this window constantly.
    post({ type: "webpack-hmr" });
    await vi.advanceTimersByTimeAsync(500);
    expect(opened!.close).not.toHaveBeenCalled();
    opened!.closed = true;
    expect(await outcome).toEqual({ status: "abandoned" });
  });
});

describe("when the popup cannot talk back", () => {
  it("still resolves, from the poll alone", async () => {
    // Cross-Origin-Opener-Policy severs `window.opener`, so no message ever
    // arrives. A flow built only on postMessage hangs here forever, on a
    // connection that in fact succeeded.
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    let connected = false;
    const outcome = settle(session.settled(async () => connected));
    await vi.advanceTimersByTimeAsync(1000);
    connected = true;
    expect(await outcome).toEqual({ status: "connected" });
  });

  it("survives a browser that throws on reading `closed`", async () => {
    // Under COOP even asking whether the window is still open throws. The watch
    // has to shrug that off rather than take it down with the flow.
    opened = {
      location: { href: "" },
      focus: vi.fn(),
      close: vi.fn(),
      get closed(): boolean {
        throw new DOMException("blocked by Cross-Origin-Opener-Policy");
      },
    } as unknown as ReturnType<typeof fakeWindow>;
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    let connected = false;
    const outcome = settle(session.settled(async () => connected));
    await vi.advanceTimersByTimeAsync(1000);
    connected = true;
    expect(await outcome).toEqual({ status: "connected" });
  });

  it("keeps polling when a poll rejects", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    let calls = 0;
    const outcome = settle(
      session.settled(async () => {
        calls += 1;
        if (calls < 3) throw new Error("engine restarting");
        return true;
      }),
    );
    expect(await outcome).toEqual({ status: "connected" });
  });
});

describe("giving up", () => {
  it("reports abandonment when the window is closed and nothing connected", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const outcome = settle(session.settled(async () => false));
    opened!.closed = true;
    expect(await outcome).toEqual({ status: "abandoned" });
  });

  it("does not call a success abandoned because the window closed first", async () => {
    // The handoff page closes itself the moment it has posted the outcome, which
    // can be before a poll issued a moment earlier comes back. Without the grace
    // window this reports a failure for a connection that worked.
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    let connected = false;
    const outcome = settle(session.settled(async () => connected));
    opened!.closed = true;
    await vi.advanceTimersByTimeAsync(300);
    connected = true;
    expect(await outcome).toEqual({ status: "connected" });
  });

  it("stops listening once it has an answer", async () => {
    const session = openConsentWindow();
    session.send("https://example.test/consent");
    const poll = vi.fn(async () => false);
    const outcome = settle(session.settled(poll));
    post({ kind: "studio:consent", status: "ok" });
    expect(await outcome).toEqual({ status: "connected" });

    // An interval that outlives the connection it was watching would report a
    // later sign-in as this one's result.
    const after = poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll.mock.calls.length).toBe(after);
  });
});

describe("reporting from the handoff page", () => {
  it("says so when there is no opener to tell", () => {
    vi.stubGlobal("opener", null);
    expect(reportConsent({ status: "ok" })).toBe(false);
  });

  it("says so when the opener has gone", () => {
    vi.stubGlobal("opener", { closed: true, postMessage: vi.fn() });
    expect(reportConsent({ status: "ok" })).toBe(false);
  });

  it("posts to our own origin, never a wildcard", () => {
    const postMessage = vi.fn();
    vi.stubGlobal("opener", { closed: false, postMessage });
    expect(reportConsent({ status: "error", reason: "nope", source: "engine" })).toBe(true);
    expect(postMessage).toHaveBeenCalledWith(
      { kind: "studio:consent", status: "error", reason: "nope", source: "engine" },
      window.location.origin,
    );
  });
});
