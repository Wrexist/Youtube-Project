/**
 * The weekly review card.
 *
 * Five states, and the interesting thing about four of them is that they look
 * alike and mean different things: a first review, a week where nothing moved, a
 * week with changes, a worker that is stopped, and an engine that never answered.
 * Collapsing any pair of those produces a screen that states something untrue with
 * total confidence, which is the failure mode this whole feature exists to avoid.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Review } from "@studio/contracts";

import { NoReviewYet, WeeklyReview } from "./weekly-review";

function review(over: Partial<Review> = {}): Review {
  return {
    generated_at: "2026-08-10T06:00:00+00:00",
    video_count: 12,
    is_first: false,
    worth_reading: false,
    confirmed_count: 2,
    findings: [],
    skipped: [],
    changes: [],
    ...over,
  };
}

describe("a review that exists", () => {
  it("says nothing changed rather than showing an empty list", () => {
    render(<WeeklyReview review={review()} />);

    expect(screen.getByText(/Nothing changed/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("distinguishes a first review from a quiet one", () => {
    // Both have no changes and they mean opposite things: one has nothing to
    // compare against, the other compared and found nothing.
    render(<WeeklyReview review={review({ is_first: true })} />);

    expect(screen.getByText(/First review/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument();
  });

  it("lists each change as the sentence the engine composed", () => {
    const changed = review({
      worth_reading: true,
      changes: [
        {
          kind: "promoted",
          was: "suggestive",
          about: null,
          sentence: "Question hooks beat statements on CTR. Confirmed this week.",
          finding: null,
        },
        {
          kind: "reversed",
          was: "confirmed",
          about: null,
          sentence: "This reverses last week's finding — treat both as unsafe.",
          finding: null,
        },
      ],
    });

    render(<WeeklyReview review={changed} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/Confirmed this week/)).toBeInTheDocument();
    // The kind is a word, not only a colour — UI-DESIGN.md requires every state to
    // be readable without hue, and "reversed" is the one nobody should miss.
    expect(screen.getByText("reversed")).toBeInTheDocument();
  });

  it("renders the date in UTC, not the server's zone", () => {
    // The cron fires 06:00 UTC. Formatted in a US zone that is the previous day,
    // so the card would name the wrong Monday.
    render(<WeeklyReview review={review({ generated_at: "2026-08-10T02:00:00+00:00" })} />);

    expect(screen.getByText(/10 Aug/)).toBeInTheDocument();
  });
});

describe("no review yet", () => {
  it("blames the worker only when it knows the worker is stopped", () => {
    render(<NoReviewYet workerRunning={false} />);

    expect(screen.getByText(/needs the render worker/)).toBeInTheDocument();
  });

  it("says it will run when the worker is up", () => {
    render(<NoReviewYet workerRunning={true} />);

    expect(screen.getByText(/next Monday/)).toBeInTheDocument();
    expect(screen.queryByText(/needs the render worker/)).not.toBeInTheDocument();
  });

  it("does not report a stopped worker when the engine never answered", () => {
    // The engine client returns null for a failed request and for a successful
    // empty one alike. Collapsing them tells the operator to fix a worker when
    // what is actually down is the engine.
    render(<NoReviewYet workerRunning={null} />);

    expect(screen.getByText(/engine did not answer/)).toBeInTheDocument();
    expect(screen.queryByText(/needs the render worker/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No review has run yet/)).not.toBeInTheDocument();
  });
});
