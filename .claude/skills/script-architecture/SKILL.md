---
name: script-architecture
description: How to structure faceless YouTube scripts for retention — hooks, beat structure, pacing, and the research-grounding requirement. Use when building or editing the script generation chain, hook generation, outline logic, duration targeting, or when reviewing generated script quality.
---

# Script Architecture

Retention beats everything. A great title gets the click; the first 30 seconds decide whether the video is recommended to anyone else.

## The chain — never single-shot

MoneyPrinterTurbo generates a script in one LLM call. That is why its output is generic. Replace it with:

```
research → angle selection → hook (3 variants) → beat outline → full script → self-critique → revision
```

Each stage is separately inspectable and re-runnable from the Create screen. Store the prompt and model at every stage.

### 1. Research
Web search + fetch. Extract **specific facts**: numbers, dates, names, studies, quotes. Retain source URLs — they go in the description and are part of the inauthentic-content defense.

A script with no retained sources should fail the job, not ship.

### 2. Angle selection
Given the research, generate 3 distinct angles on the topic and pick the one with the sharpest tension. Generic topic + generic angle = unwatchable video, regardless of production quality.

### 3. Hook — the highest-leverage 30 seconds of work
Generate 3 variants. A hook must, within **3 seconds**:
- Open a loop the viewer needs closed, **or**
- State something that contradicts what they believe, **or**
- Show the payoff up front and promise the how

It must not: introduce the channel, say "in this video", greet the viewer, or explain what's coming. Those are retention leaks and every one of them is standard LLM output — prompt explicitly against them.

Score hooks on: time-to-tension (words before the interesting thing), specificity, and whether the script actually delivers on it.

### 4. Beat outline
Break the body into beats. Each beat carries:

```
{
  "purpose": "why this exists in the video",
  "text_direction": "what is said",
  "visual_direction": "what is shown",   # drives per-beat material matching
  "energy": "high" | "medium" | "low",   # drives clip pacing in the render
  "est_seconds": float
}
```

`visual_direction` is what makes the render stop looking like random stock footage. Do not skip it.

### 5. Full script
Write from the beats. Rules:
- Second person. Short sentences. No subordinate clauses stacked three deep.
- One idea per sentence — the TTS has no ability to convey nested structure.
- Re-open a loop every 30–45 seconds in long-form. Explicit "but here's the problem" turns.
- Numbers and specifics over adjectives. "Grew 340% in eight months" beats "grew dramatically".
- No "in conclusion", no summarizing what was just said, no sign-off longer than one line.

### 6. Self-critique
A separate call that reads the script cold and answers: where does attention drop? Which sentence is filler? Does the hook's promise get paid? Then revise. This single extra pass is the largest quality delta in the chain.

## Duration targeting

Words → seconds via **the actual configured TTS rate**, not a constant. Measure it once per voice and cache. Iterate the script length until within ±10% of target, adjusting beat count rather than padding prose.

Targets:
- **Shorts (9:16):** 30–55s. Under 60 to stay in the Shorts shelf. ~90–140 words.
- **Long-form (16:9):** 8–12 min is the sweet spot for ad revenue with achievable retention. ~1,200–1,800 words.

## Long-form differences

- Needs an explicit chapter structure — 5–8 chapters, each self-contained enough to survive a viewer jumping to it.
- Needs a mid-roll retention device around the 40% mark, where drop-off concentrates.
- Cannot sustain a single energy level. Alternate `energy` across beats; the render uses this for clip pacing and music.

## Anti-patterns in generated scripts — check for these explicitly

- "In today's video we're going to be talking about…" — delete on sight
- "But first, let me explain…" — a stall, cut it
- Rhetorical questions with obvious answers
- Lists of adjectives where a number belongs
- A conclusion that summarizes rather than lands
- Any sentence the TTS will mispronounce (unexpanded acronyms, symbols, unusual proper nouns) — normalize before synthesis
