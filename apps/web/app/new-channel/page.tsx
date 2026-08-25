import { isLive, getLaunches } from "@/lib/engine";
import { CHANNEL_LAUNCH, MANUAL_STEPS } from "@/lib/demo";
import type { Launch } from "@studio/contracts";
import { LaunchView } from "./launch-view";

/** New channel — one input, everything else derived.
 *
 *  The honest framing matters here and is built into the screen: YouTube has no API
 *  for creating a channel. This designs the whole identity and validates it against
 *  the real limits, then hands over a short checklist of the clicks no API can make.
 *  Anything that pretended otherwise would fail at the worst possible moment.
 *
 *  A Server Component wrapper so the screen knows two things before it renders:
 *  whether the engine is up (the demo fixture is the fallback, badged), and whether
 *  a stored design already exists to resume — the manual steps take days, and a
 *  design that evaporated on reload made the checklist pointless.
 */
export default async function NewChannelPage() {
  const [live, launches] = await Promise.all([isLive(), getLaunches()]);

  return (
    <LaunchView
      live={live}
      resumable={(launches ?? []).filter((l) => l.status === "completed").slice(0, 3)}
      demo={DEMO_LAUNCH}
    />
  );
}

/** The demo fixture reshaped to the live contract, so the screen has one render
 *  path and the only difference demo makes is the badge. */
const DEMO_LAUNCH: Launch = {
  id: "demo",
  status: "completed",
  error: null,
  niche: "how large structures fail",
  stages: [
    { name: "grounding", title: "Niche research", status: "done", summary: "", error: null },
    { name: "positioning", title: "Positioning", status: "done", summary: "", error: null },
    { name: "naming", title: "Name & handle", status: "done", summary: "", error: null },
    { name: "about", title: "About", status: "done", summary: "", error: null },
    { name: "visuals", title: "Visual identity", status: "done", summary: "", error: null },
    { name: "series", title: "Series plan", status: "done", summary: "", error: null },
    { name: "backlog", title: "First 30 ideas", status: "done", summary: "", error: null },
  ],
  identity: {
    name: CHANNEL_LAUNCH.identity.name,
    handle: CHANNEL_LAUNCH.identity.handle,
    tagline: CHANNEL_LAUNCH.identity.tagline,
    description: CHANNEL_LAUNCH.identity.description,
    keywords: CHANNEL_LAUNCH.identity.keywords,
    keywords_string: CHANNEL_LAUNCH.identity.keywordsString,
    avatar_concept: CHANNEL_LAUNCH.identity.avatarConcept,
    banner_concept: CHANNEL_LAUNCH.identity.bannerConcept,
    palette: CHANNEL_LAUNCH.identity.palette,
  },
  positioning: null,
  name_options: null,
  visuals: null,
  series: {
    series: CHANNEL_LAUNCH.series.map((s) => ({
      name: s.name,
      format: s.format,
      pattern: s.pattern,
      per_week: s.perWeek,
    })),
  },
  backlog: CHANNEL_LAUNCH.backlog.map((b) => ({
    topic: b.topic,
    score: b.score,
    duplicate_of: null,
  })),
  problems: [],
  blocked: false,
  manual_steps: MANUAL_STEPS.map((s) => ({ ...s, url: null })),
  cost_usd: 0,
};
