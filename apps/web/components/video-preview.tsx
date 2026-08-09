"use client";

import { useState } from "react";
import { fileUrl } from "@/lib/engine";
import type { Stage } from "@/lib/types";

/**
 * The finished video, playable where it was made.
 *
 * The engine has always served this — `/v1/files/renders/<job>.mp4`, with range
 * requests, which is the whole reason `test_a_render_is_seekable` exists — but
 * nothing in the web app ever pointed at it. A completed render appeared as the
 * text `renders/6f16d7f402f9.mp4` on a collapsed stage row, so the only way to
 * watch the thing you had just spent an hour and a dollar making was to find it
 * on disk yourself.
 *
 * `preload="metadata"` rather than `auto`: these are 50MB+ files served from
 * localhost, and fetching the whole thing on page load would stall the pipeline
 * view behind it for no reason. Metadata is enough for the duration and the
 * scrubber; the rest arrives when Play is pressed.
 */
export function VideoPreview({
  stages,
  jobId,
}: {
  stages: Stage[];
  jobId: string | null;
}) {
  const [failed, setFailed] = useState(false);

  const key = renderKey(stages, jobId);
  if (!key || failed) return null;

  return (
    <div className="mb-4 overflow-hidden rounded-[var(--radius-card)] border border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--color-line)] px-4 py-3">
        <span className="text-[13px] font-semibold">Your video</span>
        <a
          href={fileUrl(key)}
          download
          className="text-[12px] text-[var(--color-muted)] underline decoration-[var(--color-line-hover)] underline-offset-4 transition-colors duration-150 hover:text-[var(--color-ink)]"
        >
          Download
        </a>
      </div>
      {/* Black rather than the surface colour: letterbox bars on a 9:16 short
          against a grey card read as a rendering fault. */}
      <video
        key={key}
        src={fileUrl(key)}
        poster={posterUrl(stages, jobId)}
        controls
        preload="metadata"
        onError={() => setFailed(true)}
        className="block max-h-[70vh] w-full bg-black"
      />
    </div>
  );
}

/**
 * Where the render landed, according to the stage that wrote it.
 *
 * Read from the stage rather than assembled from the job id, because the stage
 * is the thing that actually knows — and returns null when there is no finished
 * render, which is what keeps this component off the screen for the fifteen
 * minutes before there is anything to play. The job id is only a fallback for a
 * stage that reported success without naming its output.
 */
function renderKey(stages: Stage[], jobId: string | null): string | null {
  const render = stages.find((s) => s.name === "render");
  if (!render || render.status !== "done") return null;

  const said = `${render.detail ?? ""} ${render.summary ?? ""}`;
  const match = said.match(/renders\/[\w.-]+\.mp4/);
  if (match) return match[0];
  return jobId ? `renders/${jobId}.mp4` : null;
}

/** The generated thumbnail, when there is one — this is how it will look in a feed. */
function posterUrl(stages: Stage[], jobId: string | null): string | undefined {
  const thumbnail = stages.find((s) => s.name === "thumbnail");
  if (!thumbnail || thumbnail.status !== "done" || !jobId) return undefined;
  return fileUrl(`thumbnails/${jobId}-0.jpg`);
}
