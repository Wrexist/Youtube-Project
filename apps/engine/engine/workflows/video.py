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


# The SEO stages read the script, but they cannot say so in `seo.py`: the standalone
# `Workflow("seo", SEO_STAGES)` has no script stages, and `Workflow._validate`
# rejects a dependency that is not defined earlier in the *same* workflow — adding
# "revision" to the shared class makes video.py fail at import.
#
# So the dependency is declared here, where the script stages exist. Without it,
# editing the script through POST /v1/jobs/{id}/edit left the title, description and
# tags untouched: `dependents_of("revision")` returned only the media stages, the SEO
# stages stayed DONE, `Workflow.run` replayed them verbatim, and the publish gate
# uploaded the *old* title for a script that no longer said that. `tags` needs
# nothing extra — it depends on `titles`, so it is invalidated transitively.
#
# Both "draft" and "revision" are named because RevisionStage is skippable.


class _Titles(seo.TitlesStage):
    depends_on = ("grounding", "draft", "revision")


class _Description(seo.DescriptionStage):
    depends_on = ("titles", "grounding", "draft", "revision")


class _Chapters(seo.ChaptersStage):
    # Also fixes a dependency it always needed: ChaptersStage calls
    # `ctx.get("subtitles")` while declaring only ("titles",), so re-running the
    # voiceover left chapter timestamps pointing at cues that no longer existed.
    depends_on = ("titles", "subtitles")


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
        _Titles(),
        _Description(),
        seo.TagsStage(),
        _Chapters(),
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


#: Workflows `POST /v1/jobs` will start. "publish" is deliberately absent: it is
#: reachable only through `POST /v1/jobs/{id}/publish`, which seeds it with a
#: finished job's states and a live YouTube client. Started directly it returned
#: 202, ran the entire paid render, and then died on a bare
#: `KeyError: 'youtube_client'` in UploadStage — which has max_attempts = 1, so
#: there was not even a retry to make the cause visible.
STARTABLE = frozenset({"video", "script", "seo"})


def get(name: str) -> Workflow:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow {name!r}; have {sorted(WORKFLOWS)}")
    return WORKFLOWS[name]
