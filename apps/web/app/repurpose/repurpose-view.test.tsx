import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { RepurposeView } from "./repurpose-view";
import { REPURPOSE_CLIPS, REPURPOSE_REPORT } from "@/lib/demo";

const saveGrant = vi.hoisted(() => vi.fn());
const findClips = vi.hoisted(() => vi.fn());
const revokeClip = vi.hoisted(() => vi.fn());
// Adding a clip mounts the episode builder, which imports these two.
const previewEpisode = vi.hoisted(() => vi.fn());
const buildEpisode = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({
  saveGrant,
  findClips,
  revokeClip,
  previewEpisode,
  buildEpisode,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => {
  saveGrant.mockReset();
  saveGrant.mockResolvedValue({ ok: true, data: {} });
  findClips.mockReset();
  findClips.mockResolvedValue({
    ok: true,
    data: { found: 3, configured: true, connected: true },
  });
  revokeClip.mockReset();
  revokeClip.mockResolvedValue({ ok: true, data: {} });
  previewEpisode.mockReset();
  previewEpisode.mockResolvedValue({ ok: true, data: null });
  buildEpisode.mockReset();
  buildEpisode.mockResolvedValue({ ok: true, data: null });
});

/** One live clip with a clean grant — the only state the revoke control appears in. */
function clearedClip(overrides: Record<string, unknown> = {}) {
  return {
    id: "clip-1",
    handle: "@someone",
    caption: "a clip we are allowed to use",
    url: "https://tiktok.example/v/1",
    duration_s: 24,
    views: 1000,
    fit_score: 0.8,
    cleared: true,
    grant: { lane: "own", cleared: true, problems: [] },
    ...overrides,
  } as unknown as NonNullable<Parameters<typeof RepurposeView>[0]["clips"]>[number];
}

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

    expect(screen.getByText(/from 1 clip/i)).toBeInTheDocument();
  });

  it("offers no way to revoke a clip that has no permission to withdraw", () => {
    renderView([clearedClip({ cleared: false, grant: null })]);

    expect(screen.queryByRole("button", { name: /revoke/i })).not.toBeInTheDocument();
  });

  it("takes two presses to revoke, and the second one says so", async () => {
    // The second press is the confirmation. A modal for this would be ceremony;
    // an unguarded button next to "Add to episode" would be pressed by accident.
    renderView([clearedClip()]);

    const revoke = screen.getByRole("button", { name: /^revoke$/i });
    fireEvent.click(revoke);

    expect(revokeClip).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /really revoke/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /really revoke/i }));
    await waitFor(() => expect(revokeClip).toHaveBeenCalledWith("clip-1"));
  });

  it("says so when the revocation is refused, rather than looking like it worked", async () => {
    revokeClip.mockResolvedValue({ ok: false, error: "the engine did not answer" });
    renderView([clearedClip()]);

    fireEvent.click(screen.getByRole("button", { name: /^revoke$/i }));
    fireEvent.click(screen.getByRole("button", { name: /really revoke/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/did not answer/i);
  });

  it("drops a revoked clip from the episode it was already in", async () => {
    renderView([clearedClip()]);

    fireEvent.click(screen.getByRole("button", { name: /add to episode/i }));
    expect(screen.getByText(/from 1 clip/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^revoke$/i }));
    fireEvent.click(screen.getByRole("button", { name: /really revoke/i }));

    // Building with a clip whose permission was just withdrawn is the exact
    // thing the rights gate exists to prevent.
    await waitFor(() => expect(screen.queryByText(/from 1 clip/i)).not.toBeInTheDocument());
  });

  it("marks rights state with a symbol as well as a colour", () => {
    // docs/UI-DESIGN.md: every state distinguishable without colour.
    renderView();
    expect(screen.getAllByText(/yours|cleared|no rights|lapsed/i).length).toBeGreaterThan(0);
  });
});

