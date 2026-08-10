"""Is this clip worth making a video out of, for *this* channel?

Fit is not a property of a clip. The same TikTok is an obvious yes for one channel
and irrelevant to the next, so every score here is computed against a channel's
published history and stored with `channel_key` beside it.

**What this deliberately is not:** a virality predictor. `engine/ideas.py` opens
with the same disclaimer and it applies twice over here — a clip's *past* view
count on TikTok says almost nothing about how the same footage performs on YouTube
under different narration, in a different aspect ratio, to a different audience.
Reach is reported as evidence that the moment landed *somewhere*, weighted low, and
never dressed up as a forecast.

Everything is derived from real data or it is zero. The components:

  * **adjacency** — how close this is to what the channel already publishes,
    reused from `ideas.similarity` so a clip here and an idea there cannot disagree
    about what "on topic" means.
  * **demand** — whether anyone searches YouTube for this, via the same
    autocomplete sweep the SEO chain runs. A clip that is huge on TikTok and
    unsearched on YouTube is a Shorts candidate, not a long-form one.
  * **reach** — how the clip did on its own platform. Saturating and weighted low.
  * **usability** — length, and whether it is already cleared. A clip nobody may
    touch is ranked below one that is ready, because the operator's next action
    differs completely.

`saturation` is subtracted rather than scored: a clip near-identical to something
the channel published last week is worse than a mediocre new one, and the corpus
checks in `gate.py` would refuse it at the end anyway. Better to rank it down here
than to spend a render finding out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.ideas import similarity, tokenize

#: How the components combine. Adjacency dominates because it is the one that
#: decides whether the finished video belongs on the channel at all — the others
#: adjust the order within "things that would fit".
WEIGHTS = {
    "adjacency": 0.40,
    "demand": 0.25,
    "reach": 0.15,
    "usability": 0.20,
}

#: Views above which more views stop meaning anything for ranking purposes. A clip
#: with 40M views is not four times the candidate a 10M one is.
REACH_SATURATION = 2_000_000

#: The window a clip has to fall inside to be usable without heavy re-cutting.
#: Below the floor there is nothing to build around; above the ceiling it is a
#: video in its own right and lifting it wholesale is the thing `gate.py` refuses.
MIN_USABLE_S = 5.0
MAX_USABLE_S = 180.0

#: Similarity to an existing upload above which a clip is treated as a repeat.
#: Matches `gate.MAX_CORPUS_SIMILARITY` on purpose — ranking a clip highly here
#: and then blocking the finished video for repetition would be the system
#: disagreeing with itself at the operator's expense.
SATURATION_THRESHOLD = 0.85


@dataclass
class Fit:
    """A clip's score for one channel, and why."""

    score: float = 0.0
    adjacency: float = 0.0
    demand: float = 0.0
    reach: float = 0.0
    usability: float = 0.0
    saturation: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "adjacency": round(self.adjacency, 3),
            "demand": round(self.demand, 3),
            "reach": round(self.reach, 3),
            "usability": round(self.usability, 3),
            "saturation": round(self.saturation, 3),
            "reasons": self.reasons,
        }


