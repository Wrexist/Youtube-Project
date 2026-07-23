---
name: thumbnail-design
description: Rules for generating and composing YouTube thumbnails — concept generation, image generation, text overlay, safe zones, and true-scale preview. Use when building the thumbnail pipeline, the variant picker UI, or reviewing generated thumbnails.
---

# Thumbnail Design

The thumbnail and the title are a single unit. They should not repeat each other — the thumbnail shows what the title withholds, or vice versa.

## Specs

- 1280×720, 16:9, JPG, **≤ 2 MB** (API rejects larger)
- Judged at **168px wide on mobile**. That is the design target. Anything that only reads at full size has failed.
- Safe zones: the bottom-right ~15% is covered by the duration badge. Keep the focal point off-center-left.

## Rules

1. **Three to five words maximum.** Ideally three. Every word past that is unread.
2. **Huge type.** Cap height ≥ 15% of the thumbnail height. Heavy weight, tight tracking.
3. **Contrast over prettiness.** Text needs a hard edge — stroke, shadow, or a solid shape behind it. Subtlety disappears at 168px.
4. **One focal point.** A face, an object, or a number. Not a composition.
5. **Faces work**, even in faceless channels — a reaction shot as an element, not as the subject.
6. **Do not repeat the title.** If the title asks a question, the thumbnail shows the stakes.
7. **Series consistency** — a recognizable visual signature (a color, a corner mark, a type treatment) so a returning viewer identifies the channel before reading anything.

## Pipeline

```
script core tension → 3 distinct concepts → image gen per concept → text overlay → true-scale preview → variant stored
```

- **Concepts** come from the script's tension, not its topic. "How X went wrong" → an image of the wrongness, not of X.
- **Image generation** goes through a provider interface. The Higgsfield MCP `generate_image` is available on this machine; keep it swappable.
- **Text overlay** is composed by us in code (Pillow or a headless browser render), never baked into the generated image — generated text is unreliable and unusable for A/B variants.
- **Store all variants.** Phase 8 swaps thumbnails on underperforming videos and attributes the result.

## Preview component

The `ThumbnailPreview` component must render at true feed sizes simultaneously: mobile 168px, desktop grid 360px, sidebar suggestion 168px. Reviewing at full size is how bad thumbnails ship.

## Anti-patterns

- Text over a busy region of the image with no backing shape
- Thin or light font weights
- More than two type sizes
- Red arrows and circles — dated, and reads as low-effort in most niches
- Faces generated with visible artifacts, especially hands and eyes — reject and regenerate rather than shipping
- Reusing the same generated face across a series without it being an intentional character
