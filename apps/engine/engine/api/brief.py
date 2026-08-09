"""Turn a typed fragment into a topic the pipeline can actually make a video from.

The Create screen asks "What's the video about?" and people answer it the way it
was asked — "mrbeast", or "Make a video about how mrbeast overtook youtube". Both
are honest answers and neither is a good brief. The first has no angle and no
searchable subject; the second buries the subject inside an instruction.

Everything downstream inherits that. Keyword grounding seeds autocomplete with it,
web research searches for it, and the angle and hook stages are handed it as the
premise. A vague topic does not fail loudly — it produces a competent video about
nothing in particular, which is the expensive kind of failure.

So this is one model call, before the workflow starts, that does what a producer
would do with a one-line pitch: find the actual subject, make it specific, and
choose the format that suits it.

It deliberately stops there. It does not pick the angle, write the hook or plan
the beats — there are stages for each of those, they are grounded in research this
call has not done, and pre-empting them with a guess would be exactly the
ungrounded shortcut the rest of the pipeline refuses to take.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from engine.providers import llm
from engine.providers.llm import ProviderUnavailable

router = APIRouter(prefix="/v1/brief", tags=["brief"])

#: Long enough for a real pitch, short enough that this cannot be used to push a
#: document through the model on someone else's key.
MAX_INPUT = 500


class BriefRequest(BaseModel):
    rough: str = Field(min_length=1, max_length=MAX_INPUT)
    #: What the operator currently has selected, so the model can disagree with a
    #: reason rather than being asked in a vacuum.
    format: str = "short"


class Brief(BaseModel):
    topic: str
    format: str
    why: str
    model: str
    cost_usd: float


@router.post("")
async def refine(body: BriefRequest) -> Brief:
    rough = body.rough.strip()
    if not rough:
        raise HTTPException(422, "nothing to work from")

    selected = "vertical Short" if body.format == "short" else "long-form 16:9"
    model = llm.for_task("brief")
    try:
        result, completion = await model.json(
            f"""A creator typed this into a "what's the video about?" box:

{rough!r}

They currently have the {selected} format selected.

Rewrite it as the topic line for one video. Return the topic exactly as it should
be searched and researched — not a title, not a pitch, not a sentence addressed to
anyone.

What makes this topic good:

- **It names its subject.** Strip any instruction wrapped around it: "make a video
  about X" is X. If they named a person, product or event, keep that name — it is
  the most searchable thing they gave you.
- **It is one video, not a category.** "mrbeast" is a channel; "how MrBeast's
  $1,000,000 giveaways actually make money" is a video. Narrow until there is a
  single question being answered.
- **It is answerable from public sources.** This is searched against YouTube
  autocomplete and an encyclopedia before anything is written, so it must use the
  words people actually type. No invented statistics, no numbers you are not sure
  of, no claims the creator did not make.
- **It keeps their intent.** You are sharpening what they asked for, not replacing
  it with a topic you find more interesting. If the fragment is already a good
  topic, return it unchanged and say so.

Also choose the format. "short" is a vertical Short under 3 minutes and suits one
surprising fact or a single narrow question. "long" is 16:9 and suits anything
needing chronology, several examples, or an argument built in stages.

Return: {{"topic": str, "format": "short"|"long", "why": str}}

`why` is one short sentence for the creator, in plain language, saying what you
changed and why. If you changed nothing, say that.""",
            max_tokens=700,
        )
    except ProviderUnavailable as exc:
        # A model that is not reachable is not a server fault, and the screen has
        # a useful thing to say about it. 503 rather than 500 so it is not
        # reported as a bug in Studio.
        raise HTTPException(503, str(exc)) from exc

    topic = str(result.get("topic") or "").strip()
    if not topic:
        # Better to keep what they typed than to blank the field they were about
        # to press Generate on.
        logger.warning("brief returned no topic for {!r}; keeping the original", rough)
        topic = rough

    chosen = result.get("format")
    return Brief(
        topic=topic,
        format=chosen if chosen in ("short", "long") else body.format,
        why=str(result.get("why") or "").strip(),
        # Provenance, per CLAUDE.md #2: which model wrote this and what it cost.
        # The topic goes on to seed an entire video, so "where did this come
        # from" has to be answerable afterwards.
        model=completion.model,
        cost_usd=round(completion.cost_usd, 4),
    )
