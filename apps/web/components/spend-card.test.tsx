/**
 * The spend card.
 *
 * Money is the one thing on Analytics where a wrong number is worse than no
 * number, so the cases here are the ones where a figure could look confident and
 * be meaningless: no videos finished yet, and no spend in the window at all.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Spend } from "@studio/contracts";

import { SpendCard } from "./spend-card";

function spend(over: Partial<Spend> = {}): Spend {
  return {
    days: [
      { date: "2026-08-08", usd: 1.02, jobs: 1 },
      { date: "2026-08-09", usd: 2.44, jobs: 2 },
    ],
    total_usd: 3.46,
    month_usd: 3.46,
    per_video_usd: 1.73,
    completed_videos: 2,
    ...over,
  };
}

describe("spend", () => {
  it("shows the month, the per-video average and the window total", () => {
    render(<SpendCard spend={spend()} days={90} />);

    expect(screen.getByText("$1.73")).toBeInTheDocument();
    expect(screen.getByText("over 2 videos")).toBeInTheDocument();
    expect(screen.getAllByText("$3.46")).toHaveLength(2); // month and total
  });

  it("does not invent a per-video figure before any video has finished", () => {
    // `null` and `0.00` are different claims. A channel that has spent $4 on two
    // failed runs has not established that a video costs nothing.
    render(
      <SpendCard
        spend={spend({ per_video_usd: null, completed_videos: 0, total_usd: 4.1 })}
        days={90}
      />,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("no finished videos yet")).toBeInTheDocument();
  });

  it("says nothing was spent rather than drawing an empty chart", () => {
    render(
      <SpendCard
        spend={spend({ days: [], total_usd: 0, month_usd: 0, per_video_usd: null, completed_videos: 0 })}
        days={30}
      />,
    );

    expect(screen.getByText(/Nothing spent in this window/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("rounds away cents above ten dollars and keeps them below", () => {
    // "$1.02" is a video and the cents matter; "$147" is a month and they do not.
    render(<SpendCard spend={spend({ month_usd: 147.32, per_video_usd: 1.02 })} days={90} />);

    expect(screen.getByText("$147")).toBeInTheDocument();
    expect(screen.getByText("$1.02")).toBeInTheDocument();
  });
});
