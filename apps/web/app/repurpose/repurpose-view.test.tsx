import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { RepurposeView } from "./repurpose-view";
import { REPURPOSE_CLIPS, REPURPOSE_REPORT } from "@/lib/demo";

/**
 * The screen's whole job is refusing to let a clip become a video too early, so
 * these are mostly about what stays disabled and what the disabled thing says.
 */

function renderView(clips: Parameters<typeof RepurposeView>[0]["clips"] = null) {
  return render(
    <RepurposeView
      clips={clips}
      demoClips={REPURPOSE_CLIPS}
      demoReport={REPURPOSE_REPORT}
    />,
  );
}

describe("rights gating", () => {
  it("refuses to build with a clip that has no recorded rights", () => {
    renderView();

    // @analyst has no lane in the fixture.
    const card = screen.getByRole("article", { name: /why this chart is lying/i });
    const add = within(card).getByRole("button", { name: /add to episode/i });

    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", expect.stringMatching(/how this clip may be used/i));
  });

  it("refuses to build with a clip whose grant has lapsed", () => {
    renderView();

    const card = screen.getByRole("article", {
      name: /the one that started the whole trend/i,
    });

    expect(within(card).getByRole("button", { name: /add to episode/i })).toBeDisabled();
    expect(within(card).getByText(/lapsed/i)).toBeInTheDocument();
  });

  it("allows a cleared clip to be added", () => {
    renderView();

    const card = screen.getByRole("article", { name: /compound interest/i });
    const add = within(card).getByRole("button", { name: /add to episode/i });
    expect(add).toBeEnabled();

    fireEvent.click(add);

    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
  });

  it("marks rights state with a symbol as well as a colour", () => {
    // docs/UI-DESIGN.md: every state distinguishable without colour.
    renderView();
    expect(screen.getAllByText(/yours|cleared|no rights|lapsed/i).length).toBeGreaterThan(0);
  });
});

describe("the build action", () => {
  it("stays disabled with nothing selected, and says why", () => {
    renderView();
    const build = screen.getByRole("button", { name: /build episode/i });
    expect(build).toBeDisabled();
    expect(build).toHaveAttribute("title", expect.stringMatching(/engine|cleared clip/i));
  });

  it("stays disabled in demo mode even once a clip is selected", () => {
    // A button that appears to work against no engine is the failure
    // KNOWN-ISSUES §5.5 is about.
    renderView();

    const card = screen.getByRole("article", { name: /compound interest/i });
    fireEvent.click(within(card).getByRole("button", { name: /add to episode/i }));

    expect(screen.getByRole("button", { name: /build episode/i })).toBeDisabled();
  });
});

describe("the originality report", () => {
  it("shows rights and transformation as separate verdicts", () => {
    renderView();

    expect(screen.getByText("Rights")).toBeInTheDocument();
    expect(screen.getByText("Transformation")).toBeInTheDocument();
    // The fixture is cleared-but-blocked, which is the state a single blended
    // score would hide.
    expect(screen.getByText(/✓ cleared/)).toBeInTheDocument();
    expect(screen.getByText(/✕ 2 failing/)).toBeInTheDocument();
  });

  it("lists the failing checks with their reasons", () => {
    renderView();
    expect(screen.getByText(/longest unbroken lift is 22s/i)).toBeInTheDocument();
    expect(screen.getByText(/needs 50%/i)).toBeInTheDocument();
  });

  it("records which threshold version judged the video", () => {
    // Tuning is impossible if nobody knows which numbers were in force.
    renderView();
    expect(screen.getByText(/thresholds v1/i)).toBeInTheDocument();
  });

  it("says the thresholds are not an official algorithm", () => {
    renderView();
    expect(screen.getByText(/not to a\s+published algorithm/i)).toBeInTheDocument();
  });
});

describe("the rights panel", () => {
  it("opens on a card and offers the two built lanes", () => {
    renderView();

    fireEvent.click(screen.getByText(/why this chart is lying/i));

    const panel = screen.getByRole("dialog");
    expect(within(panel).getByText(/my own clip/i)).toBeInTheDocument();
    expect(within(panel).getByText(/paid campaign/i)).toBeInTheDocument();
  });

  it("asks for evidence only where there is a counterparty", () => {
    renderView();

    fireEvent.click(screen.getByText(/why this chart is lying/i));
    const panel = screen.getByRole("dialog");

    // Lane A is selected by default and needs nothing.
    expect(within(panel).queryByPlaceholderText("https://…")).not.toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("radio", { name: /paid campaign/i }));

    expect(within(panel).getByPlaceholderText("https://…")).toBeInTheDocument();
    expect(within(panel).getByText(/is a claim that you have permission/i)).toBeInTheDocument();
  });

  it("warns that permission does not make the video monetisable", () => {
    // The correction the research forced: the two gates are independent, and a
    // screen that implies otherwise teaches the wrong thing.
    renderView();

    fireEvent.click(screen.getByText(/why this chart is lying/i));

    expect(
      within(screen.getByRole("dialog")).getByText(/regardless of whether the creator agreed/i),
    ).toBeInTheDocument();
  });
});

describe("live data", () => {
  it("renders engine clips when they are there", () => {
    renderView([
      {
        id: "x1",
        platform: "tiktok",
        external_id: "x1",
        url: "",
        creator_handle: "@live",
        caption: "a real clip from the engine",
        hashtags: [],
        stats: { views: 1000 },
        duration_s: 30,
        fit_score: 0.5,
        fit_reasons: [],
        status: "discovered",
        grant: null,
        cleared: false,
        acquired: false,
      },
    ] as unknown as Parameters<typeof RepurposeView>[0]["clips"]);

    expect(screen.getByText(/a real clip from the engine/i)).toBeInTheDocument();
    expect(screen.queryByText(/compound interest/i)).not.toBeInTheDocument();
  });

  it("shows an empty state rather than demo clips when the engine has none", () => {
    renderView([]);
    expect(screen.getByText(/no candidates yet/i)).toBeInTheDocument();
  });
});
