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

from engine.workflows import media, publish, script, seo
from engine.workflows.base import Workflow


def _video_stages() -> list:
    """The stages that produce a finished, unpublished video.

    A function rather than a module-level list because `Stage` instances carry
    per-run state; the publish workflow needs its own instances, not the same
    objects shared across two `Workflow` objects.
    """
    return [
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
    ]


VIDEO_WORKFLOW = Workflow("video", _video_stages())

# Publishing extends the video workflow rather than standing alone. That is not a
# convenience — `UploadStage.depends_on` is ("render", "titles", "description",
# "tags"), which are video stages, and `Workflow._validate` requires every
# dependency to be defined earlier in the same workflow. A standalone
# `Workflow("publish", PUBLISH_STAGES)` raises at import, which is why this was
# never registered and why nothing in the repo could publish.
#
# A publish job is seeded with the source video job's states, so every video stage
# is already DONE and gets replayed rather than re-run. Only the four publish
# stages actually execute. See `POST /v1/jobs/{job_id}/publish`.
PUBLISH_WORKFLOW = Workflow("publish", [*_video_stages(), *publish.publish_stages()])

WORKFLOWS = {
    "video": VIDEO_WORKFLOW,
    "script": Workflow("script", script.SCRIPT_STAGES),
    "seo": Workflow("seo", seo.SEO_STAGES),
    "publish": PUBLISH_WORKFLOW,
}


def get(name: str) -> Workflow:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow {name!r}; have {sorted(WORKFLOWS)}")
    return WORKFLOWS[name]
