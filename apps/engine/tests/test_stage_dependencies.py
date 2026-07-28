"""Editing a stage must invalidate everything that read it.

`POST /v1/jobs/{id}/edit` marks a stage edited and `Workflow.dependents_of` decides
what re-runs. That walk reads `depends_on` and nothing else — so a stage that reads
another stage's output without declaring it stays DONE, gets replayed verbatim, and
the job publishes with output computed from text that no longer exists.

That was live for the whole SEO chain: `TitlesStage` reads
`ctx.try_get("revision") or ctx.try_get("draft")` while declaring only
`("grounding",)`. Edit the script, and the title, description and tags kept their
old values — then the publish gate uploaded that stale title.
"""

from __future__ import annotations

import pytest

from engine.workflows import video


@pytest.fixture
def workflow():
    return video.get("video")


@pytest.mark.parametrize("stage", ["titles", "description", "tags", "chapters"])
def test_editing_the_script_invalidates_the_seo(workflow, stage):
    assert stage in workflow.dependents_of("revision")


@pytest.mark.parametrize("stage", ["titles", "description", "tags", "chapters"])
def test_editing_the_draft_invalidates_the_seo_too(workflow, stage):
    """RevisionStage is skippable, so the draft has to invalidate them as well."""
    assert stage in workflow.dependents_of("draft")


def test_re_timing_the_voiceover_invalidates_the_chapters(workflow):
    """ChaptersStage calls ctx.get("subtitles") — timestamps from cues that no
    longer exist are worse than no chapters."""
    assert "chapters" in workflow.dependents_of("subtitles")


def test_editing_the_script_still_invalidates_the_media(workflow):
    """The behaviour that already worked, kept working."""
    for stage in ("voiceover", "subtitles", "materials", "render", "thumbnail"):
        assert stage in workflow.dependents_of("revision")


def test_the_standalone_seo_workflow_still_builds():
    """The reason the dependency is declared in video.py and not in seo.py.

    `Workflow._validate` rejects a dependency not defined earlier in the same
    workflow, so putting "revision" on the shared class breaks this import.
    """
    assert video.get("seo").name == "seo"
    assert [s.name for s in video.get("seo").stages]


def test_every_stage_declares_what_it_reads(workflow):
    """The general guard.

    Finds a stage whose body calls ctx.get/try_get on a name it does not declare —
    which is exactly the shape of the bug above, and would otherwise only surface as
    a stale publish months later.
    """
    import inspect
    import re

    offenders = []
    names = {s.name for s in workflow.stages}
    for stage in workflow.stages:
        try:
            body = inspect.getsource(type(stage))
        except (OSError, TypeError):
            continue
        read = {r for r in re.findall(r'ctx\.(?:try_get|get)\(\s*["\'](\w+)["\']', body)}
        for source in read & names:
            # Transitive, not direct. Reading "angle" while declaring "hook" is fine
            # when hook itself depends on angle — editing angle still invalidates
            # this stage, which is the only thing that matters.
            if stage.name not in workflow.dependents_of(source):
                offenders.append(
                    f"{stage.name} reads '{source}' but an edit to it would not re-run"
                )
    assert not offenders, "; ".join(offenders)
