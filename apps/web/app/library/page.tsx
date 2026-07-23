import { Header, Page } from "@/components/ui";
import { LIBRARY } from "@/lib/demo";

/** Library is a gallery, not a table. Thumbnails carry the visual weight —
 *  they are the thing being judged, so they get the pixels. */
export default function LibraryPage() {
  return (
    <>
      <Header title="Library" meta={<span>{LIBRARY.length} videos</span>} />
      <Page>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {LIBRARY.map((video) => (
            <article key={video.id} className="group cursor-pointer">
              <div className="relative aspect-video overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-raised)] transition-colors duration-150 group-hover:border-[var(--color-line-hover)]">
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
      </Page>
    </>
  );
}
