"use client";

import { useState } from "react";
import { Header, Page, Card, Button } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { CHANNEL_LAUNCH, MANUAL_STEPS } from "@/lib/demo";

/** New channel — one input, everything else derived.
 *
 *  The honest framing matters here and is built into the screen: YouTube has no API
 *  for creating a channel. This designs the whole identity and validates it against
 *  the real limits, then hands over a short checklist of the clicks no API can make.
 *  Anything that pretended otherwise would fail at the worst possible moment.
 */
export default function NewChannelPage() {
  const [niche, setNiche] = useState("");
  const [result, setResult] = useState<typeof CHANNEL_LAUNCH | null>(null);

  if (!result) {
    return (
      <>
        <Header
          title="New channel"
          meta={
            <span className="flex items-center gap-2">
              {/* The launch endpoint exists; this screen does not call it yet. */}
              <LiveBadge live={false} />
            </span>
          }
        />
        <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[680px] flex-col justify-center px-8 pb-24">
          <label htmlFor="niche" className="text-[15px] text-[var(--color-muted)]">
            What should the channel be about?
          </label>

          <input
            id="niche"
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && niche.length > 2 && setResult(CHANNEL_LAUNCH)}
            autoFocus
            placeholder="how large structures fail"
            className="mt-3 w-full border-b border-[var(--color-line)] bg-transparent pb-3 text-[28px] font-semibold outline-none transition-colors duration-150 placeholder:text-[var(--color-faint)] focus:border-[var(--color-accent)]"
          />

          <div className="mt-6 flex items-center justify-between">
            <p className="text-[13px] text-[var(--color-faint)]">
              Name, handle, About text, keywords, look, series and 30 video ideas.
            </p>
            <Button onClick={() => setResult(CHANNEL_LAUNCH)} disabled={niche.length < 3}>
              Design it
            </Button>
          </div>

          <p className="mt-8 text-[13px] leading-relaxed text-[var(--color-faint)]">
            Grounded in what people actually search for in this niche and what already
            ranks — not invented. YouTube has no API for creating a channel, so the
            last few clicks are yours; everything else is done here.
          </p>
        </div>
      </>
    );
  }

  const c = result.identity;

  return (
    <>
      <Header
        title={c.name}
        meta={<span className="mono">@{c.handle}</span>}
        action={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setResult(null)}>
              Start over
            </Button>
            {/* Disabled rather than removed: it is the honest next step out of this
                screen, and it lands with the series endpoint (KNOWN-ISSUES §5.5).
                Live-and-inert would claim the series above had been created. */}
            <Button
              disabled
              title="Creating these series needs the series endpoint, which does not exist yet."
            >
              Create series
            </Button>
          </div>
        }
      />
      <Page>
        {/* The manual steps come first. They are the blocking path, and burying them
            below the generated content would be quietly dishonest. */}
        <Card className="border-[var(--color-warn)]/40 p-5">
          <h2 className="text-[14px] font-semibold">Do these yourself first</h2>
          <p className="mt-1 text-[12px] text-[var(--color-faint)]">
            YouTube has no API for any of this. Everything below is already done.
          </p>
          <ol className="mt-4 grid gap-3">
            {MANUAL_STEPS.map((step, i) => (
              <li key={step.id} className="flex gap-3">
                <span className="mono mt-0.5 size-5 shrink-0 rounded-full border border-[var(--color-line-hover)] text-center text-[11px] leading-[18px] text-[var(--color-muted)]">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold">{step.title}</p>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--color-faint)]">
                    {step.detail}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <Field label="Handle" value={`@${c.handle}`} note={`${c.handle.length}/30`} />
          <Field label="Tagline" value={c.tagline} note={`${c.tagline.length}/60`} />
        </section>

        <section className="mt-4">
          <Block
            label="About"
            note={`${c.description.length}/1000 characters`}
            body={c.description}
          />
        </section>

        <section className="mt-4">
          <Block
            label="Channel keywords"
            note={`${c.keywordsString.length}/500 characters · ${c.keywords.length} keywords`}
            body={c.keywordsString}
            mono
          />
        </section>

        <section className="mt-8 grid gap-4 lg:grid-cols-2">
          <Card className="p-5">
            <p className="text-[12px] text-[var(--color-faint)]">Avatar</p>
            <div className="mt-3 flex items-center gap-4">
              <div
                className="size-[98px] shrink-0 rounded-full"
                style={{ background: c.palette[0] }}
              />
              <p className="text-[12px] leading-relaxed text-[var(--color-muted)]">
                {c.avatarConcept}
              </p>
            </div>
            <p className="mt-3 text-[11px] text-[var(--color-faint)]">
              Shown at true size — 98px is where it actually lives.
            </p>
          </Card>

          <Card className="p-5">
            <p className="text-[12px] text-[var(--color-faint)]">Banner</p>
            <div
              className="relative mt-3 aspect-[2048/1152] w-full overflow-hidden rounded"
              style={{ background: c.palette[1] }}
            >
              <div
                className="absolute inset-0 m-auto border border-dashed border-white/40"
                style={{ width: "60.3%", height: "29.3%" }}
              />
            </div>
            <p className="mt-3 text-[11px] text-[var(--color-faint)]">
              Dashed box is the safe area — the only part visible on every device.
            </p>
          </Card>
        </section>

        <section className="mt-8">
          <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
            Series
          </h2>
          <div className="grid gap-2.5">
            {result.series.map((s) => (
              <Card key={s.name} className="flex items-center gap-4 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-semibold">{s.name}</p>
                  <p className="mt-1 text-[12px] text-[var(--color-faint)]">{s.pattern}</p>
                </div>
                <span className="mono shrink-0 text-[12px] text-[var(--color-muted)]">
                  {s.perWeek}/week · {s.format}
                </span>
              </Card>
            ))}
          </div>
        </section>

        <section className="mt-8">
          <div className="flex items-baseline justify-between pb-2.5">
            <h2 className="text-[13px] font-semibold text-[var(--color-muted)]">
              First ideas
            </h2>
            <span className="mono text-[12px] text-[var(--color-faint)]">
              {result.backlog.length} usable · {result.rejected} duplicates removed
            </span>
          </div>
          <div className="grid gap-1.5">
            {result.backlog.map((idea, i) => (
              <Card key={idea.topic} className="flex items-center gap-4 px-4 py-2.5">
                <span className="mono w-6 shrink-0 text-[12px] text-[var(--color-faint)]">
                  {i + 1}
                </span>
                <p className="min-w-0 flex-1 truncate text-[13px]">{idea.topic}</p>
                <span className="mono shrink-0 text-[12px] text-[var(--color-muted)]">
                  {idea.score.toFixed(2)}
                </span>
              </Card>
            ))}
          </div>
        </section>
      </Page>
    </>
  );
}

function Field({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between">
        <p className="text-[12px] text-[var(--color-faint)]">{label}</p>
        <span className="mono text-[11px] text-[var(--color-faint)]">{note}</span>
      </div>
      <p className="mt-2 text-[16px] font-semibold">{value}</p>
    </Card>
  );
}

function Block({
  label,
  note,
  body,
  mono = false,
}: {
  label: string;
  note: string;
  body: string;
  mono?: boolean;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between">
        <p className="text-[12px] text-[var(--color-faint)]">{label}</p>
        <span className="mono text-[11px] text-[var(--color-faint)]">{note}</span>
      </div>
      <p
        className={`mt-2.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[var(--color-muted)] ${mono ? "mono" : ""}`}
      >
        {body}
      </p>
    </Card>
  );
}
