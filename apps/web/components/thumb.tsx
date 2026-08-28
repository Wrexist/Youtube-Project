"use client";

import { useState } from "react";

/**
 * A generated thumbnail, which quietly disappears if the file is not there.
 *
 * The engine serves these out of its own storage, so the key in a job row and
 * the file on disk can disagree: storage pruned, a volume not mounted, a render
 * cleaned up while its job row survived. A bare `<img>` answers that with the
 * browser's broken-image glyph — a torn page icon on every card, which reads as
 * "this app is broken" rather than "one file is missing".
 *
 * Hiding on error leaves the neutral `--color-raised` panel the container
 * already draws, which is exactly what a job that produced no thumbnail shows.
 * The two cases look the same because they are the same to the person looking:
 * there is no picture, and nothing they can do about it from here.
 *
 * A client component only because `onError` is a browser event — the screens
 * using it stay Server Components.
 */
export function Thumb({ src, className = "" }: { src: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- the engine serves these, not Next's optimiser
    <img
      src={src}
      alt=""
      onError={() => setFailed(true)}
      className={className || "h-full w-full object-cover"}
    />
  );
}
