import { Header, Page } from "@/components/ui";
import { LiveBadge } from "@/components/live-badge";
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
 * rather than what a default suggests. With no engine it renders the same shape
 * from the engine's own defaults and says it is not live — there is nothing
 * meaningful to demo here, because the whole value is the current value.
 */
export default async function StylePage() {
  const style = await getStyle();

  return (
    <>
      <Header
        title="Style"
        meta={<LiveBadge live={style !== null} />}
      />
      <Page>
        {style ? (
          <StyleView initial={style} />
        ) : (
          <p className="text-[13px] text-[var(--color-muted)]">
            The engine is not running, so there is nothing to change yet. Start it and
            this screen will show what your videos currently sound and look like.
          </p>
        )}
      </Page>
    </>
  );
}
