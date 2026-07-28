import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getSetup } from "@/lib/engine";
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
export default async function CreatePage() {
  const setup = await getSetup();

  // A genuinely fresh install goes to the welcome flow instead. The condition is
  // narrow on purpose: *nothing at all* configured, and the tour not yet
  // dismissed. A half-configured install has clearly already met this and is
  // better served by the Create screen's inline prompt, and an unreachable engine
  // must never trigger it — being unable to ask is not the same as the answer
  // being "new here".
  if (setup && setup.credentials.every((c) => !c.configured)) {
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

  return <CreateView ready={ready} />;
}
