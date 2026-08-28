import { Header, Page } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { getClips } from "@/lib/engine";
import { REPURPOSE_CLIPS, REPURPOSE_REPORT } from "@/lib/demo";
import { RepurposeView } from "./repurpose-view";

/**
 * Repurpose — clips in, publishable video out.
 *
 * The screen is organised around the thing that actually decides whether a clip is
 * usable, which is not how good it is. Every card leads with a rights chip, and a
 * clip with no grant can be read but not built with. That is why the rights panel
 * gets the slide-over and the fit score gets one line.
 *
 * Two gates stand between a clip and a published video, and they fail
 * independently: rights (may we use this) and transformation (is the result
 * original enough to monetise). Permission does not satisfy the second — YouTube's
 * reused-content rules apply regardless of whether the creator agreed — so the
 * report shows both verdicts separately rather than one blended score. "Cleared to
 * use, not yet original enough" is the common state, and it has a completely
 * different fix from its opposite.
 *
 * Falls back to `demo.ts` when the engine is unreachable, like every other screen,
 * and says so with a `LiveBadge`. See KNOWN-ISSUES §5.5.
 */
export default async function RepurposePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const clips = await getClips("main");
  const live = clips !== null;

  // The TikTok round trip comes back here with its outcome in the query string.
  // Read on the server and passed down rather than read with `useSearchParams`
  // in the view: that hook forces a client-side bailout, and Next refuses to
  // prerender a page using it outside a Suspense boundary — which fails the
  // build rather than degrading, so it is not a trade worth making for two
  // strings that the server already has.
  const params = await searchParams;
  const one = (key: string) => {
    const value = params[key];
    return (Array.isArray(value) ? value[0] : value) ?? null;
  };

  return (
    <>
      <Header
        title="Repurpose"
        meta={
          <span className="flex items-center gap-2">
            {live ? `${clips.clips.length} candidates` : `${REPURPOSE_CLIPS.length} candidates`}
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        <RepurposeView
          clips={live ? clips.clips : null}
          demoClips={REPURPOSE_CLIPS}
          demoReport={REPURPOSE_REPORT}
          tiktokOutcome={one("tiktok")}
          tiktokError={one("tiktok_error")}
        />
      </Page>
    </>
  );
}
