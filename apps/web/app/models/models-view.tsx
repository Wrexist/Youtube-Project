"use client";

import { useState, useTransition } from "react";
import { Card, Button } from "@/components/ui";
import {
  applyRecommendedRoutes,
  routeEverything,
  routeTask,
  restoreDefaultRoutes,
} from "@/app/actions";

/** The shape both the engine and `lib/demo.ts` are normalised into by the page. */
export interface ModelSpec {
  key: string;
  label: string;
  isLocal: boolean;
  isFree: boolean;
  jsonMode: boolean;
  context: number;
  inputPerM: number;
  outputPerM: number;
}

export interface TaskRoute {
  task: string;
  group: string;
  needs: string;
  quality: string;
  model: string;
}

/**
 * The interactive half of the Models screen.
 *
 * Every change goes to the engine through a Server Action and the page
 * revalidates, so what is on screen is what is in force. Previously the three
 * mutations called `setRoutes` and nothing else: a routing choice lasted until the
 * next reload, and the header quoted a monthly cost for a configuration that had
 * never been sent anywhere.
 *
 * `problems` comes from the engine too. It used to be re-implemented here as a
 * hand-written mirror of `Routing.problems()` in models.py — two copies of one rule
 * set, which drift.
 */
export function ModelsView({
  tasks,
  catalogue,
  problems,
  live,
}: {
  tasks: TaskRoute[];
  catalogue: ModelSpec[];
  problems: { task: string; message: string }[];
  live: boolean;
}) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const specOf = (key: string) => catalogue.find((m) => m.key === key);

  const groups: Record<string, TaskRoute[]> = {};
  for (const t of tasks) (groups[t.group] ??= []).push(t);

  function run(action: () => Promise<{ ok: boolean; error?: string }>) {
    setError(null);
    startTransition(async () => {
      const result = await action();
      // Kept, not swallowed: an optimistic UI that quietly failed to save is the
      // bug this screen had in the first place.
      if (!result.ok) setError(result.error ?? "The change was not saved.");
    });
  }

  const localModel = catalogue.find((m) => m.isLocal)?.key;

  return (
    <>
      {!live && (
        <Card className="mb-6 p-4">
          <p className="text-[12px] text-[var(--color-muted)]">
            The engine is not reachable, so this shows the default routing rather than
            what is in force. Changes are disabled.
          </p>
        </Card>
      )}

      {error && (
        // A plain element, not <Card> — this needs role="alert" so a screen reader
        // announces a failed save, and Card does not forward arbitrary props.
        <div
          role="alert"
          className="mb-6 rounded-[var(--radius-card)] border border-[var(--color-bad)]/40 bg-[var(--color-surface)] p-4"
        >
          <p className="text-[12px] text-[var(--color-bad)]">{error}</p>
        </div>
      )}

      {/* Three presets, all ghost. The screen's one primary action is choosing a
          route, and promoting any of these would compete with eighteen of them.
          Recommended leads on reading order alone, because it is the one that is
          right for most people — Reset restores the built-in defaults whether or
          not the keys to run them exist. */}
      <div className="mb-6 flex flex-wrap gap-2">
        <Button
          variant="ghost"
          disabled={!live || pending}
          onClick={() => run(() => applyRecommendedRoutes())}
          title="Best model for each task, among the providers you have a key for"
        >
          Recommended
        </Button>
        <Button
          variant="ghost"
          disabled={!live || pending || !localModel}
          onClick={() => localModel && run(() => routeEverything(localModel))}
          title="Route everything to a local Ollama model — free and private"
        >
          All local
        </Button>
        <Button
          variant="ghost"
          disabled={!live || pending}
          onClick={() => run(() => restoreDefaultRoutes())}
          title="Restore the built-in defaults"
        >
          Reset
        </Button>
      </div>

      {problems.length > 0 && (
        <Card className="mb-6 border-[var(--color-warn)]/40 p-5">
          <p className="text-[13px] font-semibold text-[var(--color-warn)]">
            {problems.length} routing warning{problems.length > 1 ? "s" : ""}
          </p>
          <ul className="mt-2 grid gap-1.5">
            {problems.map((p, i) => (
              <li
                key={i}
                className="text-[12px] leading-relaxed text-[var(--color-muted)]"
              >
                <span className="mono text-[var(--color-faint)]">{p.task}</span> —{" "}
                {p.message}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            These are warnings, not errors. Running everything locally is a legitimate
            choice — this is just what it costs you.
          </p>
        </Card>
      )}

      {Object.entries(groups).map(([group, groupTasks]) => (
        <section key={group} className="mb-8">
          <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
            {group}
          </h2>
          <div className="grid gap-1.5">
            {groupTasks.map((t) => {
              const spec = specOf(t.model);
              const flagged = problems.some((p) => p.task === t.task);
              return (
                <Card
                  key={t.task}
                  className="flex flex-wrap items-center gap-4 px-4 py-3"
                >
                  <div className="min-w-[180px] flex-1">
                    <p className="text-[13px] font-semibold">{t.task}</p>
                    <p className="mono mt-0.5 text-[11px] text-[var(--color-faint)]">
                      {t.needs}
                      {t.quality === "critical" && " · high leverage"}
                    </p>
                  </div>

                  <select
                    value={t.model}
                    disabled={!live || pending}
                    onChange={(e) => run(() => routeTask(t.task, e.target.value))}
                    aria-label={`Model for ${t.task}`}
                    className="min-w-[220px] rounded-[var(--radius-btn)] border bg-[var(--color-bg)] px-2.5 py-1.5 text-[12px] transition-colors duration-150 disabled:opacity-50"
                    style={{
                      borderColor: flagged ? "var(--color-warn)" : "var(--color-line)",
                    }}
                  >
                    {/* A model the engine routes to but that is not in the catalogue
                        would otherwise render as a blank select. */}
                    {!spec && <option value={t.model}>{t.model}</option>}
                    {catalogue.map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.label}
                        {m.isFree ? " · free" : ""}
                      </option>
                    ))}
                  </select>

                  <span className="mono w-24 shrink-0 text-right text-[11px] text-[var(--color-faint)]">
                    {!spec
                      ? "—"
                      : spec.isFree
                        ? "local"
                        : `$${(spec.inputPerM + spec.outputPerM).toFixed(2)}/M`}
                  </span>
                </Card>
              );
            })}
          </div>
        </section>
      ))}
    </>
  );
}
