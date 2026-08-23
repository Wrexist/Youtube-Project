/**
 * The Genre screen's mutation paths.
 *
 * Three behaviours pinned: a failed write surfaces its message instead of
 * vanishing, pausing is an in-place flip rather than a row disappearing, and
 * removing asks first — the watchlist is curated state and a slip of a cursor
 * must not silently unmake it.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GenreChannel, GenreWatchlist } from "@studio/contracts";

import { GenreView } from "./genre-view";

const engine = vi.hoisted(() => ({
  syncGenre: vi.fn(async () => ({
    channels_synced: 2,
    failures: 0,
    videos_new: 5,
    reports: [],
  })),
  getGenrePatterns: vi.fn(async () => null),
  toggleChannel: vi.fn(async () => ({ youtube_channel_id: "UC1", active: false })),
  unwatchChannel: vi.fn(async () => ({ removed: true })),
  watchChannel: vi.fn(async () => ({
    channel: {
      youtube_channel_id: "UC-new",
      label: "New Channel",
      note: "",
      active: true,
      last_synced_at: null,
      last_error: "",
      video_count: 0,
      created_at: "2026-08-23T00:00:00+00:00",
    },
  })),
}));

vi.mock("@/lib/engine", () => engine);

const CHANNEL: GenreChannel = {
  youtube_channel_id: "UC1",
  label: "The Bridge Files",
  note: "",
  active: true,
  last_synced_at: "2026-08-22T06:12:00+00:00",
  last_error: "",
  video_count: 58,
  created_at: "2026-08-01T09:00:00+00:00",
};

const WATCHLIST: GenreWatchlist = { channels: [CHANNEL] };

const PATTERNS = {
  video_count: 126,
  hook_patterns: [
    { pattern: "curiosity", count: 44, share: 0.349, median_views: 214_000, median_views_per_day: 12_400 },
  ],
  median_duration_s: 512,
  duration_buckets: { under_60s: 38, "60s_to_8m": 61, over_8m: 27 },
  uploads_per_week: 2.4,
  top_by_velocity: [],
};

function view(overrides: Partial<GenreWatchlist> = {}) {
  return render(
    <GenreView
      initialWatchlist={{ channels: [CHANNEL], ...overrides }}
      initialPatterns={PATTERNS}
      demo={false}
    />,
  );
}

describe("GenreView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the narrative from real corpus numbers", () => {
    view();
    expect(screen.getByText("What this niche rewards")).toBeTruthy();
    expect(screen.getByText(/35%/)).toBeTruthy();
    expect(screen.getByText(/12,400/)).toBeTruthy();
  });

  it("pauses in place — the row stays visible with its paused chip", async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(engine.toggleChannel).toHaveBeenCalledWith("UC1", false),
    );
    expect(await screen.findByText("paused")).toBeTruthy();
  });

  it("surfaces a failed write instead of swallowing it", async () => {
    engine.syncGenre.mockRejectedValueOnce(
      new Error("Could not reach the engine at localhost:8080. Is it running?"),
    );
    view();
    fireEvent.click(screen.getByRole("button", { name: "Sync" }));
    expect(await screen.findByText(/Could not reach the engine/)).toBeTruthy();
  });

  it("asks before removing a channel", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    view();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(confirm).toHaveBeenCalledOnce();
    // Declined: nothing was sent, the row is still here.
    expect(engine.unwatchChannel).not.toHaveBeenCalled();
    expect(screen.getByText("The Bridge Files")).toBeTruthy();
    confirm.mockRestore();
  });

  it("shows the empty state with its own add form when nobody is watched", () => {
    render(
      <GenreView
        initialWatchlist={{ channels: [] }}
        initialPatterns={{
          video_count: 0,
          hook_patterns: [],
          duration_buckets: {},
          top_by_velocity: [],
        }}
        demo={false}
      />,
    );
    expect(screen.getByText("Nobody watched yet")).toBeTruthy();
    expect(screen.getByPlaceholderText("@handle or channel id")).toBeTruthy();
  });
});
