"use client";

/**
 * The Genre screen's interactions: curating the watchlist and sweeping it.
 *
 * One primary action (Sync). Adding a channel is a slim secondary row, and
 * pause/remove are per-row ghost buttons — three competing primary buttons
 * would make this a settings page, which the design system rejects on sight.
 *
 * Mutations call the engine directly and throw on failure; the thrown message
 * is shown, not swallowed — same contract as the Repurpose screen's writes.
 */

import { useState } from "react";

import type {
  GenreChannel,
  GenrePatterns,
  GenreWatchlist,
} from "@studio/contracts";

import {
  getGenrePatterns,
  syncGenre,
  toggleChannel,
  unwatchChannel,
  watchChannel,
} from "@/lib/engine";
import { Button, Card, Empty } from "@/components/ui";

const fmtInt = new Intl.NumberFormat("en-US");

function PatternsNarrative({ patterns }: { patterns: GenrePatterns }) {
  if (patterns.video_count === 0) {
    return null;
  }
  const strategies = patterns.hook_patterns.slice(0, 3);
  return (
    <Card className="p-6">
      <p className="text-[15px] font-semibold">What this niche rewards</p>
      <ul className="mt-3 space-y-2 text-[13px] text-[var(--color-muted)]">
        {strategies.map((s) => (
          <li key={s.pattern}>
            <span className="font-semibold text-[var(--color-ink)]">{s.pattern}</span>
            -led titles are{" "}
            <span className="mono">{Math.round(s.share * 100)}%</span> of the corpus
            {s.median_views_per_day != null && (
              <>
                {" "}
                ·{" "}
                <span className="mono">{fmtInt.format(Math.round(s.median_views_per_day))}</span>{" "}
                views/day median
              </>
            )}
          </li>
        ))}
        {patterns.median_duration_s != null && (
          <li>
            median runtime ≈{" "}
            <span className="mono">{Math.round(patterns.median_duration_s / 60)}</span> min
          </li>
        )}
        {patterns.uploads_per_week != null && (
          <li>
            competitors upload ≈{" "}
            <span className="mono">{patterns.uploads_per_week}</span>×/week
          </li>
        )}
      </ul>
      {/* Evidence these numbers come from somewhere real, and how current it is. */}
      <p className="mt-3 text-[12px] text-[var(--color-faint)]">
        From {fmtInt.format(patterns.video_count)} recent videos across your watched
        channels. These numbers feed the script and title prompts directly.
      </p>
    </Card>
  );
}

function ChannelRow({
  channel,
  busy,
  onToggle,
  onRemove,
}: {
  channel: GenreChannel;
  busy: boolean;
  onToggle: (c: GenreChannel) => void;
  onRemove: (c: GenreChannel) => void;
}) {
  return (
    <li className="flex items-center gap-4 border-b border-[var(--color-line)] px-4 py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-semibold">
          {channel.label || channel.youtube_channel_id}
          {!channel.active && (
            <span className="ml-2 rounded-full border border-[var(--color-line)] px-2 py-0.5 align-middle text-[12px] font-normal text-[var(--color-faint)]">
              paused
            </span>
          )}
        </p>
        <p className="mono truncate text-[12px] text-[var(--color-faint)]">
          {channel.video_count} videos
          {channel.last_synced_at && ` · synced ${channel.last_synced_at.slice(0, 10)}`}
        </p>
        {channel.last_error && (
          /* Icon + text, not just colour — the state must read without hue. */
          <p className="text-[12px]" style={{ color: "var(--color-bad)" }}>
            ⚠ last sweep failed: {channel.last_error}
          </p>
        )}
      </div>
      <Button variant="ghost" disabled={busy} onClick={() => onToggle(channel)}>
        {channel.active ? "Pause" : "Resume"}
      </Button>
      <Button variant="ghost" disabled={busy} onClick={() => onRemove(channel)}>
        Remove
      </Button>
    </li>
  );
}

