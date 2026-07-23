"use client";

import { useMemo, useState } from "react";
import { Header, Page, Card, Button } from "@/components/ui";
import { MODEL_CATALOGUE, MODEL_TASKS, type ModelKey } from "@/lib/demo";

/** Models — which model runs which stage.
 *
 *  Not a settings page in the sense the design system rejects: there is one control
 *  per task and nothing else, and the defaults are good enough to never open it.
 *
 *  The warnings are the point. Routing the critique pass to a 7B is a legitimate
 *  choice; being surprised by the results is not, so the consequence is stated next
 *  to the control rather than buried in docs.
 */
export default function ModelsPage() {
  const [routes, setRoutes] = useState<Record<string, ModelKey>>(
    Object.fromEntries(MODEL_TASKS.map((t) => [t.task, t.model])),
  );

  const groups = useMemo(() => {
    const out: Record<string, typeof MODEL_TASKS> = {};
    for (const t of MODEL_TASKS) (out[t.group] ??= []).push(t);
    return out;
  }, []);

  const specOf = (key: string) => MODEL_CATALOGUE.find((m) => m.key === key)!;

  /** Mirrors Routing.problems() in engine/models.py. */
  const problems = useMemo(() => {
    const out: { task: string; message: string }[] = [];
    for (const t of MODEL_TASKS) {
      const spec = specOf(routes[t.task]);
      if (t.needs.includes("JSON") && !spec.jsonMode)
        out.push({
          task: t.task,
          message: `${spec.label} is unreliable at strict JSON, which this task requires. Expect retries and occasional stage failures.`,
        });
      if (t.quality === "critical" && spec.isLocal && spec.context < 32000)
        out.push({
          task: t.task,
          message: `${t.task} is one of the stages that decides whether a video works. A small local model here saves pennies and costs views.`,
        });
      if (t.needs.includes("long output") && spec.context < 16000)
        out.push({
          task: t.task,
          message: `${spec.label} has a ${spec.context.toLocaleString()}-token context; long-form drafts will be truncated.`,
        });
    }
    return out;
  }, [routes]);

  const monthly = useMemo(() => {
    // Rough: ~30 videos a month at ~40k tokens each across the chain.
    const perVideo = MODEL_TASKS.reduce((sum, t) => {
      const s = specOf(routes[t.task]);
      return sum + (s.inputPerM * 30000 + s.outputPerM * 10000) / 1_000_000;
    }, 0);
    return perVideo * 30;
  }, [routes]);

  const allLocal = MODEL_TASKS.every((t) => specOf(routes[t.task]).isLocal);

  return (
    <>
      <Header
        title="Models"
        meta={
          <span className="mono">
            ~${monthly.toFixed(2)}/month at 30 videos
            {allLocal && " · fully local"}
          </span>
        }
        action={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() =>
                setRoutes(
                  Object.fromEntries(
                    MODEL_TASKS.map((t) => [
                      t.task,
                      "ollama:qwen2.5:14b" as ModelKey,
                    ]),
                  ),
                )
              }
            >
              All local
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setRoutes(
                  Object.fromEntries(MODEL_TASKS.map((t) => [t.task, t.model])),
                )
              }
            >
              Reset
            </Button>
          </div>
        }
      />
      <Page>
        {problems.length > 0 && (
          <Card className="mb-6 border-[var(--color-warn)]/40 p-5">
            <p className="text-[13px] font-semibold text-[var(--color-warn)]">
              {problems.length} routing warning{problems.length > 1 ? "s" : ""}
            </p>
            <ul className="mt-2 grid gap-1.5">
              {problems.map((p, i) => (
                <li key={i} className="text-[12px] leading-relaxed text-[var(--color-muted)]">
                  <span className="mono text-[var(--color-faint)]">{p.task}</span>{" "}
                  — {p.message}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[12px] text-[var(--color-faint)]">
              These are warnings, not errors. Running everything locally is a
              legitimate choice — this is just what it costs you.
            </p>
          </Card>
        )}

        {Object.entries(groups).map(([group, tasks]) => (
          <section key={group} className="mb-8">
            <h2 className="pb-2.5 text-[13px] font-semibold text-[var(--color-muted)]">
              {group}
            </h2>
            <div className="grid gap-1.5">
              {tasks.map((t) => {
                const spec = specOf(routes[t.task]);
                const flagged = problems.some((p) => p.task === t.task);
                return (
                  <Card key={t.task} className="flex flex-wrap items-center gap-4 px-4 py-3">
                    <div className="min-w-[180px] flex-1">
                      <p className="text-[13px] font-semibold">{t.task}</p>
                      <p className="mono mt-0.5 text-[11px] text-[var(--color-faint)]">
                        {t.needs}
                        {t.quality === "critical" && " · high leverage"}
                      </p>
                    </div>

                    <select
                      value={routes[t.task]}
                      onChange={(e) =>
                        setRoutes({ ...routes, [t.task]: e.target.value as ModelKey })
                      }
                      aria-label={`Model for ${t.task}`}
                      className="min-w-[220px] rounded-[var(--radius-btn)] border bg-[var(--color-bg)] px-2.5 py-1.5 text-[12px] transition-colors duration-150"
                      style={{
                        borderColor: flagged
                          ? "var(--color-warn)"
                          : "var(--color-line)",
                      }}
                    >
                      {MODEL_CATALOGUE.map((m) => (
                        <option key={m.key} value={m.key}>
                          {m.label}
                          {m.isFree ? " · free" : ""}
                        </option>
                      ))}
                    </select>

                    <span className="mono w-24 shrink-0 text-right text-[11px] text-[var(--color-faint)]">
                      {spec.isFree
                        ? "local"
                        : `$${(spec.inputPerM + spec.outputPerM).toFixed(2)}/M`}
                    </span>
                  </Card>
                );
              })}
            </div>
          </section>
        ))}

        <Card className="p-5">
          <h2 className="text-[13px] font-semibold">Local models</h2>
          <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-muted)]">
            Ollama models are detected automatically and cost nothing, so a fully
            local pipeline never touches the spend ceiling. They are meaningfully
            worse at the strict JSON most stages demand — Ollama&apos;s constrained
            decoding helps, but a 7B will still fail where a frontier model
            won&apos;t.
          </p>
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            A reasonable split: local for tags, chapters, series and backlog; a
            frontier model for hook, draft, critique and titles.
          </p>
          <pre className="mono mt-3 overflow-x-auto rounded bg-[var(--color-raised)] p-3 text-[11px] text-[var(--color-muted)]">
{`ollama serve
ollama pull qwen2.5:14b`}
          </pre>
        </Card>
      </Page>
    </>
  );
}
