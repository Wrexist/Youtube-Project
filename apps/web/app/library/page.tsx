import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { fileUrl, getJobs } from "@/lib/engine";
import { LIBRARY } from "@/lib/demo";

/** Library is a gallery, not a table. Thumbnails carry the visual weight —
 *  they are the thing being judged, so they get the pixels.
 *
 *  Reads completed jobs from `GET /v1/jobs?status=completed`. It used to render
 *  `lib/demo.ts` unconditionally, so a video you had just made never appeared here
 *  and six invented view counts were presented as though they were yours.
 *
 *  Views and CTR are deliberately absent from the live view: those come from the
 *  YouTube Analytics API and only exist for a video that has been *published*. A
 *  rendered file has no views, and showing "0 views" would be a different lie from
 *  the one this replaces.
 */
export default async function LibraryPage() {
  const jobs = await getJobs("completed");
  const live = jobs !== null;

  return (
    <>
      <Header
        title="Library"
        meta={
          <span className="flex items-center gap-2">
            {live ? `${jobs.length} rendered` : `${LIBRARY.length} videos`}
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        {live && jobs.length === 0 ? (
          <Card className="p-6">
            <p className="text-[14px] text-[var(--color-muted)]">
              Nothing rendered yet. Finished videos land here.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {live
              ? jobs.map((video) => (
                  <article key={video.id}>
                    <div className="relative aspect-video overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)]">
                      {video.thumbnail_keys?.[0] && (
                        // eslint-disable-next-line @next/next/no-img-element -- the engine serves these, not Next's optimiser
                        <img
                          src={fileUrl(video.thumbnail_keys[0])}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      )}
                    </div>
                    <h3 className="mt-2.5 line-clamp-2 text-[14px] leading-snug font-semibold">
                      {video.topic || "Untitled"}
                    </h3>
                    <p className="mono mt-1 text-[12px] text-[var(--color-faint)]">
                      ${(video.cost_usd ?? 0).toFixed(2)}
                      {video.render_key && (
                        <>
                          {" · "}
                          <a
                            href={fileUrl(video.render_key)}
                            className="underline decoration-[var(--color-line)] underline-offset-2 hover:decoration-current"
                          >
                            watch
                          </a>
                        </>
                      )}
                    </p>
                  </article>
                ))
              : LIBRARY.map((video) => (
                  <article key={video.id}>
                    <div className="relative aspect-video overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)]">
                      <span className="mono absolute right-2 bottom-2 rounded bg-black/75 px-1.5 py-0.5 text-[11px] text-white">
                        {video.dur}
                      </span>
                    </div>
                    <h3 className="mt-2.5 line-clamp-2 text-[14px] leading-snug font-semibold">
                      {video.title}
                    </h3>
                    <p className="mono mt-1 text-[12px] text-[var(--color-faint)]">
                      {video.views} views · {video.ctr}% CTR
                    </p>
                  </article>
                ))}
          </div>
        )}
      </Page>
    </>
  );
}
