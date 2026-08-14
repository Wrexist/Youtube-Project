"""Which stretch of a clip to use, and which half-second earns the first frame.

Two problems, deliberately solved separately, because only the second decides
whether the video is watched at all. Viewers settle in 1.5–3 seconds: a segment
whose payoff lands twenty seconds in needs that payoff *teased at the front*, not
played in order.

**The trap, inherited from `shorts.py` and worth restating.** Any signal that
decays over a clip will pick the opening every time and produce a "best moment"
feature that has not looked at the clip at all. `shorts.py` hits this with
`audienceWatchRatio`, which is measured against the whole audience and falls
monotonically. Here the decaying signal is different but the failure is identical:
short-form clips are front-loaded by construction, so raw audio energy peaks in
the first seconds of nearly every one.

So everything scores against a **detrended** signal — the series minus its own
rolling mean — exactly as `shorts.detrend` does, and for exactly the same reason.
What marks a moment worth using is a *local* rise against the clip's own trend.

**What the signals are.** Retention data does not exist for a clip somebody else
posted, so the inputs are things measurable from the file itself:

  * **energy** — RMS audio level per window. Where something is happening.
  * **speech** — how continuously voiced the window is. A loud music sting and a
    person landing a point are both energetic; only one carries a sentence.
  * **motion** — frame-to-frame difference. Cuts, gestures, action.

Taken as plain sequences rather than a file path, so the arithmetic is testable
without media — the same reason `monetisation.progress` takes mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

#: Fraction of the clip's length spanned by the detrending baseline. Wide enough
#: that a genuine peak does not lift its own baseline and erase itself, narrow
#: enough to still track the overall shape. Matches `shorts.BASELINE_SPAN`.
BASELINE_SPAN = 0.25

#: How the three signals combine. Speech dominates because the thing being looked
#: for is a moment that *says* something — an edit built from the loudest windows
#: is a music video, not a clip with a point.
WEIGHTS = {"speech": 0.5, "energy": 0.3, "motion": 0.2}

#: A usable segment's bounds. Below the floor there is nothing to narrate over;
#: above the ceiling it stops being an excerpt — and `gate.MAX_BARE_RUN_S` would
#: refuse an unbroken lift past 15s anyway, so a 90-second segment only passes if
#: it is narrated throughout.
MIN_SEGMENT_S = 4.0
MAX_SEGMENT_S = 45.0

#: The opening window a hook is chosen from, and how long a hook runs.
HOOK_S = 2.5

#: Lift below which the "best moment" is indistinguishable from the clip's own
#: wobble. Reported as no confident pick rather than an arbitrary one — saying so
#: beats ranking three windows that differ by noise.
MIN_LIFT = 0.4


@dataclass(frozen=True)
class Segment:
    """A proposed in/out point, with the numbers that chose it."""

    start_s: float
    end_s: float
    lift: float
    reason: str

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def as_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 2),
            "end_s": round(self.end_s, 2),
            "duration_s": round(self.duration_s, 2),
            "lift": round(self.lift, 4),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Hook:
    """The moment that earns the first frame.

    `teased` is the point. When the strongest moment is not at the start, the edit
    should open on it and then return — playing the clip in order buries the
    payoff behind twenty seconds a viewer will not wait through.
    """

    at_s: float
    duration_s: float
    teased: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "at_s": round(self.at_s, 2),
            "duration_s": round(self.duration_s, 2),
            "teased": self.teased,
            "reason": self.reason,
        }


def detrend(series: list[float]) -> list[float]:
    """The series minus its own rolling mean.

    Lifted from `shorts.detrend`, including the symmetric window shrink at the
    edges: a one-sided window on a falling series averages in only the lower
    values ahead of it, so the baseline sits below the curve and the opening shows
    a residual it has not earned — which is the every-clip-starts-at-zero bug
    re-entering through the baseline after detrending was meant to remove it.
    """
    if len(series) < 3:
        return [0.0] * len(series)

    half = max(1, int(len(series) * BASELINE_SPAN / 2))
    out: list[float] = []
    for i, value in enumerate(series):
        h = min(half, i, len(series) - 1 - i)
        out.append(value - fmean(series[i - h : i + h + 1]))
    return out


def _normalise(series: list[float]) -> list[float]:
    """Scale to 0..1 so three signals in different units can be combined."""
    if not series:
        return []
    low, high = min(series), max(series)
    if high - low < 1e-9:
        return [0.0] * len(series)
    return [(v - low) / (high - low) for v in series]


def _padded(
    energy: list[float], speech: list[float], motion: list[float], length: int
) -> list[float]:
    """The three signals combined at their raw level — normalised, not detrended.

    Used only by the tease decision in `choose_hook`, which needs to know how loud
    the opening actually is rather than how much it rises against trend. See the
    note there.
    """

    def prepare(series: list[float]) -> list[float]:
        if not series:
            return [0.0] * length
        padded = list(series) + [series[-1]] * (length - len(series))
        return _normalise(padded[:length])

    parts = {
        "energy": prepare(energy),
        "speech": prepare(speech),
        "motion": prepare(motion),
    }
    return [sum(WEIGHTS[name] * parts[name][i] for name in WEIGHTS) for i in range(length)]


def _combined(energy: list[float], speech: list[float], motion: list[float]) -> list[float]:
    """One detrended interest series from the three inputs.

    Each is normalised and detrended *independently* before combining. Detrending
    the sum instead would let a signal with a large dynamic range dominate the
    baseline of the others.
    """
    length = max(len(energy), len(speech), len(motion))
    if not length:
        return []

    def prepare(series: list[float]) -> list[float]:
        if not series:
            return [0.0] * length
        padded = list(series) + [series[-1]] * (length - len(series))
        return detrend(_normalise(padded[:length]))

    parts = {
        "energy": prepare(energy),
        "speech": prepare(speech),
        "motion": prepare(motion),
    }
    return [sum(WEIGHTS[name] * parts[name][i] for name in WEIGHTS) for i in range(length)]


def choose_segment(
    *,
    energy: list[float],
    speech: list[float] | None = None,
    motion: list[float] | None = None,
    duration_s: float,
    window_s: float = 1.0,
    target_s: float = 20.0,
) -> Segment | None:
    """The stretch worth using, or None when nothing stands out.

    `window_s` is the sampling interval of the input series. `target_s` is the
    length aimed for; the returned segment is clamped into
    `MIN_SEGMENT_S..MAX_SEGMENT_S` and to the clip's own bounds.

    Returns None rather than a shrug. A clip with no local rise is a clip where
    the honest answer is "use it whole or not at all", and inventing a best moment
    is what this module exists to avoid.
    """
    if duration_s <= 0 or not energy:
        return None

    interest = _combined(energy, speech or [], motion or [])
    if not interest:
        return None

    span = max(1, int(round(min(max(target_s, MIN_SEGMENT_S), MAX_SEGMENT_S) / window_s)))
    if span >= len(interest):
        # Shorter than one target window — the whole clip is the segment.
        return Segment(
            start_s=0.0,
            end_s=duration_s,
            lift=0.0,
            reason="clip is shorter than the target segment — using it whole",
        )

    # The typical positive deviation, as the unit lift is measured in. Same device
    # as `shorts.find_candidates`: a fixed threshold means nothing across clips
    # with different dynamics.
    positives = [v for v in interest if v > 0]
    noise = fmean(positives) if positives else 0.0
    if noise <= 0:
        return None

    best_start, best_mean = 0, float("-inf")
    for start in range(len(interest) - span + 1):
        mean = fmean(interest[start : start + span])
        if mean > best_mean:
            best_start, best_mean = start, mean

    lift = best_mean / noise
    if lift < MIN_LIFT:
        return None

    start_s = min(best_start * window_s, max(0.0, duration_s - MIN_SEGMENT_S))
    end_s = min(start_s + span * window_s, duration_s)
    if end_s - start_s < MIN_SEGMENT_S:
        return None

    return Segment(
        start_s=start_s,
        end_s=end_s,
        lift=lift,
        reason=(
            f"{lift:.1f}× the clip's typical rise — speech and motion both climb here"
            if lift >= 1.0
            else f"a modest rise ({lift:.1f}×) against the clip's own trend"
        ),
    )


#: Fraction of the peak's raw level a window must still hold to count as part of
#: the same stretch. Loose, because the walk only needs to find where the event
#: began, not to trace its exact shape.
_ONSET_FLOOR = 0.8


def _onset(raw: list[float], peak: int) -> int:
    """Where the stretch containing `peak` begins, walked on the **raw** series.

    Raw rather than detrended, deliberately — see the note in `choose_hook`. A
    plateau running from the first frame walks back to zero here, which is the
    answer that stops a clip being told to tease its own opening; an isolated
    event partway through stops at its own leading edge, because the quieter
    material before it falls below the floor.
    """
    if not raw or raw[peak] <= 0:
        return peak
    threshold = raw[peak] * _ONSET_FLOOR
    start = peak
    while start > 0 and raw[start - 1] >= threshold:
        start -= 1
    return start


def choose_hook(
    *,
    energy: list[float],
    speech: list[float] | None = None,
    motion: list[float] | None = None,
    duration_s: float,
    window_s: float = 1.0,
) -> Hook | None:
    """The half-second that earns the first frame.

    A *separate* decision from `choose_segment`, and the one that decides whether
    the clip is watched: viewers settle in 1.5–3 seconds. When the strongest
    moment is not already at the front, `teased` says the edit should open on it
    and then return — playing in order buries the payoff behind seconds nobody
    waits through.
    """
    if duration_s <= 0 or not energy:
        return None

    interest = _combined(energy, speech or [], motion or [])
    if not interest:
        return None

    # Two series, two jobs, and getting this split wrong is the subtle failure in
    # this module:
    #
    #   * the **detrended** series locates the event — where the clip rises
    #     against its own trend;
    #   * the **raw** series says how far back that event *extends*.
    #
    # Neither alone works. Detrended cannot find the onset of a *leading* strong
    # stretch: its baseline window sits entirely inside the plateau, so residual
    # there is ~0 by construction, and a clip that opens on eight loud seconds
    # reports its peak at second seven — "open on it, then cut back" about a clip
    # already doing that, cutting away from the hook and back to it.
    #
    # Raw alone is worse: short-form clips are front-loaded by construction, so
    # raw level almost always peaks in the first seconds. Deciding on raw level
    # reintroduces exactly the bias the detrending exists to remove.
    raw = _normalise(_padded(energy, speech or [], motion or [], len(interest)))
    peak = max(range(len(interest)), key=lambda i: interest[i])
    onset = _onset(raw, peak)
    at_s = min(onset * window_s, max(0.0, duration_s - HOOK_S))

    # A strong stretch beginning inside the hook's own length *is* the opening.
    teased = at_s > HOOK_S

    return Hook(
        at_s=at_s,
        duration_s=min(HOOK_S, max(0.0, duration_s - at_s)),
        teased=teased,
        reason=(
            f"strongest moment is {at_s:.0f}s in — open on it, then cut back"
            if teased
            else "the clip already opens on its strongest moment"
        ),
    )
