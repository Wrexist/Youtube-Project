"""The composite workflow — the thing the Create screen runs.

Stage order matters and is not arbitrary:

  * `grounding` runs *before* the script, so the angle and hook can be informed by
    what people actually search for rather than retrofitted to a finished script.
  * `titles` runs *after* the script, so a title can never promise something the
    script does not deliver. Retention outranks click-through; an overpromise costs
    both.
  * `chapters` runs last, after `subtitles`, because chapter timestamps must come
    from real cue timings, not estimates.
"""

from __future__ import annotations

from engine.workflows import media, script, seo
from engine.workflows.base import Workflow

VIDEO_WORKFLOW = Workflow(
    "video",
    [
        # Research and grounding — cheap, parallel-ish, and everything depends on it.
        seo.GroundingStage(),
        script.ResearchStage(),
        # Creative chain.
        script.AngleStage(),
        script.HookStage(),
        script.BeatsStage(),
        script.DraftStage(),
        script.CritiqueStage(),
        script.RevisionStage(),
        # Production.
        media.VoiceoverStage(),
        media.SubtitlesStage(),
        media.MaterialsStage(),
        media.RenderStage(),
        # Packaging — after the script exists, so promises stay honest.
        seo.TitlesStage(),
        seo.DescriptionStage(),
        seo.TagsStage(),
        seo.ChaptersStage(),
        media.ThumbnailStage(),
    ],
)

WORKFLOWS = {
    "video": VIDEO_WORKFLOW,
    "script": Workflow("script", script.SCRIPT_STAGES),
    "seo": Workflow("seo", seo.SEO_STAGES),
}


def get(name: str) -> Workflow:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow {name!r}; have {sorted(WORKFLOWS)}")
    return WORKFLOWS[name]
