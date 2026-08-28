import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Handoff, legacyUrl } from "./handoff";

/**
 * The handoff page, which nobody is meant to see.
 *
 * Its whole job is to end: tell the window that opened it, close, and failing
 * that, forward somewhere real. The tests are about the failing-that, because
 * the happy path is two lines and the fallbacks are what people actually hit.
 *
 * The forwarding is asserted on the URL rather than on a navigation, because
 * jsdom's `window.location` is unforgeable and cannot be spied on. The same URL
 * is the href of the visible "Back to Studio" link, so the component renders it
 * too and both paths are covered by one value.
 */

let postMessage: ReturnType<typeof vi.fn>;
let close: ReturnType<typeof vi.fn>;

beforeEach(() => {
  postMessage = vi.fn();
  close = vi.fn();
  vi.stubGlobal("close", close);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const YOUTUBE_OK = {
  provider: "youtube" as const,
  ok: true,
  reason: "",
  source: "",
  returnTo: "setup" as const,
};

describe("with a window to report to", () => {
  beforeEach(() => vi.stubGlobal("opener", { closed: false, postMessage }));

  it("hands the outcome over and closes itself", () => {
    render(<Handoff {...YOUTUBE_OK} />);
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ status: "ok" }),
      window.location.origin,
    );
    expect(close).toHaveBeenCalled();
  });

  it("passes the reason through so the opener can explain it", () => {
    render(
      <Handoff provider="youtube" ok={false} reason="access_denied" source="" returnTo="setup" />,
    );
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ status: "error", reason: "access_denied" }),
      window.location.origin,
    );
  });

  it("says something when the browser refuses to close it", async () => {
    vi.useFakeTimers();
    render(<Handoff {...YOUTUBE_OK} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.getByRole("status")).toHaveTextContent("YouTube connected");
    vi.useRealTimers();
  });
});

describe("with no window to report to", () => {
  beforeEach(() => vi.stubGlobal("opener", null));

  it("does not close a window that is the only one the operator has", () => {
    // No opener means this is not a popup — the popup was blocked and consent
    // loaded in the app's own window. Closing it would leave nothing.
    render(<Handoff {...YOUTUBE_OK} />);
    expect(close).not.toHaveBeenCalled();
  });

  it("offers the way back that the redirect used to take automatically", () => {
    const { container } = render(<Handoff {...YOUTUBE_OK} />);
    expect(container.querySelector("a")).toHaveAttribute("href", "/setup?connected=1");
  });

  it("treats an opener that has already gone as no opener at all", () => {
    vi.stubGlobal("opener", { closed: true, postMessage });
    render(<Handoff {...YOUTUBE_OK} />);
    expect(postMessage).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });
});

describe("the URL the fallback path forwards to", () => {
  it("is the one the callback used to redirect to on success", () => {
    expect(legacyUrl(YOUTUBE_OK)).toBe("/setup?connected=1");
  });

  it("keeps the engine/provider distinction the Setup screen renders on", () => {
    // Setup shows an engine failure and one of Google's own error codes
    // completely differently, and must not have to guess which it has.
    expect(
      legacyUrl({
        provider: "youtube",
        ok: false,
        reason: "ConnectError",
        source: "engine",
        returnTo: "setup",
      }),
    ).toBe("/setup?connect_error=ConnectError&connect_error_source=engine");
  });

  it("leaves Google's own refusals unmarked", () => {
    expect(
      legacyUrl({
        provider: "youtube",
        ok: false,
        reason: "access_denied",
        source: "",
        returnTo: "setup",
      }),
    ).toBe("/setup?connect_error=access_denied");
  });

  it("returns a TikTok sign-in to the screen it started from", () => {
    expect(
      legacyUrl({ provider: "tiktok", ok: true, reason: "", source: "", returnTo: "repurpose" }),
    ).toBe("/repurpose?tiktok=connected");
  });

  it("escapes a failure reason rather than pasting it into a query string", () => {
    expect(
      legacyUrl({
        provider: "tiktok",
        ok: false,
        reason: "that sign-in link has expired — try again",
        source: "engine",
        returnTo: "setup",
      }),
    ).toBe(
      "/setup?tiktok_error=" + encodeURIComponent("that sign-in link has expired — try again"),
    );
  });
});
