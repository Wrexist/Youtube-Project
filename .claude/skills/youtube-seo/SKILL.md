---
name: youtube-seo
description: Rules and scoring heuristics for generating YouTube titles, descriptions, tags, and chapters. Use whenever writing or reviewing SEO metadata generation code, prompt chains that produce titles/descriptions/tags, the SEO panel UI, or when asked to optimize a specific video's metadata. Also use when working on keyword research or competitor title mining.
---

# YouTube SEO

Metadata decides whether a video is seen. Production quality decides whether it's finished. Weight effort accordingly.

## Hard limits — enforce in code, not just prompts

| Field | Hard limit | Practical target |
|---|---|---|
| Title | 100 chars | **≤ 60** — beyond that it truncates in search and suggested |
| Description | 5,000 chars | first **~150 chars** are all most people see |
| Tags | 500 chars total | 15–25 tags |
| Thumbnail | 2 MB, 1280×720 | JPG, ≥ 16:9 |

Validate these in the model layer. A title that passes the LLM but fails the API is a bug caught too late.

## Titles

Generate **8 variants across distinct strategies**, never 8 rewordings of one idea:

1. **Curiosity gap** — states a tension without resolving it. "Nobody tells you what happens after…"
2. **Number/list** — "7 things…". Reliable, saturated, lower ceiling.
3. **Contrarian** — inverts an assumption the audience holds.
4. **Outcome** — the result the viewer wants, stated plainly.
5. **Question** — only when the question is one the viewer actively has.
6. **Negative/warning** — "Stop doing X". High CTR, risks clickbait perception.
7. **Authority/specific** — a concrete number, date, or credential.
8. **Story** — implies a narrative with a stake.

Score each variant on:
- **Length** — penalty above 60 chars, hard fail above 100
- **Keyword position** — primary keyword in the first 40% of the title
- **Front-loaded interest** — the first 3 words must carry weight; mobile truncates hardest
- **Specificity** — a number, name, or concrete noun beats an abstraction
- **Promise/payoff match** — the title must be deliverable by the script. Overpromising kills retention, and retention outranks CTR.

Never generate a title with clickbait the script does not fulfill. Log the strategy label with each variant — Phase 8 attributes CTR back to strategy.

## Descriptions

Structure, in order:

1. **Hook paragraph (≤ 150 chars)** — restates the promise, contains the primary keyword naturally. This is what shows in search results.
2. **Body (200–400 words)** — expands with secondary keywords woven in as prose. Never a keyword list. YouTube reads this for topic classification.
3. **Chapters** — `0:00 Title` format, first must be `0:00`, minimum 3 chapters, each ≥ 10 seconds. Derived from script beats mapped onto **actual subtitle timings**, not estimates.
4. **Links / resources** — sources cited in the script go here. This matters for the inauthentic-content policy as much as for the viewer.
5. **Hashtags** — 3 maximum. The first three appear above the title. More than 15 causes YouTube to ignore all of them.

## Tags

- 15–25 tags, under 500 chars total.
- Mix: 2–3 head terms (high volume, high competition), 8–12 mid-tail, 5–10 long-tail phrases matching actual search queries.
- Include one tag that is the exact title.
- Include common misspellings of the primary keyword only if volume data supports it.
- Tags are a weak ranking signal now. Do not spend generation budget optimizing them past this.

## Keyword grounding — the part that isn't optional

An LLM guessing keywords is worthless. Ground every SEO package in:

1. **YouTube autocomplete** — query the suggest endpoint with the seed term plus each letter a–z. Free, and it reflects real search behavior.
2. **Competitor mining** — `search.list` for the primary keyword, pull the top 20 titles and their view counts. Extract shared patterns and gaps. Costs 100 quota units per call — cache aggressively.
3. **Volume data** — the Semrush MCP is connected on this machine (`keyword_research`, `competitors_research`). Use it for volume and difficulty when the topic has web search overlap.

A `SeoPackage` with no `keyword_sources` recorded is incomplete. Fail the job rather than shipping ungrounded metadata.

## Scoring output shape

```python
{
  "variants": [
    {"text": str, "strategy": str, "score": float, "reasons": {"length": ..., "keyword_pos": ...}}
  ],
  "chosen": int,          # index — but the user always gets the final say in the UI
  "keyword_sources": [...] # required, non-empty
}
```

## Anti-patterns

- Keyword stuffing in the description — actively penalized.
- Reusing one tag set across a series. Overlap is fine; identical is a signal.
- Chapters generated from the script outline rather than the rendered subtitle timings — they'll drift and break.
- Emoji in titles. Rarely helps, often reads as low quality in the niches this system targets.
