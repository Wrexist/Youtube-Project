"""Is the finished video original enough to monetise?

A *reused-content* question, and it has nothing to do with copyright. A licence
buys nothing here — YouTube's rules apply "regardless of whether you have
permission from the original creator", and a video can take zero Content ID claims
and still fail this review. `rights.py` answers the other question. See the package
docstring for why they are separate modules.

**The trap this module exists to avoid.** The obvious implementation scores
"percentage of runtime that is our own footage", and it is wrong in the most
expensive direction: it fails exactly the format the policy explicitly permits. A
reaction video is 100% someone else's footage with commentary over it, and YouTube
names that as monetisable — "filming a reaction with thoughtful commentary",
"editing a compilation with your own narration or analysis". A naive ratio marks
the single best-documented compliant format as a total failure, and the operator
learns to ignore the gate. Once they ignore it, it may as well not exist.

So the measurement is **authorship, not ownership of pixels**. Runtime counts as
authored when it carries something we made — our footage, or someone else's footage
with our narration over it. What is actually dangerous is *bare* source: third-party
footage with nothing added at all, which is the literal description of a reupload.
`bare_source_share` and `longest_bare_run` are the numbers that matter, and they are
the ones a human reviewer is effectively eyeballing.

**On the thresholds.** They are heuristics calibrated to the *language* of the
policy, not to any published algorithm. YouTube documents no numeric bar and any
module claiming otherwise is guessing. They are constants in one place, versioned,
and every report records the version that scored it — because the only way these
ever get better is comparing them against real review outcomes, and that is
impossible if nobody knows which numbers were in force at the time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from engine.repurpose.rights import Grant, RightsProblem

#: Bump when any threshold below changes. Stored on every report so a finding six
#: months from now can be attributed to the rules that produced it.
THRESHOLDS_VERSION = 1

#: Fraction of runtime that must carry original authorship — our footage, or source
#: footage under our narration. Half is not a policy number; it is the point at
#: which "a video with clips in it" stops being "clips with a video around them".
MIN_AUTHORED_SHARE = 0.50

#: Fraction of runtime allowed to be third-party footage with nothing added.
MAX_BARE_SOURCE_SHARE = 0.35

#: The longest single unbroken stretch of bare source, in seconds. This is the
#: strictest check and the most important one: totals can look respectable while a
#: single 40-second lift sits in the middle, and that lift is what a reviewer
#: scrubbing the video will land on. Fifteen seconds is roughly the point at which
#: an excerpt stops reading as a quotation.
MAX_BARE_RUN_S = 15.0

#: Of the runtime that *is* source footage, how much must have narration over it.
MIN_NARRATION_OVER_SOURCE = 0.60

#: Below this many distinct segments, a "compilation" is one clip with a top and a
#: tail — the classic failure the policy calls out as "clips edited together with
#: little or no narrative".
MIN_SEGMENTS_FOR_COMPILATION = 3

#: Seconds between cuts, above which the edit reads as unedited. Doubles as a
#: retention signal — the craft research puts pattern interrupts at every 3–5s.
MAX_SECONDS_PER_CUT = 5.0

#: Cosine-ish similarity to the channel's recent uploads, above which this video is
#: a near-repeat of something already published. The corpus checks are the ones
#: automation walks into by default: no individual video looks bad and the channel
#: dies anyway.
MAX_CORPUS_SIMILARITY = 0.85

#: Consecutive uploads sharing a narration skeleton before it is flagged. Policy
#: names this one directly — "dozens of videos all using the same narration or
#: text". Three is early enough to correct and late enough not to fire on a format.
MAX_TEMPLATE_REPEATS = 3


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    OK = "ok"


@dataclass(frozen=True)
class Signal:
    """One measured property of the finished video, and how it read."""

    name: str
    severity: Severity
    message: str
    #: What was measured, for the UI to render as a number rather than prose.
    value: float | None = None
    threshold: float | None = None

    @property
    def ok(self) -> bool:
        return self.severity is Severity.OK

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity.value,
            "message": self.message,
            "value": round(self.value, 4) if self.value is not None else None,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class TimelineSegment:
    """One stretch of the finished video.

    `narrated` and `annotated` are what turn source footage into authored runtime.
    They are separate because they are separately detectable: narration comes from
    the voiceover track's cue timings, annotation from the overlay compositor.
    """

    start_s: float
    end_s: float
    #: None for footage we made. A source id for third-party footage.
    source_id: str | None = None
    #: Original narration plays over this segment.
    narrated: bool = False
    #: On-screen original text/graphics over this segment.
    annotated: bool = False

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def is_source(self) -> bool:
        return self.source_id is not None

    @property
    def is_bare_source(self) -> bool:
        """Third-party footage with nothing of ours on it. The dangerous kind."""
        return self.is_source and not self.narrated and not self.annotated

    @property
    def is_authored(self) -> bool:
        return not self.is_source or self.narrated or self.annotated


@dataclass(frozen=True)
class Corpus:
    """How this video sits against what the channel already published.

    Defaults describe a channel with no history, which scores clean — a first video
    cannot be repetitive, and flagging it would be noise on the one run where the
    operator is still deciding whether to trust the gate.
    """

    #: Highest similarity to any recent upload, 0..1.
    max_similarity: float = 0.0
    #: Consecutive prior uploads sharing this video's narration skeleton.
    template_repeats: int = 0
    #: Consecutive prior uploads sharing this video's beat structure.
    structure_repeats: int = 0
    #: How many uploads were compared against. Zero means the checks did not run,
    #: which is reported rather than passed off as a clean result.
    compared_against: int = 0


@dataclass(frozen=True)
class Timeline:
    """The assembled video, as the gate needs to see it."""

    segments: tuple[TimelineSegment, ...] = ()
    #: Total cuts in the finished edit.
    cuts: int = 0
    #: Whether the source audio bed was replaced. Non-negotiable: TikTok music
    #: licences do not extend to YouTube, so an unreplaced bed is an unlicensed
    #: use regardless of what the video rights say.
    audio_bed_replaced: bool = False
    #: Source ids whose frames still show a third-party watermark.
    watermarked_sources: tuple[str, ...] = ()
    attribution_on_screen: bool = False
    attribution_in_description: bool = False
    #: Whether this is presented as a compilation, which carries a segment-count
    #: expectation a single-clip commentary piece does not.
    is_compilation: bool = False

    @property
    def duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments)

    @property
    def authored_share(self) -> float:
        total = self.duration_s
        if total <= 0:
            return 0.0
        return sum(s.duration_s for s in self.segments if s.is_authored) / total

    @property
    def bare_source_share(self) -> float:
        total = self.duration_s
        if total <= 0:
            return 0.0
        return sum(s.duration_s for s in self.segments if s.is_bare_source) / total

    @property
    def source_duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments if s.is_source)

    @property
    def narration_over_source(self) -> float:
        """Of the source footage, how much carries our narration.

        Returns 1.0 when there is no source footage at all — vacuously true, and
        the alternative (0.0) would fail an entirely original video for not
        narrating over clips it does not contain.
        """
        source = self.source_duration_s
        if source <= 0:
            return 1.0
        narrated = sum(s.duration_s for s in self.segments if s.is_source and s.narrated)
        return narrated / source

    def longest_bare_run_s(self) -> float:
        """The longest unbroken stretch of bare source.

        Adjacent bare segments are merged, because two 10-second bare segments cut
        back to back are a 20-second lift with a cut in it — the cut changes
        nothing about how it reads. Segments are assumed to be in playback order.
        """
        longest = 0.0
        run = 0.0
        for segment in self.segments:
            if segment.is_bare_source:
                run += segment.duration_s
                longest = max(longest, run)
            else:
                run = 0.0
        return longest

    @property
    def seconds_per_cut(self) -> float:
        """Average seconds between cuts. Infinite for an uncut video."""
        if self.cuts <= 0:
            return float("inf")
        return self.duration_s / self.cuts


@dataclass(frozen=True)
class RightsVerdict:
    """May we use this footage. Not a score — see `rights.problems`."""

    cleared: bool
    problems: dict[str, list[RightsProblem]] = field(default_factory=dict)
    #: Sources with no grant at all, which is a different failure from a bad grant.
    ungranted: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "cleared": self.cleared,
            "ungranted": list(self.ungranted),
            "problems": {
                source_id: [
                    {"code": p.code, "message": p.message, "fatal": p.fatal} for p in problems
                ]
                for source_id, problems in self.problems.items()
            },
        }


@dataclass(frozen=True)
class TransformationVerdict:
    """Is it original enough. Scored, because this one genuinely is a matter of degree."""

    passed: bool
    signals: tuple[Signal, ...]

    @property
    def blocks(self) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.severity is Severity.BLOCK)

    @property
    def warnings(self) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.severity is Severity.WARN)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "signals": [s.as_dict() for s in self.signals],
        }


@dataclass(frozen=True)
class Report:
    """Both verdicts, never blended.

    "Cleared to use, not yet original enough" is a real and common state with a
    completely different fix from its opposite, and a single number hides which one
    you are in.
    """

    rights: RightsVerdict
    transformation: TransformationVerdict
    thresholds_version: int = THRESHOLDS_VERSION

    @property
    def publishable(self) -> bool:
        return self.rights.cleared and self.transformation.passed

    def headline(self) -> str:
        """One sentence for the blocking card. Says which gate, because they differ."""
        if self.publishable:
            return "Cleared to publish."
        if not self.rights.cleared and not self.transformation.passed:
            return "Blocked — rights are not cleared and the edit is not original enough."
        if not self.rights.cleared:
            return "Blocked on rights — the edit itself is fine."
        count = len(self.transformation.blocks)
        return f"Blocked on originality — {count} check{'s' if count != 1 else ''} failed."

    def as_dict(self) -> dict:
        return {
            "publishable": self.publishable,
            "headline": self.headline(),
            "thresholds_version": self.thresholds_version,
            "rights": self.rights.as_dict(),
            "transformation": self.transformation.as_dict(),
        }


def evaluate(
    timeline: Timeline,
    grants: dict[str, Grant],
    *,
    corpus: Corpus | None = None,
    platform: str = "youtube",
) -> Report:
    """Score a finished video against both gates.

    `grants` is keyed by source id. A source appearing in the timeline with no entry
    is `ungranted`, which is fatal and deliberately distinguished from a grant that
    exists and is bad — the first is a wiring bug or a skipped step, the second is a
    lapsed licence, and they are fixed differently.
    """
    corpus = corpus or Corpus()
    return Report(
        rights=_rights_verdict(timeline, grants, platform=platform),
        transformation=_transformation_verdict(timeline, grants, corpus),
    )


def _rights_verdict(
    timeline: Timeline, grants: dict[str, Grant], *, platform: str
) -> RightsVerdict:
    source_ids = {s.source_id for s in timeline.segments if s.source_id}
    problems: dict[str, list[RightsProblem]] = {}
    ungranted: list[str] = []

    for source_id in sorted(source_ids):
        grant = grants.get(source_id)
        if grant is None:
            ungranted.append(source_id)
            continue
        found = grant.problems(platform=platform)
        if found:
            problems[source_id] = found

    cleared = not ungranted and not any(p.fatal for found in problems.values() for p in found)
    return RightsVerdict(cleared=cleared, problems=problems, ungranted=tuple(ungranted))


def _transformation_verdict(
    timeline: Timeline, grants: dict[str, Grant], corpus: Corpus
) -> TransformationVerdict:
    signals: list[Signal] = [
        *_hard_signals(timeline, grants),
        *_scored_signals(timeline),
        *_corpus_signals(corpus),
    ]
    passed = not any(s.severity is Severity.BLOCK for s in signals)
    return TransformationVerdict(passed=passed, signals=tuple(signals))


def _hard_signals(timeline: Timeline, grants: dict[str, Grant]) -> list[Signal]:
    """Checks with no scale: they are satisfied or the video does not go out."""
    out: list[Signal] = []

    # Watermarks. Independently disqualifying for Shorts monetisation, separately
    # from any copyright question — and note the ordering in this module: rights
    # are established first, so removing a watermark can never read as a solution
    # to not having permission.
    if timeline.watermarked_sources:
        names = ", ".join(timeline.watermarked_sources)
        out.append(
            Signal(
                "watermark",
                Severity.BLOCK,
                f"third-party watermark still visible on {names}. Source a clean master "
                "— cropping it out is not the fix, and re-framing to hide it usually "
                "wrecks the composition.",
            )
        )
    else:
        out.append(Signal("watermark", Severity.OK, "no third-party watermarks"))

    # Audio. The single likeliest cause of a claim: TikTok's music licences cover
    # TikTok and nothing else, so an unreplaced bed is unlicensed on YouTube no
    # matter how solid the video rights are.
    if timeline.source_duration_s > 0 and not timeline.audio_bed_replaced:
        out.append(
            Signal(
                "audio_bed",
                Severity.BLOCK,
                "the source audio bed was not replaced. TikTok music licences do not "
                "extend to YouTube, so this will draw a Content ID claim regardless of "
                "the video rights.",
            )
        )
    else:
        out.append(Signal("audio_bed", Severity.OK, "audio bed replaced"))

    # Attribution, where the lane requires it.
    needs = sorted(
        source_id
        for source_id, grant in grants.items()
        if grant.needs_attribution
        and source_id in {s.source_id for s in timeline.segments if s.source_id}
    )
    if needs and not (timeline.attribution_on_screen and timeline.attribution_in_description):
        missing = []
        if not timeline.attribution_on_screen:
            missing.append("on screen")
        if not timeline.attribution_in_description:
            missing.append("in the description")
        out.append(
            Signal(
                "attribution",
                Severity.BLOCK,
                f"credit is missing {' and '.join(missing)} for {', '.join(needs)}",
            )
        )
    elif needs:
        out.append(Signal("attribution", Severity.OK, "source credited on screen and in text"))

    return out


def _scored_signals(timeline: Timeline) -> list[Signal]:
    """The measurements a reviewer is effectively making by eye."""
    out: list[Signal] = []

    if timeline.duration_s <= 0:
        return [Signal("empty", Severity.BLOCK, "the timeline is empty")]

    authored = timeline.authored_share
    out.append(
        Signal(
            "authored_share",
            Severity.OK if authored >= MIN_AUTHORED_SHARE else Severity.BLOCK,
            f"{authored:.0%} of the runtime carries original narration, annotation or footage"
            + ("" if authored >= MIN_AUTHORED_SHARE else f" — needs {MIN_AUTHORED_SHARE:.0%}"),
            value=authored,
            threshold=MIN_AUTHORED_SHARE,
        )
    )

    bare = timeline.bare_source_share
    out.append(
        Signal(
            "bare_source_share",
            Severity.OK if bare <= MAX_BARE_SOURCE_SHARE else Severity.BLOCK,
            f"{bare:.0%} of the runtime is source footage with nothing added"
            + ("" if bare <= MAX_BARE_SOURCE_SHARE else f" — allowed {MAX_BARE_SOURCE_SHARE:.0%}"),
            value=bare,
            threshold=MAX_BARE_SOURCE_SHARE,
        )
    )

    run = timeline.longest_bare_run_s()
    out.append(
        Signal(
            "longest_bare_run",
            Severity.OK if run <= MAX_BARE_RUN_S else Severity.BLOCK,
            f"longest unbroken lift is {run:.0f}s"
            + (
                ""
                if run <= MAX_BARE_RUN_S
                else f" — over {MAX_BARE_RUN_S:.0f}s reads as a reupload however good "
                "the totals look"
            ),
            value=run,
            threshold=MAX_BARE_RUN_S,
        )
    )

    if timeline.source_duration_s > 0:
        coverage = timeline.narration_over_source
        out.append(
            Signal(
                "narration_over_source",
                Severity.OK if coverage >= MIN_NARRATION_OVER_SOURCE else Severity.WARN,
                f"narration covers {coverage:.0%} of the source footage"
                + (
                    ""
                    if coverage >= MIN_NARRATION_OVER_SOURCE
                    else " — commentary should play *over* the clips, not around them"
                ),
                value=coverage,
                threshold=MIN_NARRATION_OVER_SOURCE,
            )
        )

    if timeline.is_compilation:
        count = len({s.source_id for s in timeline.segments if s.source_id})
        out.append(
            Signal(
                "segment_count",
                Severity.OK if count >= MIN_SEGMENTS_FOR_COMPILATION else Severity.WARN,
                f"{count} distinct source clips"
                + (
                    ""
                    if count >= MIN_SEGMENTS_FOR_COMPILATION
                    else " — one clip with a top and a tail is not a compilation"
                ),
                value=float(count),
                threshold=float(MIN_SEGMENTS_FOR_COMPILATION),
            )
        )

    spc = timeline.seconds_per_cut
    out.append(
        Signal(
            "cut_density",
            Severity.OK if spc <= MAX_SECONDS_PER_CUT else Severity.WARN,
            (
                f"a cut every {spc:.1f}s"
                if spc != float("inf")
                else "no cuts at all — this is a straight playthrough"
            )
            + ("" if spc <= MAX_SECONDS_PER_CUT else " — retention wants one every 3–5s"),
            value=None if spc == float("inf") else spc,
            threshold=MAX_SECONDS_PER_CUT,
        )
    )

    return out


def _corpus_signals(corpus: Corpus) -> list[Signal]:
    """The channel-level checks — the failure mode automation walks into by default.

    No individual video looks bad here. That is exactly the point: "dozens of videos
    all using the same narration or text" is named by the policy, and it is invisible
    from inside any one of them.
    """
    if corpus.compared_against <= 0:
        return [
            Signal(
                "corpus",
                Severity.WARN,
                "no published history to compare against — the repetition checks did "
                "not run, which is not the same as passing them",
            )
        ]

    out: list[Signal] = []

    out.append(
        Signal(
            "corpus_similarity",
            Severity.OK if corpus.max_similarity <= MAX_CORPUS_SIMILARITY else Severity.BLOCK,
            f"closest match among your last {corpus.compared_against} uploads scores "
            f"{corpus.max_similarity:.0%}"
            + (
                ""
                if corpus.max_similarity <= MAX_CORPUS_SIMILARITY
                else " — this is a near-repeat of something already published"
            ),
            value=corpus.max_similarity,
            threshold=MAX_CORPUS_SIMILARITY,
        )
    )

    if corpus.template_repeats >= MAX_TEMPLATE_REPEATS:
        out.append(
            Signal(
                "narration_template",
                Severity.BLOCK,
                f"{corpus.template_repeats} consecutive uploads share this narration "
                "skeleton. Policy names this one directly — vary the script structure, "
                "not just the clips.",
                value=float(corpus.template_repeats),
                threshold=float(MAX_TEMPLATE_REPEATS),
            )
        )
    else:
        out.append(
            Signal("narration_template", Severity.OK, "narration structure varies across uploads")
        )

    if corpus.structure_repeats >= MAX_TEMPLATE_REPEATS:
        out.append(
            Signal(
                "structure",
                Severity.WARN,
                f"{corpus.structure_repeats} consecutive uploads share a beat structure "
                "— different clips in the same skeleton is still a template",
                value=float(corpus.structure_repeats),
                threshold=float(MAX_TEMPLATE_REPEATS),
            )
        )

    return out
