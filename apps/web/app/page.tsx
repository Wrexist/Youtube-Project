import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getJob, getSetup } from "@/lib/engine";
import { ONBOARDED_COOKIE } from "@/lib/onboarding";
import { CreateView, type Readiness } from "./create-view";

/**
 * The Create screen, wrapped so it knows whether this install can render.
 *
 * A thin Server Component over a client one, which is the pattern the rest of the
 * app already uses (`models/page.tsx`, `queue/page.tsx`). It exists for one
 * question: on a fresh clone with no keys, pressing Generate started a job that
 * ran a single stage and died on a provider error. The engine knew that would
 * happen before the click; the first thing the product did was fail anyway, and
 * finding out why meant reading a job log.
 *
 * `known: false` when the engine does not answer. That is deliberately distinct
 * from "not set up" — a stopped engine must not be reported as missing keys, so
 * the screen falls back to the demo pipeline exactly as it did before.
 */
export default async function CreatePage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string }>;
}) {
  const setup = await getSetup();
  // `?job=<id>` reopens a project. Everything needed was already there — jobs are
  // persisted and restored on boot, `GET /v1/jobs/{id}` returns their stages, and
  // "Re-run from here" works — but the id lived only in Create's client state, so
  // a reload or a click on any other screen lost the project for good. There was
  // no way back to a video you had already paid to make.
  const { job } = await searchParams;

  // A genuinely fresh install goes to the welcome flow instead. The condition is
  // narrow on purpose: *nothing at all* configured, and the tour not yet
  // dismissed. A half-configured install has clearly already met this and is
  // better served by the Create screen's inline prompt, and an unreachable engine
  // must never trigger it — being unable to ask is not the same as the answer
  // being "new here".
  // Not when reopening a project: arriving with a job id is proof this install
  // has already made something, whatever the credential check happens to say.
  if (!job && setup && setup.credentials.every((c) => !c.configured)) {
    const jar = await cookies();
    if (!jar.get(ONBOARDED_COOKIE)) redirect("/welcome");
  }

  const ready: Readiness = setup
    ? {
        known: true,
        canRender: setup.can_render,
        missing: setup.missing_required,
      }
    : { known: false, canRender: false, missing: [] };

  // The topic and format come from the engine rather than from client state,
  // which by definition no longer has them: reopening a project is exactly the
  // case where the browser has forgotten everything. Without this the header
  // showed the demo topic over a real job's pipeline.
  const resumed = job ? await getJob(job) : null;
  // `GET /v1/jobs/{id}` nests these under `inputs` — the list endpoint flattens
  // `topic` to the top level and the detail endpoint does not, which is worth
  // reading twice before trusting either shape.
  const inputs = (resumed?.inputs ?? {}) as { topic?: string; format?: string };

  return (
    <CreateView
      ready={ready}
      resumeJobId={resumed ? job! : null}
      resumeTopic={inputs.topic ?? ""}
      resumeFormat={inputs.format === "long" ? "long" : "short"}
    />
  );
}
