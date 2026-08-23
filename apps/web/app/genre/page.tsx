import { Header, Page } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { getGenrePatterns, getGenreWatchlist } from "@/lib/engine";
import { DEMO_GENRE_PATTERNS, DEMO_GENRE_WATCHLIST } from "@/lib/demo";
import { GenreView } from "./genre-view";

/**
 * Genre — what the niche rewards, learned from the channels we chose to watch.
 *
 * A Server Component reading `/v1/genre/watchlist` and `/v1/genre/patterns`, so
 * the evidence on screen is what the script and SEO prompts actually see.
 * Falls back to the demo fixture like every other screen, for the same reason:
 * the design is judged before the plumbing exists.
 *
 * Deliberately not a dashboard. The patterns half reads as four plain
 * sentences — the same shape Analytics' "What's working" section uses — because
 * this screen's job is to answer "what should I make next and why", not to be
 * explored.
 */
export default async function GenrePage() {
  const [watchlist, patterns] = await Promise.all([
    getGenreWatchlist(),
    getGenrePatterns(),
  ]);
  const live = watchlist !== null;

  return (
    <>
      <Header title="Genre" meta={<LiveBadge live={live} />} />
      <Page>
        <GenreView
          initialWatchlist={watchlist ?? DEMO_GENRE_WATCHLIST}
          initialPatterns={patterns ?? DEMO_GENRE_PATTERNS}
          demo={!live}
        />
      </Page>
    </>
  );
}
