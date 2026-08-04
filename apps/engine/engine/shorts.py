"""Pick the moments in a long-form video worth cutting into a Short.

The input is the retention curve already pulled for the retention map
(`Analytics.retention`) plus the script beats that produced the video, so this
costs nothing extra — it is a second reading of data the system already has.

**The trap this module exists to avoid.** `audienceWatchRatio` is measured against
the video's whole audience, so it decays: at 10% through the video more people are
still watching than at 80%, essentially always. Scoring segments by raw retention
therefore picks the opening every single time, for every video, and produces a
"best moment" feature that has not looked at the video at all. What actually marks
a moment worth extracting is a *local* rise against that decay — a spot where the
curve flattens or ticks up while the trend says it should be falling. That is a
rewatch, and a rewatch is the closest thing the API gives to "people cared about
this bit".

So everything here scores against a detrended curve. The baseline is a wide rolling
mean; the residual above it is the signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from engine.insights import as_beats

# YouTube accepts Shorts up to three minutes, but the format's own conventions —
# and every study of them — sit far below that. Under 15s reads as a fragment
# rather than a clip; over 60s stops being a Short in anything but eligibility.
MIN_SECONDS = 15.0
MAX_SECONDS = 60.0

#: Fraction of the video's runtime that the rolling baseline spans. Wide enough
#: that a genuine rewatch spike does not lift its own baseline and erase itself,
#: narrow enough to still track the overall decay.
BASELINE_SPAN = 0.25

#: The closing stretch is excluded from candidates. Retention there looks strong
#: for a reason that does not transfer: the people still present at the outro are
#: the ones who were always going to finish, and a call-to-action makes a
#: uniquely bad standalone clip.
OUTRO_FRACTION = 0.10

#: How far above the decay baseline a window must sit before it is worth offering,
#: measured in units of the video's own typical wobble (see `noise` in
#: `find_candidates`). A window sitting at exactly the typical positive deviation
#: scores 1.0, so this asks for half of that, sustained across the whole window.
#: Below it the "best moment" is noise, and saying so beats ranking three arbitrary
#: windows.
MIN_LIFT = 0.5


@dataclass(frozen=True)
class Candidate:
    """One proposed cut, with the numbers that chose it."""

    start_s: float
    end_s: float
    label: str
    lift: float
    """Mean rise above the decay baseline across the window, in units of the video's
    own typical wobble. This is the term that does the work — see the module
    docstring."""
    hold: float
    """Retention at the end of the window over retention at its start. Below 1.0
    the window bleeds viewers as it plays, which a clip cannot afford."""
    score: float
    reason: str

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def as_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 2),
            "end_s": round(self.end_s, 2),
            "duration_s": round(self.duration_s, 2),
            "label": self.label,
            "lift": round(self.lift, 4),
            "hold": round(self.hold, 4),
            "score": round(self.score, 4),
            "reason": self.reason,
        }


def _sample(curve: list[float], pct: float) -> float:
    """Linear interpolation into a retention curve at a fractional position.

    Interpolated rather than indexed for the same reason the retention map does it:
    the curve is sampled at fixed intervals that no beat boundary lines up with, and
    indexing collapses any window shorter than one sample to a single value.
    """
    if not curve:
        return 0.0
    pct = min(max(pct, 0.0), 1.0)
    position = pct * (len(curve) - 1)
    low = int(position)
    if low >= len(curve) - 1:
        return curve[-1]
    fraction = position - low
    return curve[low] + (curve[low + 1] - curve[low]) * fraction


def detrend(curve: list[float]) -> list[float]:
    """The curve minus its own rolling mean — what is left after the decay.

    Returned in the curve's own units. Positive means the video held more attention
    at that point than the surrounding stretch would predict.
    """
    if len(curve) < 3:
        return [0.0] * len(curve)

    half = max(1, int(len(curve) * BASELINE_SPAN / 2))
    out: list[float] = []
    for i, value in enumerate(curve):
        # The window shrinks symmetrically near the ends rather than being clipped
        # to one side. A one-sided window on a falling curve averages in only the
        # lower values ahead of it, so the baseline sits below the curve and the
        # opening seconds show a residual they have not earned — which is the
        # every-Short-comes-from-the-intro bug, re-entering through the baseline
        # after the detrending was supposed to have removed it.
        h = min(half, i, len(curve) - 1 - i)
        out.append(value - fmean(curve[i - h : i + h + 1]))
    return out


def _windows(beats: list, duration_s: float) -> list[tuple[float, float, str]]:
    """Candidate (start, end, label) spans, aligned to beats wherever possible.

    Beats are used as the cut points because a beat is a complete thought by
    construction — it is the unit the script was written in. A clip that starts
    mid-beat starts mid-sentence.

    Runs of consecutive beats are emitted, not just single beats, because the
    beat that lands a point and the beat that sets it up are often both needed for
    the clip to stand alone.
    """
    if duration_s <= 0:
        return []

    # Beat `est_seconds` are the script's estimates and rarely sum to the finished
    # runtime, so they are rescaled onto the real duration rather than trusted.
    normalised = as_beats(beats)
    weights = [max(b.est_seconds, 0.5) for b in normalised]
    total = sum(weights)
    if total <= 0:
        return []
    scale = duration_s / total

    bounds: list[tuple[float, float]] = []
    cursor = 0.0
    for weight in weights:
        end = cursor + weight * scale
        bounds.append((cursor, end))
        cursor = end

    out: list[tuple[float, float, str]] = []
    for i in range(len(beats)):
        for j in range(i, len(beats)):
            start, end = bounds[i][0], bounds[j][1]
            length = end - start
            if length > MAX_SECONDS:
                break  # every longer run from i is also too long
            if length >= MIN_SECONDS:
                label = normalised[i].purpose[:60]
                out.append((start, end, label))

    # A single beat longer than MAX_SECONDS produces no run at all, and on a video
    # written in three long beats that means no candidates whatsoever. Slide a
    # max-length window through any such beat so it can still be drawn from.
    for (start, end), beat in zip(bounds, normalised, strict=True):
        if end - start <= MAX_SECONDS:
            continue
        label = beat.purpose[:60]
        step = MAX_SECONDS / 2
        cursor = start
        last_start: float | None = None
        while cursor + MAX_SECONDS <= end:
            out.append((cursor, cursor + MAX_SECONDS, label))
            last_start = cursor
            cursor += step
        # The stride leaves a tail: a beat from 30s to 130s emitted 30–90 and
        # 60–120, so a rewatch at 125s could not be scored at all. Anchor one window
        # to the end when the stride did not already land there.
        tail = end - MAX_SECONDS
        if last_start is None or abs(last_start - tail) > 1e-9:
            out.append((tail, end, label))

    return out


def _score_window(
    curve: list[float],
    residuals: list[float],
    noise: float,
    start_s: float,
    end_s: float,
    duration_s: float,
) -> tuple[float, float, float]:
    """Return (lift, hold, score) for one window."""
    start_pct = start_s / duration_s
    end_pct = end_s / duration_s

    # Average the residual across the window rather than taking its peak: a single
    # spiking sample is as likely to be a stutter in the data as a rewatch, and a
    # clip is only as good as the whole of it.
    samples = 12
    residual_mean = fmean(
        _sample(residuals, start_pct + (end_pct - start_pct) * i / (samples - 1))
        for i in range(samples)
    )
    lift = residual_mean / noise if noise > 0 else 0.0

    start_value = _sample(curve, start_pct)
    end_value = _sample(curve, end_pct)
    hold = end_value / start_value if start_value > 0 else 0.0

    # `hold` discounts rather than adds, and is capped at 1.0. Holding every viewer
    # is the ceiling of what the term measures; letting a window score above it
    # rewards a curve that rises mid-clip, which is a rewatch artefact of the
    # original video and not something the cut carries with it.
    score = lift * min(hold, 1.0)
    return lift, hold, score


def _overlaps(a: Candidate, b: Candidate) -> bool:
    """Whether two candidates share more than a third of the shorter one.

    Neighbouring windows score almost identically, so without this the top three
    are three near-identical cuts of the same twenty seconds — technically the
    three best, useless as a set of choices.
    """
    overlap = min(a.end_s, b.end_s) - max(a.start_s, b.start_s)
    if overlap <= 0:
        return False
    return overlap > min(a.duration_s, b.duration_s) / 3


def find_candidates(
    curve: list[float],
    beats: list,
    duration_s: float,
    *,
    count: int = 3,
) -> list[Candidate]:
    """Rank the moments in a video worth cutting into a Short.

    Returns at most `count` non-overlapping candidates, best first, or an empty list
    when nothing in the video rises above its own decay by enough to be worth
    offering. Empty is a real answer here: a video with a flat curve has no
    standout moment, and inventing three is how a feature stops being believed.
    """
    if not curve or not beats or duration_s <= 0:
        return []
    if count <= 0:
        # Checked up front. The loop below tests `len(picked) == count` only *after*
        # appending, so a zero or negative count returned every non-overlapping
        # candidate — the parameter meant the opposite of a limit.
        return []

    spread = max(curve) - min(curve)
    if spread <= 0:
        return []  # a perfectly flat curve has no best moment, only arbitrary ones

    residuals = detrend(curve)

    # The scale `lift` is measured in: the video's own typical wobble around its
    # decay. Dividing by this rather than by the curve's range is what makes the
    # threshold a constant — a video that decays 90→80 and one that decays 90→20
    # both have their own noise floor, and a rewatch is a rewatch in either.
    noise = fmean(abs(r) for r in residuals)
    if noise <= spread * 1e-3:
        # A curve that is exactly its own trend has no local structure at all —
        # nothing rises above anything. Bail rather than divide by a rounding error
        # and hand back three windows ranked by floating-point dust.
        return []

    cutoff_s = duration_s * (1 - OUTRO_FRACTION)

    scored: list[Candidate] = []
    for start_s, end_s, label in _windows(beats, duration_s):
        if end_s > cutoff_s:
            continue
        lift, hold, score = _score_window(curve, residuals, noise, start_s, end_s, duration_s)
        if lift < MIN_LIFT:
            continue
        scored.append(
            Candidate(
                start_s=start_s,
                end_s=end_s,
                label=label,
                lift=lift,
                hold=hold,
                score=score,
                reason=_reason(lift, hold, start_s, duration_s),
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)

    picked: list[Candidate] = []
    for candidate in scored:
        if any(_overlaps(candidate, chosen) for chosen in picked):
            continue
        picked.append(candidate)
        if len(picked) == count:
            break
    return picked


def _reason(lift: float, hold: float, start_s: float, duration_s: float) -> str:
    """Why this window was picked, in words, for the UI to show verbatim.

    A ranked list with no stated reason is a list the user has to take on faith,
    and the whole argument for retention-derived cuts is that the reason is
    checkable.
    """
    where = round(start_s / duration_s * 100)
    strength = "well above" if lift >= 1.0 else "above"
    sentence = f"Retention runs {strength} the surrounding stretch, {where}% in"
    if hold >= 0.98:
        return f"{sentence}, and holds steady the whole way through."
    if hold >= 0.9:
        return f"{sentence}, with only a slight drop across it."
    return f"{sentence}, though it does lose viewers towards the end."
