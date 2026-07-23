import { Header, Page } from "@/components/ui";
import { Calendar } from "@/components/calendar";
import { PENDING_VIDEOS, QUOTA_BY_DAY } from "@/lib/demo";

export default function CalendarPage() {
  return (
    <>
      <Header
        title="Calendar"
        meta={<span>Drag to schedule · 4 uploads/day max</span>}
      />
      <Page>
        <Calendar videos={PENDING_VIDEOS} quotaByDay={QUOTA_BY_DAY} />
      </Page>
    </>
  );
}
