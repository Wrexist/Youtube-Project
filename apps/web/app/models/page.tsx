import { Header, Page, Card } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { getModels } from "@/lib/engine";
import { MODEL_CATALOGUE, MODEL_TASKS } from "@/lib/demo";
import { ModelsView, type ModelSpec, type TaskRoute } from "./models-view";

/** Models — which model runs which stage.
 *
 *  Not a settings page in the sense the design system rejects: there is one control
 *  per task and nothing else, and the defaults are good enough to never open it.
 *
 *  The warnings are the point. Routing the critique pass to a 7B is a legitimate
 *  choice; being surprised by the results is not, so the consequence is stated next
 *  to the control rather than buried in docs.
 *
 *  A Server Component reading `GET /v1/models`, so the screen shows the routing
 *  actually in force. It previously seeded `useState` from demo data and never read
 *  the engine at all, which meant the routing shown was not the routing used and
 *  the header quoted a monthly cost for a configuration that existed nowhere.
 */
export default async function ModelsPage() {
  const models = await getModels();
  const live = models !== null;

  const tasks: TaskRoute[] = live
    ? models.tasks.map((t) => ({
        task: t.task,
        group: t.group,
        needs: t.needs,
        quality: t.quality,
        model: t.model,
      }))
    : MODEL_TASKS.map((t) => ({
        task: t.task,
        group: t.group,
        needs: t.needs,
        quality: t.quality,
        model: t.model,
      }));

  // The engine speaks snake_case and the demo fixture camelCase; normalised here so
  // the client component sees exactly one shape.
  const catalogue: ModelSpec[] = live
    ? models.catalogue.map((m) => ({
        key: m.key,
        label: m.label,
        isLocal: m.is_local,
        isFree: m.is_free,
        jsonMode: m.json_mode,
        context: m.context,
        inputPerM: m.input_per_m,
        outputPerM: m.output_per_m,
      }))
    : MODEL_CATALOGUE.map((m) => ({ ...m }));

  const problems = live
    ? models.problems
    : // Offline there is nothing to warn about — the defaults are the defaults.
      [];

  const monthly = estimateMonthly(tasks, catalogue);
  const allLocal =
    tasks.length > 0 &&
    tasks.every((t) => catalogue.find((m) => m.key === t.model)?.isLocal);

  return (
    <>
      <Header
        title="Models"
        meta={
          <span className="mono flex items-center gap-2">
            ~${monthly.toFixed(2)}/month at 30 videos
            {allLocal && " · fully local"}
            <LiveBadge live={live} />
          </span>
        }
      />
      <Page>
        <ModelsView tasks={tasks} catalogue={catalogue} problems={problems} live={live} />

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

/** Rough: ~30 videos a month at ~40k tokens each across the chain. */
function estimateMonthly(tasks: TaskRoute[], catalogue: ModelSpec[]): number {
  const perVideo = tasks.reduce((sum, t) => {
    const spec = catalogue.find((m) => m.key === t.model);
    if (!spec) return sum;
    return sum + (spec.inputPerM * 30000 + spec.outputPerM * 10000) / 1_000_000;
  }, 0);
  return perVideo * 30;
}
