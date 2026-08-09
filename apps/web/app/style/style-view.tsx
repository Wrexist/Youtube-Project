"use client";

import { useMemo, useState } from "react";
import { updateStyle } from "@/app/actions";
import { Button, Card } from "@/components/ui";
import type { Style, StyleUpdate, StyleVoice } from "@studio/contracts";

/**
 * Three things that decide the character of a video, and one disclosure.
 *
 * Every control saves on change rather than behind a Save button. There is no
 * multi-field form to keep consistent — each setting is independent, the engine
 * returns the whole style in force after every write, and a Save button on a
 * screen with one control per row is a second click that can only ever be forgotten.
 *
 * What comes back replaces local state wholesale, which is the point: an already
 * exported `STUDIO_*` variable outranks the dotenv, so a save can legitimately
 * succeed and change nothing. Rendering the response rather than the request is
 * what makes that visible instead of silent.
 */
export function StyleView({ initial }: { initial: Style }) {
  const [style, setStyle] = useState(initial);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [motionOpen, setMotionOpen] = useState(false);
  const [locale, setLocale] = useState(() => localeOf(initial.voice));

  const locales = useMemo(
    () => [...new Set(initial.options.voices.map((v) => v.locale))].sort(),
    [initial.options.voices],
  );
  const inLocale = useMemo(
    () => style.options.voices.filter((v) => v.locale === locale),
    [style.options.voices, locale],
  );

  async function save(field: string, changes: StyleUpdate) {
    setSaving(field);
    setError(null);
    const result = await updateStyle(changes);
    if (result.ok && result.data) setStyle(result.data);
    else setError(result.error ?? "could not save that — nothing changed");
    setSaving(null);
  }

  const { options } = style;

  return (
    <div className="grid gap-6">
      {error && (
        <p role="alert" className="text-[13px] text-[var(--color-bad)]">
          {error}
        </p>
      )}

      {/* ── Narrator ─────────────────────────────────────────────────────── */}
      <Card className="p-5">
        <Field
          title="Narrator"
          hint="Every video is spoken by this voice. It is the single loudest thing about a channel."
          busy={saving === "voice"}
        />

        {options.voices_live ? (
          <>
            {/* Grouped by locale rather than listed. Edge ships around three
                hundred voices across sixty-odd locales, and a flat radio group of
                that is not a choice, it is a phone book. Opens on the locale
                already in use, so the common case is one glance and no selection. */}
            <label className="mt-4 block text-[12px] text-[var(--color-muted)]">
              Language
              <select
                value={locale}
                onChange={(e) => setLocale(e.target.value)}
                className="mt-1.5 block w-full max-w-[320px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none focus:border-[var(--color-accent)]"
              >
                {locales.map((code) => (
                  <option key={code} value={code}>
                    {localeName(code)} — {code}
                  </option>
                ))}
              </select>
            </label>

            <div className="mt-3 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Voice">
              {inLocale.map((voice) => (
                <VoiceOption
                  key={voice.id}
                  voice={voice}
                  selected={voice.id === style.voice}
                  onChoose={() => save("voice", { voice: voice.id })}
                />
              ))}
            </div>
          </>
        ) : (
          <div className="mt-4">
            {/* The catalogue is a network call and it is allowed to fail. Typing an
                id still works, so the screen degrades to the thing it would have
                been anyway rather than to nothing. */}
            <p className="text-[12px] text-[var(--color-faint)]">
              Could not reach the voice catalogue, so the list is unavailable. The
              current voice still applies, and any Edge voice id works here.
            </p>
            <input
              defaultValue={style.voice}
              onBlur={(e) =>
                e.target.value.trim() !== style.voice &&
                save("voice", { voice: e.target.value.trim() })
              }
              className="mono mt-2 w-full max-w-[320px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none focus:border-[var(--color-accent)]"
              aria-label="Voice id"
            />
          </div>
        )}
      </Card>

      {/* ── Music ────────────────────────────────────────────────────────── */}
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <Field
            title="Music"
            hint="A bed under the narration. Off by default, and nothing ships with music."
            busy={saving === "bgm"}
          />
          <Switch
            checked={style.bgm_enabled}
            label="Music"
            disabled={saving !== null}
            onChange={(on) => save("bgm", { bgm_enabled: on })}
          />
        </div>

        {/* Said before the switch is useful, not after it disappoints. Turning music
            on with an empty directory renders exactly as it did with music off, and
            the reason is not discoverable from the result. */}
        {options.tracks.length === 0 ? (
          <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-muted)]">
            No tracks found in{" "}
            <span className="mono text-[11px]">{options.tracks_dir}</span>. Drop
            licensed <span className="mono text-[11px]">.mp3</span> or{" "}
            <span className="mono text-[11px]">.wav</span> files there and they appear
            here. Publishing over music you do not have the rights to is a copyright
            strike, which is why none is included.
          </p>
        ) : (
          style.bgm_enabled && (
            <div className="mt-4 grid gap-3">
              <label className="text-[12px] text-[var(--color-muted)]">
                Track
                <select
                  value={style.bgm_track}
                  disabled={saving !== null}
                  onChange={(e) => save("bgm", { bgm_track: e.target.value })}
                  className="mt-1.5 block w-full max-w-[320px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none focus:border-[var(--color-accent)]"
                >
                  {/* Empty is a real choice, not a placeholder — `bgm.resolve("")`
                      picks a different track per render, which is what the engine
                      did unconditionally before this screen existed. */}
                  <option value="">A different one each render</option>
                  {options.tracks.map((track) => (
                    <option key={track} value={track}>
                      {track}
                    </option>
                  ))}
                </select>
              </label>

              <Slider
                label="Volume"
                value={style.bgm_volume}
                min={0.02}
                max={1}
                step={0.02}
                disabled={saving !== null}
                format={(v) => `${Math.round(v * 100)}%`}
                onCommit={(v) => save("bgm", { bgm_volume: v })}
              />
            </div>
          )
        )}
      </Card>

      {/* ── Captions ─────────────────────────────────────────────────────── */}
      <Card className="p-5">
        <Field
          title="Captions"
          hint="Burnt into the video. Most of the audience watches muted, so this is not decoration."
          busy={saving === "font"}
        />
        <select
          value={style.subtitle_font}
          disabled={saving !== null}
          onChange={(e) => save("font", { subtitle_font: e.target.value })}
          className="mt-4 block w-full max-w-[320px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none focus:border-[var(--color-accent)]"
        >
          <option value="">Bundled default</option>
          {options.fonts.map((font) => (
            <option key={font} value={font}>
              {font}
            </option>
          ))}
        </select>
        {options.fonts.length === 0 && (
          <p className="mt-2 text-[12px] text-[var(--color-faint)]">
            No extra fonts installed. Add <span className="mono text-[11px]">.ttf</span>{" "}
            files to the fonts directory to choose one.
          </p>
        )}
      </Card>

      {/* ── Motion, behind a disclosure ──────────────────────────────────── */}
      <Card className="p-5">
        <button
          onClick={() => setMotionOpen(!motionOpen)}
          aria-expanded={motionOpen}
          className="flex w-full items-center justify-between gap-4 text-left"
        >
          <Field
            title="Motion"
            hint="How stills move and how shots join. The defaults are deliberate."
            busy={saving === "motion"}
          />
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            className="size-4 shrink-0 text-[var(--color-faint)] transition-transform duration-200"
            style={{ transform: motionOpen ? "rotate(180deg)" : "none" }}
            aria-hidden
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>

        {motionOpen && (
          <div className="mt-4 grid gap-4">
            <label className="text-[12px] text-[var(--color-muted)]">
              Ken Burns
              <select
                value={style.ken_burns}
                disabled={saving !== null}
                onChange={(e) =>
                  save("motion", {
                    ken_burns: e.target.value as Style["ken_burns"],
                  })
                }
                className="mt-1.5 block w-full max-w-[320px] rounded-[var(--radius-btn)] border border-[var(--color-line)] bg-[var(--color-bg)] px-3 py-2 text-[13px] outline-none focus:border-[var(--color-accent)]"
              >
                <option value="alternate">Alternate — in, then out</option>
                <option value="in">Always push in</option>
                <option value="out">Always pull out</option>
                <option value="none">Still</option>
              </select>
              <span className="mt-1.5 block text-[12px] text-[var(--color-faint)]">
                Alternating by default: a whole video pushing one direction develops a
                rhythm the viewer starts to anticipate.
              </span>
            </label>

            <Slider
              label="Crossfade"
              value={style.transition_fade_s}
              min={0}
              max={2}
              step={0.05}
              disabled={saving !== null}
              format={(v) => (v === 0 ? "Hard cuts" : `${v.toFixed(2)}s`)}
              onCommit={(v) => save("motion", { transition_fade_s: v })}
            />
            <p className="-mt-2 text-[12px] text-[var(--color-faint)]">
              Zero by default. Fast-cut faceless video does not dissolve between shots
              — it reads as a slideshow.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

/** `en-US` out of `en-US-AvaNeural`, falling back to English for an unparseable id. */
function localeOf(voiceId: string): string {
  const parts = voiceId.split("-");
  return parts.length >= 2 ? `${parts[0]}-${parts[1]}` : "en-US";
}

/**
 * "English (United States)" out of "en-US".
 *
 * `Intl.DisplayNames` rather than a hand-written map: sixty locales is a table
 * that would go stale, and the browser already knows — in the reader's own
 * language, which a hard-coded English map could not do.
 */
function localeName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function Field({ title, hint, busy }: { title: string; hint: string; busy: boolean }) {
  return (
    <div className="min-w-0">
      <h2 className="text-[15px] font-semibold">
        {title}
        {/* Not a spinner. It is a word, it is announced, and it occupies space that
            was already there so nothing shifts when it appears. */}
        {busy && (
          <span
            aria-live="polite"
            className="ml-2 text-[12px] font-normal text-[var(--color-faint)]"
          >
            saving…
          </span>
        )}
      </h2>
      <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-muted)]">{hint}</p>
    </div>
  );
}

