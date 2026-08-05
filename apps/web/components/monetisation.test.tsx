import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MonetisationCard } from "./monetisation";
import type { Monetisation } from "@studio/contracts";

/** The card that says whether the operator is getting paid yet.
 *
 *  It shipped with a projection that could name a date months away while the thing
 *  actually blocking monetisation was years out — see `test_the_eta…` below. That
 *  bug survived a full engine test suite because it lives entirely in the component.
 */

type Threshold = Monetisation["subscribers"];

function threshold(over: Partial<Threshold> = {}): Threshold {
  const current = over.current ?? 0;
  const target = over.target ?? 1000;
  return {
    name: "subscribers",
    unit: "subscribers",
    current,
    target,
    met: current >= target,
    remaining: Math.max(0, target - current),
    fraction: Math.min(1, current / target),
    window_days: 1,
    covers_full_window: true,
    days_remaining: null,
    ...over,
  } as Threshold;
}

function data(over: Partial<Monetisation> = {}): Monetisation {
  return {
    eligible: false,
    route: "watch_hours",
    blocking: [],
    caveat: null,
    subscriber_count_hidden: false,
    subscribers: threshold({ name: "subscribers", current: 50, target: 1000 }),
    watch_hours: threshold({ name: "watch hours", current: 100, target: 4000, unit: "hours" }),
    shorts_views: threshold({ name: "Shorts views", current: 0, target: 10_000_000, unit: "views" }),
    ...over,
  } as Monetisation;
}

describe("MonetisationCard", () => {
  it("does not promise a date when another threshold is the real blocker", () => {
    // 4,000 hours banked, 50 subscribers. The old card read the watch-hours
    // projection and announced "about 2 months" — the subscriber gate was years
    // away and gated both routes.
    render(
      <MonetisationCard
        data={data({
          subscribers: threshold({ current: 50, target: 1000 }),
          watch_hours: threshold({
            name: "watch hours",
            current: 3900,
            target: 4000,
            unit: "hours",
            window_days: 365,
            days_remaining: 60,
          }),
          blocking: ["subscribers"],
        })}
      />,
    );
    expect(screen.queryByText(/at this rate/)).toBeNull();
  });

  it("shows the projection once the other threshold is met", () => {
    render(
      <MonetisationCard
        data={data({
          subscribers: threshold({ current: 1200, target: 1000 }),
          watch_hours: threshold({
            name: "watch hours",
            current: 3000,
            target: 4000,
            unit: "hours",
            window_days: 365,
            days_remaining: 60,
          }),
        })}
      />,
    );
    expect(screen.getByText(/about 2 months at this rate/)).toBeInTheDocument();
  });

  it("names what is holding monetisation up", () => {
    render(<MonetisationCard data={data({ blocking: ["subscribers"] })} />);
    expect(screen.getByText(/Held up by subscribers/)).toBeInTheDocument();
  });

  it("names both blockers when both are outstanding", () => {
    // `blocking` is a list because both halves of a route can be outstanding at
    // once. It was typed `str | None` on the engine, which made the endpoint 500
    // for every channel that was not already monetised.
    render(
      <MonetisationCard data={data({ blocking: ["subscribers", "watch hours"] })} />,
    );
    expect(
      screen.getByText(/Held up by subscribers and watch hours/),
    ).toBeInTheDocument();
  });

  it("draws only the route in play, never both", () => {
    render(<MonetisationCard data={data({ route: "watch_hours" })} />);
    expect(screen.getByLabelText(/watch hours/i)).toBeInTheDocument();
    // A Shorts bar at 0.001% next to a healthy one makes real progress look worse
    // than it is — the cockpit `docs/UI-DESIGN.md` rules out.
    expect(screen.queryByLabelText(/Shorts views/i)).toBeNull();
  });

  it("exposes each bar as a progressbar with its own value", () => {
    render(<MonetisationCard data={data()} />);
    const bars = screen.getAllByRole("progressbar");
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveAttribute("aria-valuenow", "5");
  });

  it("says so when the subscriber count is hidden rather than drawing a zero", () => {
    render(<MonetisationCard data={data({ subscriber_count_hidden: true })} />);
    expect(screen.getByText(/hides its subscriber count/)).toBeInTheDocument();
    // The engine sends 0 as the placeholder. Drawn as a bar it reads as a fact.
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect(screen.getByText(/Subscriber count unavailable/)).toBeInTheDocument();
  });

  it("shows the caveat when the window is incomplete", () => {
    render(<MonetisationCard data={data({ caveat: "Measured over 30 days, not 365." })} />);
    expect(screen.getByText(/Measured over 30 days/)).toBeInTheDocument();
  });
});