describe("the build action", () => {
  it("offers no episode at all until a clip is added", () => {
    // The builder is absent rather than present-and-empty: a Build button with
    // nothing to build is the inert control queue/page.tsx rules out.
    renderView();

    expect(screen.queryByRole("button", { name: /build episode/i })).not.toBeInTheDocument();
    expect(screen.getByText(/add a cleared clip to start an episode/i)).toBeInTheDocument();
  });

  it("appears once a cleared clip is added", () => {
    renderView();

    const card = screen.getByRole("article", { name: /compound interest/i });
    fireEvent.click(within(card).getByRole("button", { name: /add to episode/i }));

    expect(screen.getByRole("button", { name: /build episode/i })).toBeInTheDocument();
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

describe("recording a grant", () => {
  /** Live, because the whole point of these is the request that goes out. In
   *  demo mode the control is correctly disabled — covered separately below. */
  function openPanel() {
    renderView([
      {
        id: "x1",
        platform: "tiktok",
        external_id: "x1",
        url: "",
        creator_handle: "@analyst",
        caption: "why this chart is lying to you",
        hashtags: [],
        stats: {},
        duration_s: 33,
        fit_score: 0.79,
        fit_reasons: [],
        status: "discovered",
        grant: null,
        cleared: false,
        acquired: false,
      },
    ] as unknown as Parameters<typeof RepurposeView>[0]["clips"]);
    fireEvent.click(screen.getByText(/why this chart is lying/i));
    return screen.getByRole("dialog");
  }

  it("records Lane A with no paperwork", async () => {
    const panel = openPanel();

    fireEvent.click(within(panel).getByRole("button", { name: /^record$/i }));

    await waitFor(() => expect(saveGrant).toHaveBeenCalledTimes(1));
    expect(saveGrant.mock.calls[0][1]).toMatchObject({ lane: "own" });
  });

  it("will not submit a campaign grant without its evidence", () => {
    // The engine refuses an unevidenced grant. Finding that out after a round
    // trip is a worse version of the same answer.
    const panel = openPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /paid campaign/i }));

    const record = within(panel).getByRole("button", { name: /^record$/i });

    expect(record).toBeDisabled();
    expect(record).toHaveAttribute("title", expect.stringMatching(/link the terms/i));
    expect(saveGrant).not.toHaveBeenCalled();
  });

  it("submits a campaign grant once it is evidenced", async () => {
    const panel = openPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /paid campaign/i }));
    fireEvent.change(within(panel).getByPlaceholderText("@streamer"), {
      target: { value: "@streamer" },
    });
    fireEvent.change(within(panel).getByPlaceholderText("https://…"), {
      target: { value: "https://whop.example/c/1" },
    });

    fireEvent.click(within(panel).getByRole("button", { name: /^record$/i }));

    await waitFor(() => expect(saveGrant).toHaveBeenCalledTimes(1));
    expect(saveGrant.mock.calls[0][1]).toMatchObject({
      lane: "campaign",
      grantor: "@streamer",
      evidence_ref: "https://whop.example/c/1",
    });
  });

  it("shows one line per problem rather than a summary", async () => {
    saveGrant.mockResolvedValue({
      ok: false,
      error: "This grant is not usable as recorded.",
      blockers: [
        { code: "no_grantor", message: "a campaign grant must name who granted it" },
        { code: "no_evidence", message: "a campaign grant needs evidence" },
      ],
    });
    const panel = openPanel();

    fireEvent.click(within(panel).getByRole("button", { name: /^record$/i }));

    await screen.findByText(/must name who granted it/i);
    expect(screen.getByText(/needs evidence/i)).toBeInTheDocument();
  });

  it("says the edit is still judged separately after a successful save", async () => {
    const panel = openPanel();

    fireEvent.click(within(panel).getByRole("button", { name: /^record$/i }));

    await screen.findByText(/still judged separately/i);
  });

  it("cannot be recorded in demo mode", () => {
    // A control that appears to save into nothing is the failure
    // KNOWN-ISSUES §5.5 is about.
    renderView();
    fireEvent.click(screen.getByText(/why this chart is lying/i));

    const record = within(screen.getByRole("dialog")).getByRole("button", {
      name: /^record$/i,
    });

    expect(record).toBeDisabled();
    expect(record).toHaveAttribute("title", expect.stringMatching(/engine running/i));
    expect(saveGrant).not.toHaveBeenCalled();
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

/**
 * Finding clips.
 *
 * The screen used to say "nothing has been swept in yet" above no control that
 * swept anything, which made the whole TikTok integration unreachable from the
 * UI. These pin the way back out, and — more importantly — that the four ways a
 * sweep can come back empty stay four different sentences.
 */
describe("sweeping", () => {
  it("offers a way out of the empty state", async () => {
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    const sweep = screen.getByRole("button", { name: /find clips/i });
    expect(sweep).toBeEnabled();

    fireEvent.click(sweep);

    await waitFor(() => expect(findClips).toHaveBeenCalled());
    expect(await screen.findByRole("status")).toHaveTextContent(/3 clips/i);
  });

  it("sends an unconfigured install to Setup rather than showing an empty grid", async () => {
    findClips.mockResolvedValue({
      ok: true,
      data: { found: 0, configured: false, connected: false },
    });
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /find clips/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/not set up/i);
  });

  it("offers the connection itself when the keys are set but nobody signed in", async () => {
    // Distinct from "not set up", which needs a trip to Setup to paste keys.
    // Here the only thing missing is one press, so the press is offered here
    // rather than described as an errand on another screen.
    findClips.mockResolvedValue({
      ok: true,
      data: { found: 0, configured: true, connected: false },
    });
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /find clips/i }));

    expect(await screen.findByRole("button", { name: /connect tiktok/i })).toBeEnabled();
    expect(screen.getByRole("status")).not.toHaveTextContent(/not set up/i);
  });

  it("still sends you to Setup when the keys themselves are missing", async () => {
    // The one case that genuinely cannot be resolved from this screen.
    findClips.mockResolvedValue({
      ok: true,
      data: { found: 0, configured: false, connected: false },
    });
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /find clips/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/not set up yet/i);
    expect(screen.queryByRole("button", { name: /connect tiktok/i })).not.toBeInTheDocument();
  });

  it("says so on the way back from a refused sign-in", async () => {
    // The callback redirects here with the reason in the query string, which the
    // server component reads and hands down. Landing on a screen that says
    // nothing is how "did that work?" goes unanswered.
    render(
      <RepurposeView
        clips={[]}
        demoClips={REPURPOSE_CLIPS}
        demoReport={REPURPOSE_REPORT}
        tiktokError="you declined the request"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/you declined the request/i);
  });

  it("confirms a successful connection on the way back", async () => {
    render(
      <RepurposeView
        clips={[]}
        demoClips={REPURPOSE_CLIPS}
        demoReport={REPURPOSE_REPORT}
        tiktokOutcome="connected"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/tiktok connected/i);
  });

  it("says a connected-but-empty account is connected", async () => {
    findClips.mockResolvedValue({
      ok: true,
      data: { found: 0, configured: true, connected: true },
    });
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /find clips/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/no posts to work from/i);
  });

  it("surfaces a failed sweep instead of looking like an empty account", async () => {
    findClips.mockResolvedValue({ ok: false, error: "Reconnect the account to continue." });
    render(
      <RepurposeView clips={[]} demoClips={REPURPOSE_CLIPS} demoReport={REPURPOSE_REPORT} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /find clips/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/reconnect the account/i);
  });

  it("cannot sweep without an engine", () => {
    renderView();

    expect(screen.getByRole("button", { name: /find clips/i })).toBeDisabled();
  });
});
