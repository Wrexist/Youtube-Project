/**
 * The command palette.
 *
 * Radix renders `Dialog.Content` through a portal into `document.body`, not into
 * whatever container `render()` mounts to — `screen` queries the whole document,
 * so that needs no special handling here, only awareness of why `container`
 * would come back empty if anyone reached for it instead.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => pathname,
}));

const getJobs = vi.fn();
vi.mock("@/lib/engine", () => ({ getJobs: (...args: unknown[]) => getJobs(...args) }));

import { CommandPalette } from "./command-palette";

async function openPalette() {
  // `window` is not a node `render()` mounted, so React Testing Library's
  // automatic `act()` wrapping around `fireEvent` does not reach a listener
  // bound there the way it does for a rendered element — wrapped explicitly.
  // Async, and awaited: opening starts the `getJobs()` fetch in an effect, and
  // that promise (mocked to resolve immediately) settles on a microtask this
  // synchronous `act()` would not wait for, landing its `setVideos` outside any
  // `act()` at all.
  await act(async () => {
    fireEvent.keyDown(window, { key: "k", metaKey: true });
  });
}

beforeEach(() => {
  pathname = "/";
  push.mockReset();
  getJobs.mockReset();
  getJobs.mockResolvedValue(null); // engine unreachable by default — exercises the demo fallback
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

describe("opening and closing", () => {
  it("is closed until ⌘K or Ctrl+K is pressed", async () => {
    render(<CommandPalette />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await openPalette();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("toggles closed on a second ⌘K", async () => {
    render(<CommandPalette />);
    await openPalette();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await openPalette();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not mount at all on the welcome screen", async () => {
    pathname = "/welcome";
    render(<CommandPalette />);
    await openPalette();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clears the query on close, so it reopens fresh rather than mid-search", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "calendar" } });
    expect(screen.getByRole("combobox")).toHaveValue("calendar");

    await openPalette(); // close
    await openPalette(); // reopen
    expect(screen.getByRole("combobox")).toHaveValue("");
  });
});

describe("screens", () => {
  it("lists every rail destination plus Setup", async () => {
    render(<CommandPalette />);
    await openPalette();
    // Anchored to the start: the accessible name is "<label> <hint>", and every
    // video row's hint is literally "Open in Create" — an unanchored /Create/
    // matches those rows too now that the jobs fetch has actually resolved by
    // the time this assertion runs.
    for (const label of ["Create", "Queue", "Library", "Calendar", "Analytics", "Setup"]) {
      expect(screen.getByRole("option", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    }
  });

  it("navigates on Enter", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "calendar" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });

    expect(push).toHaveBeenCalledWith("/calendar");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); // Enter also closes it
  });

  it("navigates on click without needing the keyboard at all", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.click(screen.getByRole("option", { name: /Analytics/ }));
    expect(push).toHaveBeenCalledWith("/analytics");
  });

  it("filters out everything that does not match", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zzz-nothing-matches" } });
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
  });
});

describe("actions", () => {
  it("offers New video, which goes to Create", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.click(screen.getByRole("option", { name: /New video/ }));
    expect(push).toHaveBeenCalledWith("/");
  });

  it("toggles the theme without navigating anywhere", async () => {
    render(<CommandPalette />);
    await openPalette();
    fireEvent.click(screen.getByRole("option", { name: /Toggle theme/ }));

    expect(push).not.toHaveBeenCalled();
    expect(["light", "dark"]).toContain(document.documentElement.dataset.theme);
  });

  it("has no command that claims to run a series — Series has no backing endpoint yet", async () => {
    render(<CommandPalette />);
    await openPalette();
    expect(screen.queryByRole("option", { name: /run.*series/i })).not.toBeInTheDocument();
  });
});

describe("videos", () => {
  it("lists live jobs by topic when the engine answers", async () => {
    getJobs.mockResolvedValue([
      { id: "job-1", topic: "Why bridges collapse", status: "completed", cost_usd: 0, stages_done: 1, stages_total: 1, workflow: "video" },
    ]);
    render(<CommandPalette />);
    await openPalette();

    expect(await screen.findByRole("option", { name: /Why bridges collapse/ })).toBeInTheDocument();
  });

  it("falls back to the demo library when the engine is unreachable", async () => {
    getJobs.mockResolvedValue(null);
    render(<CommandPalette />);
    await openPalette();

    // From lib/demo.ts LIBRARY — the same fixture every other screen falls back to.
    expect(await screen.findByRole("option", { name: /Why bridges collapse/ })).toBeInTheDocument();
  });

  it("does not fetch jobs until the palette is actually opened", async () => {
    render(<CommandPalette />);
    expect(getJobs).not.toHaveBeenCalled();
    await openPalette();
    expect(getJobs).toHaveBeenCalledTimes(1);
  });

  it("jumps to the job in Create on selection", async () => {
    getJobs.mockResolvedValue([
      { id: "job-9", topic: "How salt built cities", status: "completed", cost_usd: 0, stages_done: 1, stages_total: 1, workflow: "video" },
    ]);
    render(<CommandPalette />);
    await openPalette();

    fireEvent.click(await screen.findByRole("option", { name: /How salt built cities/ }));
    expect(push).toHaveBeenCalledWith("/?job=job-9");
  });
});

describe("keyboard navigation", () => {
  it("moves the highlight with the arrow keys and selects with Enter", async () => {
    render(<CommandPalette />);
    await openPalette();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "cal" } }); // narrows to Calendar alone

    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
    fireEvent.keyDown(input, { key: "ArrowDown" }); // stays clamped at the only row
    fireEvent.keyDown(input, { key: "Enter" });

    expect(push).toHaveBeenCalledWith("/calendar");
  });

  it("does not throw when a narrowing query leaves the old highlight out of range", async () => {
    render(<CommandPalette />);
    await openPalette();
    const input = screen.getByRole("combobox");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" }); // highlight now several rows in

    expect(() =>
      fireEvent.change(input, { target: { value: "toggle theme" } }),
    ).not.toThrow();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(["light", "dark"]).toContain(document.documentElement.dataset.theme);
  });
});
