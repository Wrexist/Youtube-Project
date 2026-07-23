---
description: Review a screen or component against the Studio design system
argument-hint: <screen or component path/name>
---

Review **$ARGUMENTS** against the design system.

Load the `studio-ui` skill and `docs/UI-DESIGN.md` first. Then actually look at it — start the dev server, load the screen in the browser, check desktop and 375px, check light and dark.

Report findings against the bar:
- One primary action, and it reads as primary
- Advanced options collapsed by default
- Accent under 5% of pixels
- Only tokens used, no raw hex or off-scale spacing
- Two type weights, at most two sizes per component
- Contrast: text ≥ 4.5:1, boundaries ≥ 3:1 — measure `--muted` specifically
- States readable without color
- Keyboard navigable with visible focus
- Skeletons not spinners
- `prefers-reduced-motion` handled
- Both themes correct

For each issue, give the file and line and the specific fix. Rank by how much it hurts the user. If the screen is clean, say so briefly rather than inventing findings.
