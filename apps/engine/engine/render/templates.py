"""Thumbnail archetypes.

Every thumbnail used to be composed the same way: white words down the left, image
on the right. Three "variants" per video were three different pictures in one
identical frame, which is not a choice — it is the same thumbnail three times.

These are the layouts that top channels actually run, encoded as (a) direction for
the image model and (b) a real layout in `compose`. The concept stage picks one per
variant and is required to pick three different ones, so the picker offers a genuine
A/B set rather than a palette swap.

**On MrBeast.** His thumbnails are built on his own face at maximum expression —
that is the load-bearing element, and this project is faceless by design (see
PLAN.md). Copying it literally is not available. What ports is the machinery
underneath, which is what actually does the work: one idea readable in a fifth of a
second at 168 pixels wide, stakes made physically visible, saturation and contrast
far past what looks tasteful at full size, big numerals, and deliberate negative
space for type. Those are the rules below.

The hard limits every template obeys: 1280x720, under YouTube's 2MB ceiling, nothing
important in the bottom-right ~15% where the duration badge sits, and legible at
168px because that is the size the decision is actually made at.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Saturated enough to survive YouTube's compression and a bright feed. Deliberately
# not the app's own accent — a thumbnail competes in someone else's UI, not ours.
ACCENTS: dict[str, tuple[int, int, int]] = {
    "amber": (255, 186, 8),
    "red": (232, 43, 47),
    "cyan": (34, 211, 238),
    "lime": (163, 230, 53),
    "magenta": (236, 72, 153),
    "white": (255, 255, 255),
}
DEFAULT_ACCENT = "amber"


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    #: Shown to the model choosing a template. Written as "use this when…" because a
    #: list of names alone gets picked from at random.
    when: str
    #: Appended to the concept's image prompt. Carries the composition requirement —
    #: mainly *where the negative space has to be* so the type has somewhere to live.
    image_direction: str
    #: How `compose` draws the type.
    layout: str
    max_words: int = 4
    accent: str = DEFAULT_ACCENT
    #: Templates whose text is a quantity need the number kept separate so it can be
    #: set enormous while the words around it stay small.
    wants_numeral: bool = False
    examples: list[str] = field(default_factory=list)


TEMPLATES: dict[str, Template] = {
    "stakes": Template(
        key="stakes",
        label="Stakes",
        when=(
            "the video has a consequence, a risk, or an outcome that can be shown "
            "physically. The default for challenge, experiment and 'what happens if' "
            "videos. Strongest archetype there is — use it unless another fits better."
        ),
        image_direction=(
            "Dramatic, high-stakes moment at peak tension. One unmistakable subject, "
            "saturated colour, hard directional light, shallow depth of field. Leave "
            "the left third visually simple as negative space. Cinematic, not stock."
        ),
        layout="left_column",
        max_words=3,
        accent="amber",
        examples=["IT GETS WORSE", "TOO LATE", "IT COLLAPSED"],
    ),
    "numeral": Template(
        key="numeral",
        label="Big number",
        when=(
            "the video's hook is a quantity — an amount of money, a count, a duration, "
            "a rank. The number does the work and must dominate the frame."
        ),
        image_direction=(
            "A single striking subject with generous clean space on the right half for "
            "a very large number to sit. High contrast, saturated, uncluttered "
            "background. No text or digits anywhere in the image."
        ),
        layout="numeral",
        max_words=4,
        accent="lime",
        examples=["7 DAYS", "$0", "100 HOURS"],
    ),
    "versus": Template(
        key="versus",
        label="Versus",
        when=(
            "the video compares two things, or sets an expectation against a reality. "
            "Comparison, myth-busting and 'X vs Y' videos."
        ),
        image_direction=(
            "A split composition: two clearly distinct subjects, one on each side of "
            "the frame, visually contrasting in colour and lighting. Centre kept simple "
            "for a divider. Equal visual weight on both sides."
        ),
        layout="versus",
        max_words=4,
        accent="cyan",
        examples=["CHEAP VS REAL", "MYTH VS FACT"],
    ),
    "transformation": Template(
        key="transformation",
        label="Before / after",
        when=(
            "the video shows a change over time — a build, a repair, a process, a "
            "decline. Anything where the payoff is the difference between two states."
        ),
        image_direction=(
            "One subject shown mid-transformation, or a scene with a clear ruined-to-"
            "restored contrast across the frame. Strong colour separation between the "
            "two halves. Keep the lower third calm for a caption bar."
        ),
        layout="banner",
        max_words=4,
        accent="magenta",
        examples=["FROM THIS TO THIS", "I FIXED IT"],
    ),
    "revelation": Template(
        key="revelation",
        label="Revelation",
        when=(
            "the video explains a hidden mechanism or answers a question the viewer "
            "did not know they had. Explainers, history, science, 'why does X'."
        ),
        image_direction=(
            "One object or place, centred and spotlit against a dark, simple "
            "background, as if just uncovered. Strong vignette, dramatic single light "
            "source, deep shadows. Keep the lower third dark."
        ),
        layout="centre_stage",
        max_words=4,
        accent="white",
        examples=["NOBODY NOTICED", "HERE IS WHY"],
    ),
}

FALLBACK = "stakes"


def get(key: str | None) -> Template:
    """A template by key, falling back rather than failing.

    A model that invents a template name must not lose the thumbnail — the layout is
    a presentation choice, and the concept it came with is still usable.
    """
    if key and key in TEMPLATES:
        return TEMPLATES[key]
    return TEMPLATES[FALLBACK]


def catalogue_for_prompt() -> str:
    """The archetype list as the concept stage sees it."""
    lines = []
    for template in TEMPLATES.values():
        examples = ", ".join(f'"{e}"' for e in template.examples)
        lines.append(
            f"- {template.key} ({template.label}): {template.when} "
            f"Overlay text: at most {template.max_words} words. Examples: {examples}"
        )
    return "\n".join(lines)


def accent_rgb(name: str | None) -> tuple[int, int, int]:
    return ACCENTS.get(name or "", ACCENTS[DEFAULT_ACCENT])


def distinct(keys: list[str | None]) -> list[str]:
    """Three template keys, deduplicated, in the order asked for.

    The point of three variants is three genuinely different frames. A model asked
    for variety still returns "stakes" three times often enough that this has to be
    enforced here rather than hoped for in the prompt.
    """
    out: list[str] = []
    for key in keys:
        resolved = get(key).key
        if resolved not in out:
            out.append(resolved)
    for key in TEMPLATES:
        if len(out) >= len(keys):
            break
        if key not in out:
            out.append(key)
    return out[: len(keys)]
