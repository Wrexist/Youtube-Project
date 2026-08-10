/**
 * The Style screen's save path.
 *
 * Both tests here are about a control that ends up lying. A screen whose every
 * row saves on change has no Save button to retry with, so a failed write has to
 * leave the interface honest by itself — re-enabled, showing the value actually in
 * force, and saying what went wrong.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Style } from "@studio/contracts";

import { StyleView } from "./style-view";

const updateStyle = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({ updateStyle }));

const STYLE: Style = {
  voice: "en-US-AvaNeural",
  subtitle_font: "",
  ken_burns: "alternate",
  transition_fade_s: 0,
  bgm_enabled: true,
  bgm_volume: 0.12,
  bgm_track: "",
  options: {
    voices_live: true,
    voices: [
      {
        id: "en-US-AvaNeural",
        name: "AvaNeural",
        locale: "en-US",
        gender: "Female",
        traits: ["Friendly"],
      },
      {
        id: "en-US-AndrewNeural",
        name: "AndrewNeural",
        locale: "en-US",
        gender: "Male",
        traits: ["Warm"],
      },
    ],
    fonts: [],
    tracks: ["bed.mp3"],
    tracks_dir: "storage/bgm",
  },
};

beforeEach(() => {
  updateStyle.mockReset();
});

describe("a save that does not land", () => {
  it("re-enables the controls when the Server Action itself rejects", async () => {
    // `updateStyle` catches *engine* errors inside the action and returns
    // `{ok: false}`. The call to the action can still reject on its own — a
    // dropped connection between browser and server — and that path used to skip
    // the reset, leaving every control disabled until reload.
    updateStyle.mockRejectedValue(new Error("Failed to fetch"));
    render(<StyleView initial={STYLE} />);

    const andrew = screen.getByRole("radio", { name: /Andrew/ });
    fireEvent.click(andrew);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(andrew).not.toBeDisabled();
    expect(screen.getByRole("alert").textContent).toMatch(/could not reach the engine/);
  });

  it("puts the slider back to the value actually in force", async () => {
    // `local` follows the thumb while dragging. On a failed save it would sit
    // showing a number nothing is using, which reads as success next to an error.
    updateStyle.mockResolvedValue({ ok: false, error: "no" });
    render(<StyleView initial={STYLE} />);

    const slider = screen.getByRole("slider", { name: /Volume/ });
    fireEvent.change(slider, { target: { value: "0.6" } });
    expect(screen.getByText("60%")).toBeInTheDocument();

    fireEvent.pointerUp(slider);

    await waitFor(() => expect(screen.getByText("12%")).toBeInTheDocument());
  });
});

describe("demo mode", () => {
  it("never calls the engine and still moves", () => {
    // The screen exists so the design can be judged without an engine. Writing to
    // one that is not there is the failure this branch avoids.
    render(<StyleView initial={STYLE} demo />);

    fireEvent.click(screen.getByRole("radio", { name: /Andrew/ }));

    expect(updateStyle).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: /Andrew/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
