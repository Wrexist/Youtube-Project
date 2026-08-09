"use client";

import { useEffect, useState } from "react";
import { loadThumbnails, remakeThumbnail, sharpenInstruction } from "@/app/actions";
import { fileUrl } from "@/lib/engine";
import type { Thumbnails } from "@studio/contracts";

/**
 * The thumbnails, as pictures, with a way to ask for a different one.
 *
 * This row used to read "3 items". Three concepts had been designed, three
 * backgrounds generated and three type treatments composed — the most expensive
 * single stage in a run — and the screen showed a number.
 *
 * Two things it deliberately does not do:
 *
 * **It does not auto-improve what you typed.** Sharpen is its own press. Silently
 * rewriting the box someone is typing into is the fastest way to make them stop
 * trusting it, and the rewrite is shown before it is used.
 *
 * **It does not replace.** Every generation appends. The originals cost real money
 * and the point of asking for another is to compare, so overwriting the thing being
 * compared against is the one move that cannot be undone.
 */
export function ThumbnailPanel({
  jobId,
  chosen,
  onChoose,
}: {
  jobId: string;
  /** The Create screen's selection, so the pick reaches the publish call. */
  chosen?: number;
  onChoose?: (index: number) => void;
}) {
  const [set, setSet] = useState<Thumbnails | null>(null);
  const [instruction, setInstruction] = useState("");
  const [why, setWhy] = useState<string | null>(null);
  const [busy, setBusy] = useState<"none" | "sharpening" | "making">("none");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadThumbnails(jobId).then((r) => {
      if (cancelled) return;
      // The failure branch was discarded, so an unreachable engine — or the 409
      // this endpoint returns when the stage produced nothing — left the panel
      // on "Loading thumbnails…" for good, with no way to tell or retry.
      if (r.ok && r.data) setSet(r.data);
      else setError(r.error ?? 'could not load the thumbnails');
    });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const selected = chosen ?? set?.chosen ?? 0;

  async function sharpen() {
    if (!instruction.trim() || busy !== "none") return;
    setError(null);
    setBusy("sharpening");
    try {
      const r = await sharpenInstruction(jobId, instruction.trim());
      if (!r.ok || !r.data) {
        setError(r.error ?? "could not sharpen that — your wording is unchanged");
        return;
      }
      setInstruction(r.data.instruction);
      setWhy(r.data.why);
    } finally {
      setBusy("none");
    }
  }

  async function make() {
    if (!instruction.trim() || busy !== "none") return;
    setError(null);
    setBusy("making");
    try {
      const r = await remakeThumbnail(jobId, instruction.trim(), selected);
      if (!r.ok || !r.data) {
        setError(
          r.error ?? "could not make that thumbnail — the originals are unchanged",
        );
        return;
      }
      setSet(r.data);
      setWhy(null);
      setInstruction("");
      onChoose?.(r.data.chosen);
    } finally {
      setBusy("none");
    }
  }

  if (!set) {
    if (error) {
      return (
        <p role="alert" className="text-[12px] text-[var(--color-bad)]">
          {error}
        </p>
      );
    }
    return <p className="text-[12px] text-[var(--color-faint)]">Loading thumbnails…</p>;
  }

  return (
    <div>
      {/* Three across, because that is how many the stage makes and a 16:9 tile at
          a third of this column is close to the size a thumbnail is actually
          judged at in a feed. Wraps to one column on a narrow screen. */}
      <ul
        className="grid grid-cols-1 gap-3 sm:grid-cols-3"
        role="radiogroup"
        aria-label="Thumbnails"
      >
        {set.variants.map((variant) => {
          const active = variant.index === selected;
          return (
            <li key={variant.key || variant.index}>
              <button
                role="radio"
                aria-checked={active}
                onClick={() => onChoose?.(variant.index)}
                className={`w-full overflow-hidden rounded-[var(--radius-card)] border text-left transition-colors duration-150 ${
                  active
                    ? "border-[var(--color-accent)]"
                    : "border-[var(--color-line)] hover:border-[var(--color-line-hover)]"
                }`}
              >
                {/* Built from the key, not from the engine's `url`. That field is
                    a path — `/v1/files/...` — which a browser resolves against the
                    *web app* on :3000, where nothing serves it. `fileUrl` prefixes
                    the engine's own origin, which is what every other image in the
                    app already does. */}
                {/* eslint-disable-next-line @next/next/no-img-element -- the engine serves these, not Next's optimiser */}
                <img
                  src={fileUrl(variant.key)}
                  alt={variant.overlay_text || `Thumbnail ${variant.index + 1}`}
                  className="aspect-video w-full bg-[var(--color-raised)] object-cover"
                />
                <div className="px-2.5 py-2">
                  <p className="truncate text-[12px] font-semibold">
                    {variant.overlay_text || "—"}
                  </p>
                  <p className="mono mt-0.5 truncate text-[11px] text-[var(--color-faint)]">
                    {variant.template}
                    {variant.accent ? ` · ${variant.accent}` : ""}
                  </p>
                </div>
              </button>
            </li>
          );
        })}
      </ul>

      {/* The rationale for whichever is selected. It is the one thing the concept
          call produced that is not visible in the picture. */}
      {set.variants[selected]?.rationale && (
        <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-muted)]">
          {set.variants[selected].rationale}
        </p>
      )}

      <div className="mt-4">
        <label
          htmlFor="thumb-instruction"
          className="text-[12px] text-[var(--color-muted)]"
        >
          Change the selected one
        </label>
        <textarea
          id="thumb-instruction"
          value={instruction}
          onChange={(e) => {
            setInstruction(e.target.value);
            setWhy(null);
          }}
          rows={2}
          placeholder="Darker background, keep the text. Put the subject on the right."
          className="mt-1.5 w-full resize-y rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-accent)]"
        />

        {why && (
          <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--color-muted)]">
            {why}
          </p>
        )}
        {error && (
          <p role="alert" className="mt-1.5 text-[12px] text-[var(--color-bad)]">
            {error}
          </p>
        )}

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <button
            onClick={sharpen}
            disabled={!instruction.trim() || busy !== "none"}
            title="Rewrite what you typed as something an image model can act on"
            className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2.5 py-1.5 text-[12px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === "sharpening" ? "Sharpening…" : "Improve wording"}
          </button>
          <button
            onClick={make}
            disabled={!instruction.trim() || busy !== "none"}
            className="rounded-[var(--radius-btn)] border border-[var(--color-line)] px-2.5 py-1.5 text-[12px] font-semibold text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-line-hover)] hover:text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === "making" ? "Making…" : "Make another"}
          </button>
          {/* Named before the press, not discovered after it. */}
          <p className="text-[12px] text-[var(--color-faint)]">
            Adds a variant · ${set.cost_per_generation.toFixed(2)}
          </p>
        </div>
      </div>
    </div>
  );
}
