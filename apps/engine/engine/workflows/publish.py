"""Publishing stages.

Upload, thumbnail, captions, and playlist are four separate stages rather than one
"publish" step, because they have four different failure modes and only the first one
is expensive. If captions fail, the video is live and correct — re-running a
400-unit caption upload is very different from re-running a 1,600-unit video upload.

A failed publish is also not a failed job: the render is still good. The Queue screen
offers a retry at the publish step alone for exactly this reason.
"""

from __future__ import annotations

from datetime import datetime

from engine.providers.youtube import YouTube
from engine.quota import ledger
from engine.storage import store
from engine.workflows.base import Provenance, Stage, StageOutput, WorkflowContext


class UploadStage(Stage[str]):
    name = "upload"
    title = "Upload"
    depends_on = ("render", "titles", "description", "tags")
    timeout_s = None  # a large upload on a slow line legitimately takes a long time
    max_attempts = 1  # resumption is handled inside the client; a retry here re-spends

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        client: YouTube = ctx.inputs["youtube_client"]
        titles = ctx.get("titles")
        chosen = ctx.inputs.get("chosen_title_index", 0)
        publish_at: datetime | None = ctx.inputs.get("publish_at")

        video_path = await store.local_path(ctx.get("render"))

        async def on_progress(fraction: float, message: str) -> None:
            await ctx.progress(message, fraction)

        video_id = await client.upload(
            video_path,
            title=titles[chosen].text,
            description=ctx.get("description"),
            tags=ctx.get("tags"),
            # Scheduled videos must go up private; publishAt is ignored otherwise.
            privacy="private" if publish_at else ctx.inputs.get("privacy", "public"),
            publish_at=publish_at,
            made_for_kids=ctx.inputs.get("made_for_kids", False),
            on_progress=on_progress,
        )

        return StageOutput(
            value=video_id,
            provenance=Provenance(
                params={
                    "title": titles[chosen].text,
                    "strategy": titles[chosen].strategy,  # attributed to CTR in Phase 8
                    "publish_at": publish_at.isoformat() if publish_at else None,
                    "quota_spent": ledger.cost_of("videos.insert"),
                }
            ),
        )


class ThumbnailSetStage(Stage[str]):
    name = "thumbnail_set"
    title = "Thumbnail"
    depends_on = ("upload", "thumbnail")
    optional = True
    max_attempts = 4  # the video may still be processing right after upload

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        client: YouTube = ctx.inputs["youtube_client"]
        video_id = ctx.get("upload")
        variants = ctx.get("thumbnail")
        chosen = ctx.inputs.get("chosen_thumbnail_index", 0)

        path = await store.local_path(variants[chosen]["key"])
        await client.set_thumbnail(video_id, path)

        return StageOutput(
            value=variants[chosen]["key"],
            provenance=Provenance(params={"variant_index": chosen}),
        )


class CaptionsStage(Stage[str]):
    name = "captions"
    title = "Captions"
    depends_on = ("upload", "subtitles")
    optional = True

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        client: YouTube = ctx.inputs["youtube_client"]
        video_id = ctx.get("upload")
        cues = ctx.get("subtitles")

        srt = _to_srt(cues)
        path = await store.put_bytes(srt.encode("utf-8"), f"captions/{ctx.job_id}.srt")
        await client.upload_captions(video_id, path)

        return StageOutput(
            value=str(path),
            provenance=Provenance(params={"cue_count": len(cues)}),
        )


class PlaylistStage(Stage[str]):
    name = "playlist"
    title = "Playlist"
    depends_on = ("upload",)
    optional = True

    def should_skip(self, ctx: WorkflowContext) -> bool:
        return not ctx.inputs.get("playlist_id")

    async def run(self, ctx: WorkflowContext) -> StageOutput[str]:
        client: YouTube = ctx.inputs["youtube_client"]
        playlist_id = ctx.inputs["playlist_id"]
        await client.add_to_playlist(ctx.get("upload"), playlist_id)
        return StageOutput(value=playlist_id, provenance=Provenance())


def _to_srt(cues: list[dict]) -> str:
    lines = []
    for i, cue in enumerate(cues, 1):
        lines += [str(i), f"{_ts(cue['start'])} --> {_ts(cue['end'])}", cue["text"], ""]
    return "\n".join(lines)


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def publish_stages() -> list[Stage]:
    """Fresh stage instances for one workflow.

    These cannot form a `Workflow` on their own: `UploadStage` depends on "render",
    "titles", "description" and "tags", which are video stages, and
    `Workflow._validate` requires dependencies to be defined earlier in the same
    workflow. `engine.workflows.video.PUBLISH_WORKFLOW` composes them onto the
    video stages, which is the only valid arrangement.
    """
    return [
        UploadStage(),
        ThumbnailSetStage(),
        CaptionsStage(),
        PlaylistStage(),
    ]


PUBLISH_STAGES: list[Stage] = publish_stages()
