"""Genre intelligence — what the niche rewards, learned from public metadata.

Three capabilities, one rule:

  * **sync** pulls recent uploads for every *watched* competitor channel via the
    Data API's own playlists (≈1 quota unit per channel, not the 100 a
    `search.list` costs) and keeps view counters fresh so velocity is a
    subtraction, not a history table.
  * **patterns** reads that corpus structurally: which hook strategies the
    niche's winners actually use, how long their videos run, how often they
    post. These numbers feed the script and SEO prompts with evidence instead
    of vibes.
  * **gaps** crosses demand signals (autocomplete, trending — both free) against
    supply measured on the watchlist corpus (zero quota), and hands the result
    to `ideas.build_backlog_async` as `competitor_counts` — upgrading idea
    scoring from "competition never measured" to "measured against the channels
    that matter".

The rule: this package mines **metadata only**. Titles, counters, publish
dates — the same public facts any viewer's subscription feed shows. It does not
download competitor media and must never grow a function that does; footage has
its own rights system (`repurpose.rights`), and discovery-without-acquisition
is the line that keeps this side of it.
"""

from engine.genre import gaps, patterns, sync

__all__ = ["gaps", "patterns", "sync"]
