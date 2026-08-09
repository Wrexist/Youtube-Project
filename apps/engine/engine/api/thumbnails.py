"""Look at the thumbnails, and change your mind about them.

The thumbnail is the only artifact a viewer sees before deciding whether to watch,
and until now the pipeline reported it as the text "3 items". Three images had been
designed, generated and composed — at $0.44, the most expensive single stage in a
run — and the one screen that could have shown them showed a number.

So: the variants, as pictures, plus a way to ask for a different one in words.

Regeneration reuses everything the stage already knows. The concept call that made
the originals saw the title, the hook, the alternative titles and the beats with
their visual directions and energy; asking for "make it darker" against a blank
context would throw all of that away and produce a thumbnail for a different video.
The instruction is applied *to* the existing concept rather than replacing it.

Text is still never baked into the generated image — `compose.make_thumbnail`
composes type in code, which is what keeps the overlay swappable and the
typography reliable. An instruction about wording changes `overlay_text`, not the
image prompt.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from engine.providers import llm
from engine.providers.llm import ProviderUnavailable
from engine.render import compose, templates

router = APIRouter(prefix="/v1/jobs", tags=["thumbnails"])

MAX_INSTRUCTION = 400

#: One lock per job, because a regeneration is a read-modify-write spanning two
#: awaits and the variant list is the thing being modified.
#:
#: Without it, two presses of "Make another" on the same job both read the list
#: at the same length, both compose `thumbnail_{index}` for that index, and the
#: second write replaces the list the first had appended to — so one variant
#: becomes unreachable although it was generated and charged for.
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class Variant(BaseModel):
    index: int
    url: str
    key: str
    template: str
    overlay_text: str
    accent: str
    rationale: str
    image_model: str


class Thumbnails(BaseModel):
    variants: list[Variant]
    chosen: int
    #: What one more costs, so the button can say so before it is pressed.
    cost_per_generation: float


class Regenerate(BaseModel):
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION)
    #: Which existing variant to work from. The concept is edited, not replaced.
    base_index: int = 0


class Sharpen(BaseModel):
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION)


class Sharpened(BaseModel):
    instruction: str
    why: str


def _job(job_id: str) -> dict:
    from engine.main import JOBS

    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return job


def _variants(job: dict) -> list[dict]:
    state = job.get("states", {}).get("thumbnail")
    if state is None or state.output is None:
        raise HTTPException(409, "this job has no thumbnails yet")
    return list(state.output.value or [])


@router.get("/{job_id}/thumbnails")
async def list_thumbnails(job_id: str) -> Thumbnails:
    from engine.providers import images

    job = _job(job_id)
    variants = _variants(job)
    spec = images.selected()

    return Thumbnails(
        variants=[_present(v, i) for i, v in enumerate(variants)],
        chosen=int(job.get("inputs", {}).get("chosen_thumbnail_index", 0) or 0),
        cost_per_generation=round((spec.cost_per_image if spec else 0.0) + 0.01, 3),
    )


@router.post("/{job_id}/thumbnails")
async def regenerate(job_id: str, body: Regenerate) -> Thumbnails:
    """Apply an instruction to an existing concept and compose a new variant.

    Appends rather than replaces. The originals cost real money and the operator
    may well prefer one of them after seeing the alternative — overwriting the
    thing they were comparing against is the one behaviour that cannot be undone.
    """
    job = _job(job_id)
    variants = _variants(job)
    if not 0 <= body.base_index < len(variants):
        raise HTTPException(422, f"no variant {body.base_index}")

    base = variants[body.base_index]
    try:
        concept = await _revise(base, body.instruction, job)
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    # Only the mutation is serialised. `_revise` is a model call that touches
    # nothing shared, and holding the lock across it would make two people asking
    # for a thumbnail at once wait for each other's LLM round trip.
    async with _locks[job_id]:
        return await _compose_and_append(job, job_id, concept)


async def _compose_and_append(job: dict, job_id: str, concept: dict) -> Thumbnails:
    """Render the concept and add it to the stage. Caller holds the job's lock.

    The variant list is re-read here rather than passed in: another request may
    have appended while `_revise` was awaiting, and `index` has to be the length
    as of *now* or the two collide on one artifact key.
    """
    from engine.providers import images

    variants = _variants(job)
    index = len(variants)
    try:
        thumb = await compose.make_thumbnail(concept, job_id=job_id, index=index)
    except Exception as exc:  # noqa: BLE001 - a failed regeneration keeps the originals
        logger.opt(exception=True).error("thumbnail regeneration failed for {}", job_id)
        raise HTTPException(502, f"could not compose the thumbnail: {exc}") from exc

    fresh = {
        **concept,
        "key": thumb.key,
        "template": thumb.template,
        "image_model": thumb.image_model,
    }
    variants.append(fresh)

    state = job["states"]["thumbnail"]
    state.output.value = variants
    state.output.artifacts[f"thumbnail_{index}"] = thumb.key
    # Metered like every other generation - CLAUDE.md #5. A run where the operator
    # asked for six more thumbnails cost that, and the job's total has to say so.
    state.output.cost_usd = (state.output.cost_usd or 0.0) + thumb.cost_usd

    await _persist(job)

    spec = images.selected()
    return Thumbnails(
        variants=[_present(v, i) for i, v in enumerate(variants)],
        chosen=index,  # the new one, which is what was just asked for
        cost_per_generation=round((spec.cost_per_image if spec else 0.0) + 0.01, 3),
    )


@router.post("/{job_id}/thumbnails/sharpen")
async def sharpen(job_id: str, body: Sharpen) -> Sharpened:
    """Turn a rough note into an instruction an image model can act on.

    "make it better" is not actionable and produces a random different thumbnail.
    This is the same idea as the Create screen's Improve button, applied to the
    thing being asked for rather than to the topic — and it is deliberately a
    separate press, because rewriting someone's words without being asked is the
    fastest way to make them stop trusting the box they typed into.
    """
    job = _job(job_id)
    variants = _variants(job)
    base = variants[0] if variants else {}

    model = llm.for_task("thumbnail")
    try:
        result, _ = await model.json(
            f"""A creator is looking at a YouTube thumbnail and wants it changed.

