---
name: studio-ui
description: The Studio design system — colors, type, spacing, layout, motion, component patterns, and accessibility rules for the web app. Use before writing or editing ANY UI code, component, screen, or style in apps/web, and when reviewing whether a screen matches the product's minimal aesthetic.
---

# Studio UI

Full spec: [docs/UI-DESIGN.md](../../../docs/UI-DESIGN.md). Read it for screen-by-screen design. This skill is the enforcement checklist.

## Before writing any component

1. Does this screen have exactly **one** primary action? If not, demote the others.
2. Can the advanced options be collapsed behind a disclosure? They should be, by default.
3. Is the accent color used on less than 5% of the pixels? It must be.
4. Does it work at 375px wide if it's part of the approval flow (Queue, publish action)?

## Tokens — use these, never raw values

```css
--bg: #0A0A0B;      --surface: #141416;  --raised: #1C1C1F;
--border: #26262A;  --border-hover: #33333A;
--text: #FAFAFA;    --muted: #A1A1AA;    --faint: #71717A;
--accent: #FF3B30;
--success: #34D399; --warn: #FBBF24;     --danger: #F87171;
```

Spacing: 4 · 8 · 12 · 16 · 24 · 32 · 48. Nothing else.
Type sizes: 12 · 14 · 16 · 20 · 32. Weights: 400 and 600 only.
Radius: 6 (buttons) · 8 (cards, inputs) · 12 (modals).

Light mode inverts the neutral ramp; the accent is unchanged. Both ship together — never add a dark-only style.

## Structural rules

- **No top navigation bar.** Left rail (64px icon-only, expands on hover) + page header carrying title and the one primary action.
- **No breadcrumbs.** Hierarchy is one level deep.
- **Slide-over panels, not new pages,** for detail views.
- **⌘K command palette** is the escape hatch that lets every screen stay sparse. Register new actions there rather than adding buttons.
- Content column max-width 1200px, centered.

## Elevation

Dark mode: no drop shadows. Use `--raised` plus a 1px `--border`. Light mode: one soft shadow maximum, never stacked.

## Motion

- 150ms hover/press · 250ms panels · 400ms pipeline stage transitions
- Entrances: `cubic-bezier(0.32, 0.72, 0, 1)`. Exits: `ease-out`.
- **Skeletons, not spinners**, wherever the content shape is known.
- Motion only communicates state change. Nothing decorative animates.
- `prefers-reduced-motion` degrades all of the above to instant.

## Data & state

- Server Components by default; `"use client"` only where interaction requires it.
- Types from `packages/contracts`. Never hand-write a type mirroring an API response.
- Mutations via Server Actions. TanStack Query only for SSE-backed live job views.
- Long-running work streams over SSE and survives reload. Never gate a screen behind a full-page loading state.

## Accessibility — verified, not assumed

- Text contrast ≥ 4.5:1, UI boundaries ≥ 3:1. `--muted` on `--surface` is the usual failure — check it.
- Every state distinguishable without color. Stage rows get icon + text, not just a colored dot.
- Full keyboard nav. Never `outline: none` without a visible replacement.
- Live regions announce pipeline stage completion.

## Charts

Follow the `dataviz` skill. On top of it: one accent per chart, direct labels over legends, no gridline clutter, and the retention map gets more space than anything else on the Analytics screen.

## Rejected on sight

- A settings page with more than ~8 controls
- A table where a gallery belongs (the Library is a gallery)
- Three or more type sizes in one component
- A spinner where a skeleton fits
- Any component library dropped in wholesale — Radix primitives, styled by us
