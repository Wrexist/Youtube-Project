import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { EpisodeBuilder, type BuilderClip } from "./episode-builder";

const previewEpisode = vi.hoisted(() => vi.fn());
const buildEpisode = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({ previewEpisode, buildEpisode }));

const CLEARED_PREVIEW = {
  publishable: true,
  headline: "Cleared to publish.",
  thresholds_version: 1,
  rights: { cleared: true, ungranted: [], problems: {} },
  transformation: {
    passed: true,
    signals: [
      {
        name: "segment_count",
        severity: "ok",
        message: "2 distinct source clips",
        value: 2,
        threshold: 3,
      },
    ],
  },
};

beforeEach(() => {
  previewEpisode.mockReset();
  buildEpisode.mockReset();
  previewEpisode.mockResolvedValue({ ok: true, data: CLEARED_PREVIEW });
  buildEpisode.mockResolvedValue({ ok: true, data: { job_id: "job123" } });
});

function clip(id: string, caption: string): BuilderClip {
  return { id, handle: `@${id}`, caption, duration: 30, lane: "own", cleared: true };
}

const TWO = [clip("a", "first clip"), clip("b", "second clip")];

/**
 * Render, and let the pre-check settle before asserting.
 *
 * The preview is an async effect, so a synchronous assertion races it and React
 * logs an unwrapped-update warning for state that lands after the test body. The
 * wait is not incidental — it is what makes these tests observe the same DOM a
 * user would.
 */
async function renderBuilder(clips = TWO, live = true) {
  const onRemove = vi.fn();
  render(<EpisodeBuilder clips={clips} live={live} onRemove={onRemove} />);
  if (live && clips.length > 0) {
    await waitFor(() => expect(previewEpisode).toHaveBeenCalled());
  }
  return onRemove;
}

function order() {
  return screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
}

// ── ordering ────────────────────────────────────────────────────────────────

describe("ordering the cut list", () => {
  it("starts in selection order", async () => {
    await renderBuilder();
    expect(order()[0]).toContain("first clip");
  });

  it("moves a clip earlier", async () => {
    await renderBuilder();

    fireEvent.click(screen.getByRole("button", { name: /move second clip earlier/i }));

    expect(order()[0]).toContain("second clip");
    // Reordering re-fires the pre-check; settle it so the state it sets lands
    // inside the test rather than after it.
    await waitFor(() => expect(previewEpisode).toHaveBeenCalledTimes(2));
  });

  it("moves a clip later", async () => {
    await renderBuilder();

    fireEvent.click(screen.getByRole("button", { name: /move first clip later/i }));

    expect(order()[0]).toContain("second clip");
    await waitFor(() => expect(previewEpisode).toHaveBeenCalledTimes(2));
  });

  it("cannot move the ends past themselves", async () => {
    await renderBuilder();

    expect(screen.getByRole("button", { name: /move first clip earlier/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /move second clip later/i })).toBeDisabled();
  });

  it("is reachable without dragging", async () => {
    // A cut list is a sequence, and "move up" is one keystroke away from working
    // for someone who cannot drag.
    await renderBuilder();
    expect(screen.getAllByRole("button", { name: /move .* (earlier|later)/i })).toHaveLength(4);
  });

  it("removes a clip through the parent, which owns the selection", async () => {
    const onRemove = await renderBuilder();

    fireEvent.click(screen.getByRole("button", { name: /remove first clip/i }));

    expect(onRemove).toHaveBeenCalledWith("a");
  });
});

// ── the pre-check ───────────────────────────────────────────────────────────