The thumbnail they are looking at:
  layout: {base.get("template", "unknown")}
  text on it: {base.get("overlay_text", "")!r}
  accent colour: {base.get("accent", "")}
  image: {base.get("image_prompt", "")}

What they typed: {body.instruction!r}

Rewrite it as a specific instruction for whoever remakes this thumbnail.

- Keep their intent exactly. You are making it actionable, not choosing for them.
- Name what changes and what stays. "make it better" is not an instruction;
  "darker background, keep the text and the layout" is.
- Be concrete about the picture: subject, framing, lighting, colour.
- Stay within one thumbnail. Do not propose a series of options.

Return: {{"instruction": str, "why": str}}

`why` is one short sentence saying what you made specific.""",
            max_tokens=500,
        )
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    return Sharpened(
        instruction=str(result.get("instruction") or "").strip() or body.instruction,
        why=str(result.get("why") or "").strip(),
    )


async def _revise(base: dict, instruction: str, job: dict) -> dict:
    """The base concept with the instruction applied, as a whole concept.

    Asked for as one object rather than as a patch: the fields interact — an
    accent that worked against a bright image is wrong against a dark one — and a
    model editing one field at a time has no way to notice.
    """
    titles = job.get("states", {}).get("titles")
    title = ""
    if titles is not None and titles.output is not None:
        chosen = list(titles.output.value or [])
        if chosen:
            title = getattr(chosen[0], "text", "") or ""

    model = llm.for_task("thumbnail")
    result, _ = await model.json(
        f"""Revise one YouTube thumbnail concept.

The video: {title or job.get("inputs", {}).get("topic", "")}

The current concept:
  template: {base.get("template", "")}
  image_prompt: {base.get("image_prompt", "")}
  overlay_text: {base.get("overlay_text", "")}
  accent: {base.get("accent", "")}
  focal_point: {base.get("focal_point", "")}

What the creator asked for: {instruction!r}

Apply it. Change what they asked about and leave the rest alone — this is a
revision, not a fresh design, and they are comparing it against the original.

Rules that do not bend:

- **No text in `image_prompt`.** Type is composed separately in code, so an image
  with words baked into it is a thumbnail with two sets of text on it. Say "no
  text" in the prompt.
- **`overlay_text` is at most 3 words**, and it is the thing read at 168px.
- **`accent` must be one of:** {", ".join(templates.ACCENTS)} — and must contrast
  with its own image rather than blending into it.
- **`template` must be one of:** {", ".join(templates.TEMPLATES)}.

Return: {{"template": str, "image_prompt": str, "overlay_text": str,
          "accent": str, "focal_point": str, "rationale": str}}""",
        max_tokens=900,
    )

    concept = {**base, **{k: v for k, v in result.items() if v}}
    # Validated rather than trusted: an invented template name falls back to the
    # default layout, and an invented accent to the template's own, both silently.
    if concept.get("template") not in templates.TEMPLATES:
        concept["template"] = base.get("template", templates.FALLBACK)
    if concept.get("accent") not in templates.ACCENTS:
        concept["accent"] = base.get("accent", "")
    return concept


def _present(variant: dict, index: int) -> Variant:
    return Variant(
        index=index,
        key=variant.get("key", ""),
        url=f"/v1/files/{variant.get('key', '')}",
        template=variant.get("template", ""),
        overlay_text=variant.get("overlay_text", ""),
        accent=variant.get("accent", ""),
        rationale=variant.get("rationale", ""),
        image_model=variant.get("image_model", ""),
    )


async def _persist(job: dict) -> None:
    """Write the new variant through, so it survives a reload.

    Regeneration costs money; losing it to a refresh would be the same defect as
    the one that made a finished render unreachable.
    """
    from engine.settings import get_settings

    if not get_settings().persist:
        return
    from engine import repository

    try:
        await repository.save_job(job)
    except Exception:  # noqa: BLE001 - the variant exists either way
        logger.exception("could not persist the regenerated thumbnail for {}", job.get("id"))