export function GenreView({
  initialWatchlist,
  initialPatterns,
  demo,
}: {
  initialWatchlist: GenreWatchlist;
  initialPatterns: GenrePatterns;
  demo: boolean;
}) {
  const [channels, setChannels] = useState(initialWatchlist.channels);
  const [patterns, setPatterns] = useState(initialPatterns);
  const [handle, setHandle] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setError("");
    try {
      setNote(await action());
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const add = () =>
    run(async () => {
      if (!handle.trim()) return "";
      const added = await watchChannel({ handle: handle.trim() });
      setHandle("");
      setChannels(
        [
          added.channel,
          ...channels.filter(
            (c) => c.youtube_channel_id !== added.channel.youtube_channel_id,
          ),
        ],
      );
      return `Watching ${added.channel.label || added.channel.youtube_channel_id}`;
    });

  const sync = () =>
    run(async () => {
      const result = await syncGenre();
      // Re-read after a sweep so the narrative reflects what arrived.
      const fresh = await getGenrePatterns();
      if (fresh) setPatterns(fresh);
      return `${result.channels_synced} channels swept · ${result.videos_new} new videos`;
    });

  const toggle = (c: GenreChannel) =>
    run(async () => {
      await toggleChannel(c.youtube_channel_id, !c.active);
      setChannels(
        channels.map((x) =>
          x.youtube_channel_id === c.youtube_channel_id ? { ...x, active: !c.active } : x,
        ),
      );
      return "";
    });

  const remove = (c: GenreChannel) =>
    run(async () => {
      if (!window.confirm(`Stop watching ${c.label || c.youtube_channel_id}?`)) {
        return "";
      }
      await unwatchChannel(c.youtube_channel_id);
      setChannels(channels.filter((x) => x.youtube_channel_id !== c.youtube_channel_id));
      return "";
    });

  return (
    <div className="space-y-6">
      <PatternsNarrative patterns={patterns} />

      {(note || error) && (
        <p
          className="text-[13px]"
          style={{ color: error ? "var(--color-bad)" : "var(--color-muted)" }}
          role="status"
        >
          {error || note}
        </p>
      )}

      <Card>
        <div className="flex items-center justify-between px-4 pt-4 pb-1">
          <h2 className="text-[14px] font-semibold">Watchlist</h2>
          <Button onClick={sync} disabled={busy || demo}>
            Sync
          </Button>
        </div>

        {channels.length > 0 ? (
          <>
            <ul>
              {channels.map((c) => (
                <ChannelRow
                  key={c.youtube_channel_id}
                  channel={c}
                  busy={busy}
                  onToggle={toggle}
                  onRemove={remove}
                />
              ))}
            </ul>
            <form
              className="flex gap-2 px-4 py-3"
              onSubmit={(e) => {
                e.preventDefault();
                void add();
              }}
            >
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder="@handle or channel id"
                aria-label="Channel to watch"
                className="mono w-full rounded-[var(--radius-input)] border border-[var(--color-line)] bg-transparent px-3 py-2 text-[13px] outline-none focus:border-[var(--color-line-hover)]"
              />
              <Button variant="ghost" type="submit" disabled={busy || demo || !handle.trim()}>
                Watch
              </Button>
            </form>
          </>
        ) : (
          <Empty
            title="Nobody watched yet"
            hint="Add the channels that own your niche. Studio pulls their recent uploads nightly (~1 quota unit each) and learns which hooks, lengths and cadence win — then feeds that evidence into your scripts and titles."
          >
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void add();
              }}
            >
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder="@handle or channel id"
                aria-label="Channel to watch"
                className="mono w-64 rounded-[var(--radius-input)] border border-[var(--color-line)] bg-transparent px-3 py-2 text-[13px] outline-none focus:border-[var(--color-line-hover)]"
              />
              <Button variant="ghost" type="submit" disabled={demo || !handle.trim()}>
                Watch
              </Button>
            </form>
          </Empty>
        )}
      </Card>
    </div>
  );
}
