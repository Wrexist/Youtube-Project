---
description: Generate a grounded SEO package (titles, description, tags, chapters) for a topic or video
argument-hint: <topic or video id>
---

Produce a full SEO package for: **$ARGUMENTS**

Delegate to the `seo-strategist` agent. It must ground everything in real data — YouTube autocomplete, competitor titles from `search.list`, and Semrush volume data where the topic overlaps web search.

Return:
- 8 title variants, each labeled with its strategy and score, with the reasoning visible
- The description in the required structure, with live character counts for the first 150 chars
- 15–25 tags, total under 500 chars
- Chapters, if a rendered subtitle file exists for this video — otherwise say chapters need the render first
- The competitor gap being exploited, in one sentence

If grounding data can't be retrieved, say so and stop rather than guessing.
