# UI & Dashboard Design Spec

The system is complicated. The interface must not be. Every screen should feel like it does one thing, and do it without ceremony.

## Principles

1. **One primary action per screen.** Everything else is secondary or hidden behind ⌘K.
2. **Progressive disclosure.** Defaults are good enough to never open the advanced panel. The panel exists; it's collapsed.
3. **Motion means something.** Animation only communicates state change — a stage completing, a card arriving. Nothing decorative moves.
4. **The content is the interface.** Thumbnails, video previews, and titles are the visual weight. Chrome recedes.
5. **Never block on a spinner.** Long work streams progress. The user can leave and come back.

## Foundations

**Color** — dark-first, light supported. One accent, used sparingly (primary buttons, active states, the single most important number on a screen). Everything else is neutral.

```
bg          #0A0A0B    surface   #141416    raised   #1C1C1F
border      #26262A    (hover #33333A)
text        #FAFAFA    muted     #A1A1AA    faint    #71717A
accent      #FF3B30    (YouTube red, desaturated — used at <5% of pixels)
success     #34D399    warn      #FBBF24    danger   #F87171
```

Light mode inverts the neutral ramp; the accent stays. Both themes ship at launch, not later.

**Type** — Inter (UI) + JetBrains Mono (numbers, IDs, durations). Two weights: 400 and 600. Sizes: 12 / 14 / 16 / 20 / 32. That's it.

**Space** — 4px base. Use 8, 12, 16, 24, 32, 48. Nothing else.

**Radius** — 8px on cards and inputs, 6px on buttons, 12px on modals. Full-round only on avatars and pills.

**Elevation** — no drop shadows in dark mode; use the `raised` surface and a 1px border. Light mode gets one soft shadow, never two.

**Density** — comfortable, not compact. This is a tool used deliberately, not a trading terminal.

## Layout

```
┌────┬──────────────────────────────────────────┐
│    │  page title              [primary action]│
│ ▪  ├──────────────────────────────────────────┤
│ ▪  │                                          │
│ ▪  │   content — max-width 1200, centered     │
│ ▪  │                                          │
│    │                                          │
│ ⚙  │                                          │
└────┴──────────────────────────────────────────┘
```

- **Left rail**, 64px, icon-only with tooltips. Expands to 220px on hover or pin. Items: Create · Queue · Library · Calendar · Analytics · Settings.
- **No top nav bar.** The page header carries the title and the single primary action.
- **No breadcrumbs.** The hierarchy is one level deep everywhere.

## The five screens

### 1. Create — the screen that matters

A single centered input: *"What's the video about?"* Below it, three quiet chips: **Short 9:16** / **Long-form 16:9** / pick a saved Series. Nothing else visible.

On submit the input becomes a **pipeline view** — a vertical stack of stages, each collapsing to a one-line summary as it completes:

```
✓  Research          8 sources
✓  Hook              "Nobody tells you this about…"     [3 variants ▾]
✓  Script            1,240 words · ~8:20
◐  Voiceover         generating… ▓▓▓▓▓▓░░░░ 62%
○  Materials
○  Subtitles
○  Render
○  SEO package
○  Thumbnail
```

Each completed stage is clickable → expands inline to show and **edit** the output. Editing a stage marks downstream stages stale and offers to re-run from there. This is the core interaction of the product; get it right before anything else.

Streams over SSE. Survives a page reload. Closing the tab does not cancel the job.

### 2. Queue

Job cards in a single column. Each card: thumbnail (or a shimmering placeholder), title, current stage, elapsed time, cost so far. Failed jobs show the failing stage and a **Retry from here** button — never a bare "failed".

Filters as segmented control: All · Running · Needs review · Failed. No filter sidebar.

### 3. Library

Grid of finished videos at real thumbnail proportions — this is a gallery, not a table. Hover plays a 3-second silent preview. Click opens a detail panel (slide-over, not a new page) with the video, its SEO package, its metrics, and a publish/schedule action.

### 4. Calendar

Month view. Each scheduled video is a small thumbnail chip on its day. Drag to reschedule. A thin line at the top of each week shows quota consumed vs. the 10k/day ceiling — this is the one place the API limit is made visible without being alarming.

