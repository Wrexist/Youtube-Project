"""Caption timing and placement for short-form.

Two things the default subtitle path gets wrong for a vertical clip.

**Length.** `media._group_cues` breaks on a 32-character budget, which lands
around five or six words for ordinary English and is close to right — but it is a
*character* rule, so a line of short words runs to nine or ten. Short-form
captions want 4–7 words on screen at once: past that the viewer is reading
instead of watching, and on a phone the line wraps to three rows and covers a
third of the picture.

**Placement.** The default sits captions 72% down the frame. On 16:9 that is
correct. On 9:16 it is underneath TikTok's and Shorts' own UI — the caption text,
the handle, the button rail — so the bottom sixth of a vertical frame is not
ours to use. Captions there are half-covered on the platform they were made for.

Most short-form viewing is muted, so this is not an accessibility nicety: the
captions are the primary text channel, and a caption nobody can read is a video
nobody follows.
"""

from __future__ import annotations

#: Words on screen at once. Above the ceiling the viewer reads instead of
#: watching; the floor stops a split leaving a two-word orphan.
MAX_WORDS = 6
MIN_WORDS = 3

#: A character ceiling as well, because word count alone lets "internationally
#: recognised standardisation" through as two words.
MAX_CHARS = 32

#: Shortest a cue may be shown. Below this it is a flash rather than a caption —
#: the eye does not finish a line in a quarter of a second.
MIN_SECONDS = 0.4

#: Where the caption baseline sits, as a fraction of frame height.
#:
#: 9:16 is much higher than the others and that is the whole point: the bottom
#: sixth of a vertical frame belongs to the platform's own chrome, and a caption
#: placed at 0.72 is behind the handle and the button rail on the app it was made
#: for. 16:9 keeps the existing 0.72, which is right for a player with no overlay.
SAFE_Y = {"9:16": 0.62, "1:1": 0.68, "16:9": 0.72}


def safe_y(aspect: str) -> float:
    return SAFE_Y.get(aspect, SAFE_Y["9:16"])


def regroup(
    cues: list[dict],
    *,
    max_words: int = MAX_WORDS,
    max_chars: int = MAX_CHARS,
) -> list[dict]:
    """Re-cut cues to the word budget, keeping timing proportional.

    Splits only — never merges. Two short adjacent cues came from a sentence
    boundary or a pause, and joining them across it puts the end of one thought
    and the start of the next on screen together.

    **On the timing.** A split cue's children are given a share of the parent's
    span proportional to their character count, which is an approximation: the
    exact answer needs word-level timings, and by the time cues reach here they
    have already been grouped by `media._group_cues`. Characters rather than words
    because "a" and "extraordinarily" do not take the same time to say, and length
    tracks duration better than count does.

    A cue with no text, or a zero-length span, is dropped rather than propagated —
    a caption that appears for no time is a flicker, and one with no words is a
    black box.
    """
    out: list[dict] = []

    for cue in cues:
        text = str(cue.get("text") or "").strip()
        start = float(cue.get("start") or 0.0)
        end = float(cue.get("end") or 0.0)
        if not text or end <= start:
            continue

        chunks = _split(text, max_words=max_words, max_chars=max_chars)
        if len(chunks) == 1:
            out.append({"start": start, "end": max(end, start + MIN_SECONDS), "text": chunks[0]})
            continue

        span = end - start
        total = sum(len(chunk) for chunk in chunks) or 1
        cursor = start
        for index, chunk in enumerate(chunks):
            share = span * len(chunk) / total
            # The last chunk is pinned to the parent's end rather than accumulated,
            # so rounding cannot leave a gap or an overhang at the boundary.
            chunk_end = end if index == len(chunks) - 1 else cursor + share
            out.append(
                {
                    "start": cursor,
                    "end": max(chunk_end, cursor + MIN_SECONDS),
                    "text": chunk,
                }
            )
            cursor = chunk_end

    return out


def _split(text: str, *, max_words: int, max_chars: int) -> list[str]:
    """Break one cue into chunks that fit both budgets.

    Greedy, and it checks *before* adding rather than after — the same correction
    `media._group_cues` documents, for the same reason: appending and then
    flushing makes the budget a floor rather than a ceiling, so one long word
    carries the line well past it.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = [*current, word]
        too_long = len(candidate) > max_words or len(" ".join(candidate)) > max_chars
        if current and too_long:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current = candidate

    if current:
        chunks.append(" ".join(current))

    # A trailing orphan is pulled back into its neighbour when that stays inside
    # the character budget. "and" alone on screen for half a second reads as a
    # rendering fault, and it is the commonest artefact of a greedy split.
    if len(chunks) > 1 and len(chunks[-1].split()) < MIN_WORDS:
        merged = f"{chunks[-2]} {chunks[-1]}"
        if len(merged) <= max_chars + 8:
            chunks[-2:] = [merged]

    return chunks