function VoiceOption({
  voice,
  selected,
  onChoose,
}: {
  voice: StyleVoice;
  selected: boolean;
  onChoose: () => void;
}) {
  return (
    <button
      role="radio"
      aria-checked={selected}
      onClick={onChoose}
      className={`rounded-[var(--radius-card)] border px-3.5 py-3 text-left transition-colors duration-150 ${
        selected
          ? "border-[var(--color-accent)] bg-[var(--color-raised)]"
          : "border-[var(--color-line)] hover:border-[var(--color-line-hover)]"
      }`}
    >
      <span className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold">{voice.name.replace(/Neural$/, "")}</span>
        <span className="mono text-[11px] text-[var(--color-faint)]">{voice.locale}</span>
        {voice.gender && (
          <span className="text-[11px] text-[var(--color-faint)]">{voice.gender}</span>
        )}
      </span>
      {/* Microsoft's own personality tags, passed through rather than paraphrased.
          They are the only honest description available without listening to all
          three hundred of them. */}
      {/* Optional in the schema because the field has a default, and genuinely
          absent for voices Microsoft has not tagged. */}
      {voice.traits && voice.traits.length > 0 && (
        <span className="mt-1 block truncate text-[12px] text-[var(--color-muted)]">
          {voice.traits.join(" · ")}
        </span>
      )}
    </button>
  );
}