describe("the pre-check", () => {
  it("asks the engine to score the proposed cut list", async () => {
    await renderBuilder();

    await waitFor(() => expect(previewEpisode).toHaveBeenCalled());
    const sent = previewEpisode.mock.calls[0][0];
    expect(sent.segments.map((s: { source_id: string }) => s.source_id)).toEqual(["a", "b"]);
    expect(sent.is_compilation).toBe(true);
  });

  it("predicts narration, because the workflow refuses to build without it", async () => {
    await renderBuilder();

    await waitFor(() => expect(previewEpisode).toHaveBeenCalled());
    expect(previewEpisode.mock.calls[0][0].segments.every((s: { narrated: boolean }) => s.narrated))
      .toBe(true);
  });

  it("re-scores when the order changes", async () => {
    await renderBuilder();
    await waitFor(() => expect(previewEpisode).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /move second clip earlier/i }));

    await waitFor(() => expect(previewEpisode).toHaveBeenCalledTimes(2));
    expect(previewEpisode.mock.calls[1][0].segments[0].source_id).toBe("b");
  });

  it("says originality is judged on the finished edit", async () => {
    // The honest half of the split: rights are settled now, the rest depends on
    // an edit that does not exist yet.
    await renderBuilder();

    expect(await screen.findByText(/judged on the finished edit/i)).toBeInTheDocument();
  });

  it("names the clips with no grant", async () => {
    previewEpisode.mockResolvedValue({
      ok: true,
      data: {
        ...CLEARED_PREVIEW,
        publishable: false,
        rights: { cleared: false, ungranted: ["b"], problems: {} },
      },
    });
    await renderBuilder();

    expect(await screen.findByText(/b: no grant recorded/i)).toBeInTheDocument();
  });

  it("does not call the engine in demo mode", async () => {
    await renderBuilder(TWO, false);
    expect(previewEpisode).not.toHaveBeenCalled();
  });
});

// ── building ────────────────────────────────────────────────────────────────

describe("building", () => {
  async function withTopic(text = "the mistake these share") {
    await renderBuilder();
    fireEvent.change(screen.getByPlaceholderText(/mistake all three/i), {
      target: { value: text },
    });
  }

  it("will not build without a topic", async () => {
    await renderBuilder();

    const build = screen.getByRole("button", { name: /build episode/i });
    expect(build).toBeDisabled();
    expect(build).toHaveAttribute("title", expect.stringMatching(/what the episode is about/i));
  });

  it("starts the repurpose workflow with the clips in order", async () => {
    await withTopic();

    fireEvent.click(screen.getByRole("button", { name: /build episode/i }));

    await waitFor(() => expect(buildEpisode).toHaveBeenCalled());
    expect(buildEpisode.mock.calls[0][0]).toMatchObject({
      sourceIds: ["a", "b"],
      aspect: "9:16",
      topic: "the mistake these share",
    });
  });

  it("sends the order the operator arranged, not the selection order", async () => {
    await withTopic();
    fireEvent.click(screen.getByRole("button", { name: /move second clip earlier/i }));

    fireEvent.click(screen.getByRole("button", { name: /build episode/i }));

    await waitFor(() => expect(buildEpisode).toHaveBeenCalled());
    expect(buildEpisode.mock.calls[0][0].sourceIds).toEqual(["b", "a"]);
  });

  it("switches aspect", async () => {
    await withTopic();
    fireEvent.click(screen.getByRole("button", { name: /long-form/i }));

    fireEvent.click(screen.getByRole("button", { name: /build episode/i }));

    await waitFor(() => expect(buildEpisode).toHaveBeenCalled());
    expect(buildEpisode.mock.calls[0][0].aspect).toBe("16:9");
  });

  it("links to the running job", async () => {
    await withTopic();

    fireEvent.click(screen.getByRole("button", { name: /build episode/i }));

    const link = await screen.findByRole("link", { name: /watch it build/i });
    expect(link).toHaveAttribute("href", "/?job=job123");
  });

  it("surfaces a refusal rather than swallowing it", async () => {
    buildEpisode.mockResolvedValue({
      ok: false,
      error: "these clips are not cleared for use",
    });
    await withTopic();

    fireEvent.click(screen.getByRole("button", { name: /build episode/i }));

    expect(await screen.findByText(/not cleared for use/i)).toBeInTheDocument();
  });

  it("cannot be built in demo mode", async () => {
    render(<EpisodeBuilder clips={TWO} live={false} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/mistake all three/i), {
      target: { value: "a real topic" },
    });

    const build = screen.getByRole("button", { name: /build episode/i });
    expect(build).toBeDisabled();
    expect(build).toHaveAttribute("title", expect.stringMatching(/engine running/i));
  });
});

// ── the running total ───────────────────────────────────────────────────────

describe("the running total", () => {
  it("caps each clip at the segment length rather than its full duration", async () => {
    render(
      <EpisodeBuilder
        clips={[{ ...clip("a", "a long clip"), duration: 300 }]}
        live
        onRemove={vi.fn()}
      />,
    );

    // 20s per segment, not 300.
    expect(await screen.findByText(/0:20/)).toBeInTheDocument();
  });

  it("renders nothing at all with no clips", () => {
    const { container } = render(<EpisodeBuilder clips={[]} live onRemove={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
