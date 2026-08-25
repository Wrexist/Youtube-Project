"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { addSeries, editSeries, removeSeries } from "@/app/actions";
import { Button, Card } from "@/components/ui";

/** What the screen renders, whichever side it came from. Live series arrive as
 *  `Series` from the contract; demo fixtures carry the same facts in camelCase.
 *  The page normalises both into this — a display shape, not an API mirror. */
export interface SeriesView {
  id: string;
  name: string;
  niche: string;
  shortsPerWeek: number;
  longPerWeek: number;
  autoPublish: boolean;
  paused: boolean;
  monthlyBudget: number;
  spentThisMonth: number;
  backlogDepth: number;
  producedThisWeek: number;
  /** The run planner's reasons anything was blocked this week. Demo cards
   *  derive a lookalike warning instead — the fixture has no planner. */
  blockers: { code: string; message: string }[];
}

export function SeriesCards({ series, live }: { series: SeriesView[]; live: boolean }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = (fn: () => Promise<{ ok: boolean; error?: string }>) =>
    startTransition(async () => {
      setError(null);
      const result = await fn();
      if (!result.ok) setError(result.error ?? "The engine refused.");
      else router.refresh();
    });

  return (
    <div className="grid gap-3">
      {error && (
        <p role="alert" className="text-[12px] text-[var(--color-bad)]">
          {error}
        </p>
      )}
      {series.map((s) => {
        const pct =
          s.monthlyBudget > 0
            ? Math.min(100, (s.spentThisMonth / s.monthlyBudget) * 100)
            : 0;
        const target = s.shortsPerWeek + s.longPerWeek;
        const tight = pct > 80;
        const done = target > 0 && s.producedThisWeek >= target;

        return (
          <Card key={s.id} className="p-5">
            <div className="flex flex-wrap items-start gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-[15px] font-semibold">{s.name}</h2>
                  {s.paused && <Tag tone="muted">paused</Tag>}
                  {s.autoPublish && <Tag tone="accent">auto-publish</Tag>}
                  {done && !s.paused && <Tag tone="ok">week complete</Tag>}
                </div>
                <p className="mt-1 text-[12px] text-[var(--color-faint)]">{s.niche}</p>

                <p className="mono mt-3 text-[12px] text-[var(--color-muted)]">
                  {s.shortsPerWeek} shorts + {s.longPerWeek} long-form / week ·{" "}
                  {s.producedThisWeek}/{target} made this week
                </p>

                {/* The week as a row of slots — filled ticks over an empty track,
                    readable without color. */}
                {target > 0 && (
                  <div
                    className="mt-2 flex gap-1"
                    role="img"
                    aria-label={`${s.producedThisWeek} of ${target} videos made this week`}
                  >
                    {Array.from({ length: target }, (_, i) => (
                      <span
                        key={i}
                        className={`quest-slot ${i < s.producedThisWeek ? "is-done" : ""}`}
                      >
                        {i < s.producedThisWeek ? "✓" : ""}
                      </span>
                    ))}
                  </div>
                )}

                <div className="mt-3 flex items-center gap-3">
                  <div className="h-1 w-40 overflow-hidden rounded-full bg-[var(--color-raised)]">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${pct}%`,
                        background: tight ? "var(--color-warn)" : "var(--color-muted)",
                      }}
                    />
                  </div>
                  <span className="mono text-[11px] text-[var(--color-faint)]">
                    ${s.spentThisMonth.toFixed(2)} / ${s.monthlyBudget} this month
                  </span>
                </div>

                {s.blockers.map((b) => (
                  <p key={b.code} className="mt-2.5 text-[12px] text-[var(--color-warn)]">
                    {b.message}
                  </p>
                ))}
              </div>

              {live && (
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    variant="ghost"
                    disabled={pending}
                    onClick={() => act(() => editSeries(s.id, { paused: !s.paused }))}
                  >
                    {s.paused ? "Resume" : "Pause"}
                  </Button>
                  {confirming === s.id ? (
                    <Button
                      variant="ghost"
                      disabled={pending}
                      onClick={() => {
                        setConfirming(null);
                        act(() => removeSeries(s.id));
                      }}
                    >
                      Really remove?
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      disabled={pending}
                      onClick={() => setConfirming(s.id)}
                    >
                      Remove
                    </Button>
                  )}
                </div>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

/** The primary action: a small inline form, not a modal — three fields and two
 *  numbers, and the card it creates lands directly underneath. */
export function NewSeriesForm({ live }: { live: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [niche, setNiche] = useState("");
  const [shorts, setShorts] = useState(3);
  const [long, setLong] = useState(1);
  const [budget, setBudget] = useState(30);

  if (!live) {
    return (
      <Button disabled title="The engine is not running — nothing can be created.">
        New series
      </Button>
    );
  }

  if (!open) {
    return <Button onClick={() => setOpen(true)}>New series</Button>;
  }

  const submit = () =>
    startTransition(async () => {
      setError(null);
      const result = await addSeries({
        name: name.trim(),
        niche: niche.trim(),
        shorts_per_week: shorts,
        long_per_week: long,
        monthly_budget_usd: budget,
        auto_publish: false,
      });
      if (!result.ok) {
        setError(result.error ?? "The engine refused.");
        return;
      }
      setOpen(false);
      setName("");
      setNiche("");
      router.refresh();
    });

  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim().length > 0) submit();
      }}
    >
      <Labelled label="Name">
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Failure Files"
          className="w-36 rounded-[var(--radius-card)] border border-[var(--color-line)] bg-transparent px-2.5 py-1.5 text-[13px] outline-none focus:border-[var(--color-accent)]"
        />
      </Labelled>
      <Labelled label="Niche">
        <input
          value={niche}
          onChange={(e) => setNiche(e.target.value)}
          placeholder="engineering disasters"
          className="w-44 rounded-[var(--radius-card)] border border-[var(--color-line)] bg-transparent px-2.5 py-1.5 text-[13px] outline-none focus:border-[var(--color-accent)]"
        />
      </Labelled>
      <Labelled label="Shorts/wk">
        <NumberInput value={shorts} onChange={setShorts} max={21} />
      </Labelled>
      <Labelled label="Long/wk">
        <NumberInput value={long} onChange={setLong} max={7} />
      </Labelled>
      <Labelled label="$/month">
        <NumberInput value={budget} onChange={setBudget} max={10000} />
      </Labelled>
      <Button type="submit" disabled={pending || name.trim().length === 0}>
        Create
      </Button>
      <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {error && (
        <p role="alert" className="w-full text-[12px] text-[var(--color-bad)]">
          {error}
        </p>
      )}
    </form>
  );
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1 text-[11px] text-[var(--color-faint)]">
      {label}
      {children}
    </label>
  );
}

function NumberInput({
  value,
  onChange,
  max,
}: {
  value: number;
  onChange: (n: number) => void;
  max: number;
}) {
  return (
    <input
      type="number"
      min={0}
      max={max}
      value={value}
      onChange={(e) => onChange(Math.max(0, Math.min(max, Number(e.target.value) || 0)))}
      className="mono w-20 rounded-[var(--radius-card)] border border-[var(--color-line)] bg-transparent px-2.5 py-1.5 text-[13px] outline-none focus:border-[var(--color-accent)]"
    />
  );
}

function Tag({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone: "muted" | "accent" | "ok";
}) {
  const color =
    tone === "accent"
      ? "var(--color-accent)"
      : tone === "ok"
        ? "var(--color-ok)"
        : "var(--color-faint)";
  return (
    <span
      className="mono rounded-full border px-2 py-0.5 text-[10px] uppercase"
      style={{
        color,
        borderColor: tone === "muted" ? "var(--color-line-hover)" : color,
      }}
    >
      {children}
    </span>
  );
}
