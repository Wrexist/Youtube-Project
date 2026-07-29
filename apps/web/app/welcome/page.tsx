import { getSetup } from "@/lib/engine";
import { WelcomeFlow } from "./welcome-flow";

/** Welcome — the first five minutes.
 *
 *  A guided sequence rather than a screen, because the thing being asked for is a
 *  sequence: fetch a key from a site you may not have an account on, come back,
 *  paste it, do it again, then decide whether you also want publishing. Presenting
 *  that as one form of seven fields is how people close the tab.
 *
 *  It is genuinely skippable at every step, and it never blocks. Someone who wants
 *  to look around first should be able to, and the Setup screen has all of the same
 *  controls without the ceremony — this is the polite version, not the only version.
 *
 *  With no engine there is nothing to configure and nothing honest to show, so it
 *  says so rather than walking someone through a form whose Save cannot work.
 */
export default async function WelcomePage() {
  const setup = await getSetup();
  return <WelcomeFlow setup={setup} />;
}
