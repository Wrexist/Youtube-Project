import { Header, Page } from "@/components/ui";
import { Calendar } from "@/components/calendar";
import { LiveBadge } from "@/components/live-badge";
import { getCalendar, getQuota } from "@/lib/engine";
import { PENDING_VIDEOS, QUOTA_BY_DAY } from "@/lib/demo";

/** Calendar — real quota when the engine is up, demo data when it is not.
 *
 *  A Server Component, so the fetch happens before the page is sent and there is
 *  no loading state to design around. Quota is the number that must never be
 *  stale or invented: it decides whether a publish is even possible, so when the
 *  engine is unreachable the header says so rather than showing demo figures as
 *  though they were real.
 */
export default async function CalendarPage() {
  const [quota, calendar] = await Promise.all([getQuota(), getCalendar()]);
  const live = quota !== null;

  const quotaByDay = calendar?.quota_by_day ?? quota?.by_day ?? QUOTA_BY_DAY;

  // Bookings the engine already holds. This page destructured only `quota_by_day`,
  // so an upload the engine had already scheduled was invisible — and the same day
  // could be double-booked against a ceiling the screen could not see.
  const initialScheduled = (calendar?.scheduled ?? []).map(
    (s: { video_id: string; at: string }) => ({ videoId: s.video_id, at: new Date(s.at) }),
  );

  return (
    <>
      <Header
        title="Calendar"
        meta={
          <span className="flex items-center gap-2">
            {live && quota
              ? `${quota.uploads_left} uploads left today · ${quota.remaining.toLocaleString()} of ${quota.limit.toLocaleString()} units`
              : "Drag to schedule · 4 uploads/day max"}
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        <Calendar
          videos={PENDING_VIDEOS}
          quotaByDay={quotaByDay}
          initialScheduled={initialScheduled}
          live={live}
          // The engine's own ceiling, not the client's copy of it. An approved
          // quota extension raises `limit`, and a screen still enforcing 10,000
          // would refuse drops the engine would accept. Undefined when the engine
          // is down, which falls back to the documented default.
          dailyLimit={quota?.limit}
          // Rendered once on the server and handed down, so the grid and the
          // is-this-day-past test agree between the server pass and the client
          // pass. Calling `new Date()` in both is a hydration mismatch by
          // construction.
          now={new Date().toISOString()}
        />
      </Page>
    </>
  );
}
