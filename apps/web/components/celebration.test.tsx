/**
 * The celebration is a moment, not chrome: it announces itself politely,
 * blocks nothing, and takes itself off screen. These tests pin all three,
 * because the failure mode of a game layer is overstaying.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Celebration } from "./celebration";

describe("Celebration", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("announces the moment via a polite live region", () => {
    render(<Celebration label="Video complete" detail="+100 XP" onDone={() => {}} />);
    const toast = screen.getByRole("status");
    expect(toast).toHaveTextContent("Video complete");
    expect(toast).toHaveTextContent("+100 XP");
    expect(toast).toHaveAttribute("aria-live", "polite");
  });

  it("never intercepts a click — the overlay is pointer-events-none", () => {
    const { container } = render(<Celebration label="Done" onDone={() => {}} />);
    expect(container.firstElementChild?.className).toContain("pointer-events-none");
  });

  it("calls onDone once the moment is over, and not before", () => {
    const onDone = vi.fn();
    render(<Celebration label="Done" onDone={onDone} />);
    act(() => vi.advanceTimersByTime(2000));
    expect(onDone).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1000));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("stops its timer when unmounted early", () => {
    const onDone = vi.fn();
    const { unmount } = render(<Celebration label="Done" onDone={onDone} />);
    unmount();
    act(() => vi.advanceTimersByTime(5000));
    expect(onDone).not.toHaveBeenCalled();
  });
});
