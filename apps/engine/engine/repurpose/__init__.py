"""Repurposing third-party short-form video into publishable YouTube content.

Two lanes, two very different jobs — see `docs/REPURPOSE-PLAN.md`:

  * **Lane A (own)** builds the channel. Your own TikToks, no clearance path, clean
    masters. This is where the edit quality gets proven, because nothing here can
    go wrong legally.
  * **Lane B (campaign)** earns. A creator funds a clipping campaign, sets content
    rules, and pays per verified view. Enrolment answers the rights question that
    Lane C spends days negotiating, and a Content ID claim costs nothing because
    the money was never ad revenue.

**The one thing to understand before reading any of this code.** Two independent
gates stand between a clip and a published video, and passing one says nothing
about the other:

  * `rights.py` — may we use this footage at all? A copyright question, answered by
    a licence, an enrolment, or ownership.
  * `gate.py` — is the result original enough to monetise? A *reused-content*
    question, answered only by what was added.

YouTube's reused-content rules apply "regardless of whether you have permission
from the original creator" — collections of songs from different artists are
unmonetisable even with every artist's blessing. And the converse holds: a video
can take zero copyright claims and still fail review. So a licence buys exactly
nothing in `gate.py`, and a beautiful edit buys exactly nothing in `rights.py`.
Keeping them in separate modules is the cheapest way to stop one being mistaken
for the other, which an earlier draft of the plan did.
"""

from __future__ import annotations

from engine.repurpose.gate import (
    Report,
    RightsVerdict,
    Timeline,
    TimelineSegment,
    TransformationVerdict,
    evaluate,
)
from engine.repurpose.rights import Grant, Lane, RightsProblem

__all__ = [
    "Grant",
    "Lane",
    "Report",
    "RightsProblem",
    "RightsVerdict",
    "Timeline",
    "TimelineSegment",
    "TransformationVerdict",
    "evaluate",
]