### 5. Analytics — the dashboard

Not a wall of charts. A **narrative**, top to bottom:

1. **One number, large:** views in the last 28 days, with a sparkline and delta. Nothing else competes with it.
2. **Three tiles:** CTR · average view duration · subscribers gained. Each with a 28-day sparkline and a delta chip. Muted colors; the accent appears only on the tile that changed most.
3. **What's working** — the payoff of the whole system. Plain sentences backed by data:
   > Curiosity-gap titles average **6.2% CTR** across 23 videos, vs **4.1%** for number-led titles.
   > Videos with a question in the first 3 seconds hold **+12%** at the 30-second mark.

   Each statement expands to the underlying videos.
4. **Retention map** — for a selected video, the retention curve with script beats overlaid, so a drop-off points at the sentence that caused it. This is the single most valuable visualization in the product; it deserves the space.
5. Per-video table last, collapsed by default.

Charts follow the `dataviz` skill conventions: no gridline clutter, direct labels over legends, one accent per chart.

## Components worth building carefully

- **Stage row** — the pipeline primitive. Four states (pending / running / done / error), each visually distinct without color alone.
- **Variant picker** — for titles, hooks, thumbnails. Side-by-side, each with its score and reasoning on hover. Selection is one click.
- **Thumbnail preview** — renders at true feed sizes (mobile 168px, desktop 360px, sidebar 168px). Designers lie to themselves at full size; this component prevents that.
- **SEO panel** — live character counters, a YouTube-search-result mock showing exactly how the title and description will appear, keyword density indicator.
- **Cost chip** — always visible on a running job. Small, muted, honest.
- **Command palette (⌘K)** — new video, jump to any video by title, run a series, open settings. The escape hatch that lets every screen stay minimal.

## Accessibility — not optional

- Contrast ≥ 4.5:1 for text, ≥ 3:1 for UI boundaries. The muted gray on dark surfaces is the usual failure point — verify it.
- Every state distinguishable without color (icons + text on stage rows).
- Full keyboard navigation; visible focus rings, never `outline: none` without a replacement.
- `prefers-reduced-motion` honored — pipeline animation degrades to instant state changes.
- Live regions announce stage completion for screen readers.

## Motion

- 150ms for hover/press. 250ms for panels and slide-overs. 400ms only for the pipeline stage transitions.
- Easing: `cubic-bezier(0.32, 0.72, 0, 1)` for entrances, plain `ease-out` for exits.
- Skeletons, never spinners, for content that has a known shape.

## The play layer

The app should be satisfying to use — a little like a game, never like a casino.
The line between those two is drawn by four rules, and everything playful in the
product lives inside them:

1. **Derived from real work, never invented.** XP is finished videos and
   publishes; levels and achievements are pure functions of the jobs table
   (`lib/progress.ts`). Delete the layer and no data is lost. A number that
   flatters is a number people learn to ignore.
2. **Moments, not chrome.** Celebration plays once at a real milestone — a
   finished render bursts confetti for two seconds and disappears completely
   (`components/celebration.tsx`). The one persistent fixture is the level chip
   at the bottom of the rail, which is exactly as loud as the Setup link above
   it. No progress rings on dashboards, no streak counters nagging from headers.
3. **It never gates or delays anything.** The overlay is `pointer-events-none`;
   the approval flow, the publish gate and every screen work identically with
   the layer ignored. A game layer you must engage with is a dark pattern.
4. **All existing rules still apply.** Token colors only, states readable
   without hue (the Series screen's week slots carry ticks, not just green),
   `prefers-reduced-motion` collapses every animation, and the accent budget is
   unchanged — confetti spends the ok/warn/ink tokens, not five new hues.

The vocabulary: **levels** (triangular XP ladder, named Newcomer → Mogul),
**achievements** (six, all verifiable from job history), the **week-slot row**
on Series cards (cadence as filled ticks), and **celebrations** (render
complete). Anything new joins this list only if it passes all four rules.

## Responsive

Desktop-first — this is a production tool. But **approval must work on a phone**: the Queue and the approve/publish action collapse to a clean mobile view. That's the only mobile requirement.
