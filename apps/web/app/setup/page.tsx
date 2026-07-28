import { Header, Page, Card } from "@/components/ui";
import { getSetup } from "@/lib/engine";
import { SetupView } from "./setup-view";

/** Setup — the screen that turns a fresh clone into a working install.
 *
 *  The one screen the design system's "no settings page with 40 toggles" rule does
 *  *not* argue against, because none of this is a preference. Every field is a
 *  credential that varies per install and that nobody can pick a default for. The
 *  toggles that rule is about — render behaviour, model routing, scheduling — have
 *  opinionated defaults and live elsewhere.
 *
 *  It replaces the old first-run experience, which was: read SETUP.md, find eleven
 *  environment variables listed without saying which three matter, create `.env` by
 *  hand, restart the engine, and find out whether it worked by generating a video
 *  and watching which stage failed. The status line at the top of this screen is
 *  the answer to the only question someone actually has, which is "can I use this
 *  yet".
 *
 *  A Server Component: the status has to be what the engine currently believes, not
 *  what the browser last heard. With no engine there is nothing honest to show —
 *  the whole screen is a report about a process that is not running — so it says so
 *  rather than falling back to a fixture, which is the one case where the demo-data
 *  fallback the other screens use would be an outright lie.
 */
export default async function SetupPage() {
  const setup = await getSetup();

  if (!setup) {
    return (
      <>
        <Header title="Setup" />
        <Page>
          <Card className="p-6">
            <h2 className="text-[15px] font-semibold">The engine is not running</h2>
            <p className="mt-2 max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-muted)]">
              This screen reads and writes the engine&apos;s configuration, so there
              is nothing to show until it is up. Start it and reload.
            </p>
            <pre className="mono mt-4 overflow-x-auto rounded-[var(--radius-btn)] bg-[var(--color-raised)] p-3 text-[11px] leading-relaxed text-[var(--color-muted)]">
              {`# from the repository root
apps/engine/.venv/bin/python -m uvicorn engine.main:app --reload --port 8080

# on Windows
.\\apps\\engine\\.venv\\Scripts\\python -m uvicorn engine.main:app --reload --port 8080`}
            </pre>
            <p className="mt-4 text-[12px] text-[var(--color-faint)]">
              Never run the setup script? <span className="mono">scripts/setup.sh</span>{" "}
              on macOS and Linux, <span className="mono">.\scripts\setup.ps1</span> on
              Windows. It installs everything and starts nothing.
            </p>
          </Card>
        </Page>
      </>
    );
  }

  const ready = setup.can_render;

  return (
    <>
      <Header
        title="Setup"
        meta={
          <span className="flex items-center gap-2">
            <span
              className="size-2 rounded-full"
              style={{
                background: ready ? "var(--color-ok)" : "var(--color-warn)",
              }}
              aria-hidden
            />
            {ready ? "Ready to make videos" : "Not ready yet"}
          </span>
        }
      />
      <Page>
        <SetupView setup={setup} />
      </Page>
    </>
  );
}