def score_clip(
    *,
    caption: str,
    hashtags: list[str] | None = None,
    duration_s: float = 0.0,
    views: int = 0,
    channel_topics: list[str] | None = None,
    suggestions: list[str] | None = None,
    cleared: bool = False,
) -> Fit:
    """Score one clip against one channel.

    Takes plain values rather than a row so the arithmetic is testable without a
    database or a TikTok account — the same reason `monetisation.progress` takes
    mappings.
    """
    fit = Fit()
    hashtags = hashtags or []
    channel_topics = channel_topics or []
    suggestions = suggestions or []

    # The caption plus its hashtags is the only text a clip has. Hashtags carry
    # real signal on TikTok — they are how the platform's own topic model works —
    # so they are part of the text rather than a separate weighted term.
    text = " ".join([caption, *(h.lstrip("#") for h in hashtags)]).strip()
    tokens = tokenize(text)

    # ── adjacency ────────────────────────────────────────────────────────────
    if channel_topics and text:
        best = max((similarity(text, topic) for topic in channel_topics), default=0.0)
        # Doubled and clamped, matching `ideas.score_idea`: raw token overlap
        # between a caption and a video title runs low even when the subject is
        # plainly the same, and an unscaled figure would rank everything as a
        # poor fit.
        fit.adjacency = min(best * 2, 1.0)
        fit.saturation = best
        if fit.adjacency >= 0.6:
            fit.reasons.append("close to what this channel already covers")
        elif fit.adjacency <= 0.15:
            fit.reasons.append("only loosely related to this channel")
    elif not channel_topics:
        # No history to be adjacent to. Reported rather than scored as a zero,
        # which would rank every clip on a new channel identically and silently.
        fit.reasons.append("no published history yet — adjacency not measured")

    # ── demand ───────────────────────────────────────────────────────────────
    if suggestions and tokens:
        matches = sum(1 for s in suggestions if tokens & tokenize(s))
        fit.demand = min(matches / 20, 1.0)
        # Three-way, matching `api/ideas.py::_why`. The middle case used to say
        # nothing at all, which reads on the card as "demand was not measured" —
        # the one thing it definitely was. "Only 3 queries" is a finding.
        if fit.demand >= 0.5:
            fit.reasons.append(f"{matches} YouTube autocomplete queries match this")
        elif matches:
            fit.reasons.append(f"only {matches} YouTube autocomplete queries match this")
        else:
            fit.reasons.append("nobody searches YouTube for this phrasing")

    # ── reach ────────────────────────────────────────────────────────────────
    if views > 0:
        fit.reach = min(views / REACH_SATURATION, 1.0)
        if views >= 1_000_000:
            fit.reasons.append(f"{views / 1_000_000:.1f}M views on the source platform")

    # ── usability ────────────────────────────────────────────────────────────
    fit.usability, usability_note = _usability(duration_s, cleared)
    if usability_note:
        fit.reasons.append(usability_note)

    fit.score = round(
        sum(WEIGHTS[name] * getattr(fit, name) for name in WEIGHTS),
        3,
    )

    # Saturation is a penalty, not a component: a near-duplicate of last week's
    # upload is worse than a mediocre new clip, and `gate.py` would block the
    # finished video for it anyway. Ranking it down here saves the render.
    if fit.saturation >= SATURATION_THRESHOLD:
        fit.score = round(fit.score * 0.25, 3)
        fit.reasons.insert(0, "near-duplicate of something you already published")

    return fit


def _usability(duration_s: float, cleared: bool) -> tuple[float, str]:
    """Length and rights state, as one number and one sentence.

    Rights are folded in here rather than kept separate because they change what
    the operator does next, and the grid is sorted by score: an uncleared clip
    sitting above three ready ones sends them to a dead end. It is a *ranking*
    input only — nothing here decides whether a clip may be used, which is
    `rights.py`'s job alone.
    """
    if duration_s <= 0:
        return (0.5 if cleared else 0.25), ""

    if duration_s < MIN_USABLE_S:
        return 0.0, f"only {duration_s:.0f}s — too short to build around"
    if duration_s > MAX_USABLE_S:
        return (
            0.2,
            f"{duration_s / 60:.0f} minutes — a video in its own right, not a clip",
        )

    # Inside the window, the middle is best: 20–70s is a segment you can cut to a
    # beat, where 6s needs three more like it and 170s needs heavy trimming.
    ideal = 1.0 if 20 <= duration_s <= 70 else 0.7

    if not cleared:
        # Halved rather than zeroed. An uncleared clip is still worth surfacing —
        # recording a grant is one click — it just should not outrank a ready one.
        return ideal * 0.5, "no rights recorded yet"
    return ideal, ""
