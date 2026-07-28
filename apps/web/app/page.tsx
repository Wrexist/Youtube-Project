import { getSetup } from "@/lib/engine";
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

  const ready: Readiness = setup
    ? {
        known: true,
        canRender: setup.can_render,
        missing: setup.missing_required,
      }
    : { known: false, canRender: false, missing: [] };

  return <CreateView ready={ready} />;
}
