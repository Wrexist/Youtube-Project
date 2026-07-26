/**
 * Says whether the numbers on screen came from the engine or from demo data.
 *
 * The app is designed to run with no engine — that is how the design stayed
 * judgeable before the plumbing existed. The risk that creates is someone reading
 * a demo quota figure as real, so a screen that fell back has to admit it.
 *
 * Deliberately quiet: a small neutral chip, not a banner. When the engine is up
 * this renders nothing at all — "working normally" is not news, and the design
 * system's whole premise is that a screen shows one thing.
 */
export function LiveBadge({ live }: { live: boolean }) {
  if (live) return null;

  return (
    <span
      title="The engine is not reachable, so this screen is showing sample data."
      className="rounded-full border border-[var(--color-line)] px-2 py-0.5 text-[11px] text-[var(--color-faint)]"
    >
      demo data
    </span>
  );
}
