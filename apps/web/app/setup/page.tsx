import { Header, Page } from "@/components/ui";
import { getSetup, getDiagnostics } from "@/lib/engine";
import { SetupView } from "./setup-view";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { EngineWaiting } from "./engine-waiting";

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
  // In parallel: two independent reads, and the diagnostics call is the slower of
  // the two even with the network probe off. Awaiting them in sequence would add
  // its whole latency to a page someone is waiting on.
  const [setup, checks] = await Promise.all([
    getSetup(),
    getDiagnostics(false),
  ]);

  if (!setup) {
    return (
      <>
        <Header title="Setup" />
        <Page>
          {/* Which shell to name is decided here, on the server, where the
              platform is actually known — `navigator.platform` in the browser is
              both deprecated and, for a local app, a worse answer. */}
          <EngineWaiting windows={process.platform === "win32"} />
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
        <DiagnosticsPanel initial={checks} />
      </Page>
    </>
  );
}
