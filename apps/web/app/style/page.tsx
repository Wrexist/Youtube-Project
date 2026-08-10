import { Header, Page } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
import { DEMO_STYLE } from "@/lib/demo";
import { getStyle } from "@/lib/engine";
import { StyleView } from "./style-view";

/**
 * Style — how every video sounds and looks.
 *
 * Six settings the render engine has honoured since it was written and no screen
 * could reach, so every video this project has produced is narrated by the same
 * voice, silent, in the default font, with hard cuts. Changing any of it meant
 * editing `.env`.
 *
 * Not the settings page the design system rejects. There are three controls —
 * narrator, music, captions — and motion sits behind a disclosure because it is
 * the one most installs will never touch. The other twenty-two `Settings` fields
 * stay where they are: CLAUDE.md says expose the three things that actually vary,
 * and the storage backend is not one of them.
 *
 * A Server Component reading `GET /v1/style`, so the screen shows what is in force
 * rather than what a default suggests.
 *
 * With no engine it falls back to `DEMO_STYLE`, like every other screen. That was
 * a sentence of prose at first, on the reasoning that the whole value here is the
 * *current* value and there is nothing meaningful to demo — which mistook the
 * purpose. CLAUDE.md keeps demo data so the design can be judged before the
 * plumbing exists, and a paragraph explaining that the controls are elsewhere
 * judges nothing. The fixture is the engine's own defaults, so it is also an
 * honest picture of a fresh install, and edits in that mode stay local.
 */
export default async function StylePage() {
  const style = await getStyle();
  const live = style !== null;

  return (
    <>
      <Header
        title="Style"
        meta={
          <span className="flex items-center gap-2">
            <LiveBadge live={live} />
            {!live && (
              <span className="rounded-full border border-[var(--color-line)] px-2 py-0.5 text-[11px] text-[var(--color-faint)]">
                demo data
              </span>
            )}
          </span>
        }
      />
      <Page>
        <StyleView initial={style ?? DEMO_STYLE} demo={!live} />
      </Page>
    </>
  );
}
