import { Handoff } from "./handoff";

/**
 * Where a provider's callback lands, for about two hundred milliseconds.
 *
 * Consent opens in a popup, so the engine's callback cannot redirect to the
 * screen the operator started from: that would leave a second full copy of
 * Studio inside a 520-pixel window, while the window they are actually looking
 * at — the one with the button they pressed — never hears a thing.
 *
 * This page exists to close that gap and nothing else. It hands the outcome to
 * the window that opened it and closes itself; when there is no such window it
 * forwards to the screen the redirect used to point at, with the query string
 * that screen already knows how to read. Every large integration has a page
 * like this and none of them are meant to be looked at.
 *
 * `searchParams` is read here rather than with `useSearchParams` below for the
 * reason `/repurpose` documents: that hook forces a client bailout and Next
 * refuses to prerender a page using it outside a Suspense boundary, which fails
 * the build rather than degrading.
 */
export default async function ConnectedPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const one = (key: string) => {
    const value = params[key];
    return (Array.isArray(value) ? value[0] : value) ?? "";
  };

  return (
    <Handoff
      provider={one("provider") === "tiktok" ? "tiktok" : "youtube"}
      ok={one("status") === "ok"}
      reason={one("reason")}
      source={one("source")}
      returnTo={one("return_to") === "repurpose" ? "repurpose" : "setup"}
    />
  );
}