function Switch({
  checked,
  label,
  disabled,
  onChange,
}: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-150 disabled:opacity-40 ${
        checked
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]"
          : "border-[var(--color-line-hover)] bg-[var(--color-raised)]"
      }`}
    >
      <span
        className="absolute top-0.5 size-4 rounded-full bg-white transition-all duration-150"
        style={{ left: checked ? "calc(100% - 1.125rem)" : "0.125rem" }}
        aria-hidden
      />
    </button>
  );
}

/**
 * A slider that reports on release, not on every pixel.
 *
 * `onChange` fires per frame while dragging; wiring a save to it would send a
 * hundred requests and write the dotenv a hundred times for one gesture. The value
 * shown tracks the thumb so the drag still feels live.
 */
function Slider({
  label,
  value,
  min,
  max,
  step,
  disabled,
  format,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled?: boolean;
  format: (v: number) => string;
  onCommit: (v: number) => void;
}) {
  const [local, setLocal] = useState(value);

  return (
    <label className="block text-[12px] text-[var(--color-muted)]">
      <span className="flex items-baseline justify-between gap-4">
        {label}
        <span className="mono text-[11px] text-[var(--color-faint)]">{format(local)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={local}
        disabled={disabled}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={() => local !== value && onCommit(local)}
        onKeyUp={() => local !== value && onCommit(local)}
        className="mt-2 w-full max-w-[320px] accent-[var(--color-accent)]"
      />
    </label>
  );
}
