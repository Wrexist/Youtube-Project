"use client";

import { useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui";
import { engineReady } from "@/app/actions";

/**
 * The screen you get when the engine is not answering yet — which, on a normal
 * start, is a state that ends by itself within seconds.
 *
 * It replaced a card that said "start it and reload", because the commonest way
 * to see that card is *not* a missing engine. `npm start` opens the window as
 * soon as the **web** half answers, and the engine takes several seconds longer
 * on a cold start: Python imports, the SQLite schema check, the quota ledger. So
 * the first paint of this page routinely lands before the engine is up, a Server
 * Component cannot re-render itself when that changes, and the screen sat there
 * telling someone to start a process that was already starting.
 *
 * Now it waits, says so, and lets itself in. `router.refresh()` is deliberately
 * not used: this page's data is fetched by a Server Component whose cache is not
 * the only thing that has changed — a full reload is the honest way to re-run
 * `getSetup()` and `getDiagnostics()` from scratch, and it happens once.
 */
export function EngineWaiting({ windows }: { windows: boolean }) {
  const [live, setLive] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const polling = useRef(false);

  // The engine's own cold start is 3-8s on a warm disk and slower on a cold one,
  // so the bar is calibrated to feel truthful at the low end rather than filling
  // instantly and then sitting at 100% while nothing happens. It is honest about
  // being an estimate: `PATIENCE` is where it stops pretending to know.
  const PATIENCE = 25;
  const progress = live ? 100 : Math.min(95, (elapsed / PATIENCE) * 100);

  useEffect(() => {
    if (live) {
      // One reload, after a beat long enough for the green state to register as
      // an answer rather than a flicker.
      const settle = setTimeout(() => window.location.reload(), 700);
      return () => clearTimeout(settle);
    }

    const tick = setInterval(() => setElapsed((e) => e + 0.5), 500);

    const poll = setInterval(async () => {
      // The same guard the launcher needs: an async callback on an interval keeps
      // being fired during its own await, so without this the requests stack up
      // on a slow answer and arrive in a burst.
      if (polling.current) return;
      polling.current = true;
      try {
        const result = await engineReady();
        if (result.data?.live) setLive(true);
      } catch {
        // A failed action here means the *web* server is unhappy, not the engine.
        // Nothing to report: the next tick tries again.
      } finally {
        polling.current = false;
      }
    }, 1000);

    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [live]);

  const stalled = !live && elapsed > PATIENCE;

  return (
    <Card className="p-6">
      <div className="flex items-center gap-2.5">
        <span
          className="size-2 shrink-0 rounded-full transition-colors duration-300"
          style={{
            background: live
              ? "var(--color-ok)"
              : stalled
                ? "var(--color-warn)"
                : "var(--color-muted)",
          }}
          aria-hidden
        />
        <h2 className="text-[15px] font-semibold">
          {live
            ? "Active"
            : stalled
              ? "Still waiting for the engine"
              : "Starting the engine"}
        </h2>
        <span className="mono ml-auto text-[11px] text-[var(--color-faint)]">
          {live ? "ready" : `${Math.floor(elapsed)}s`}
        </span>
      </div>

      {/* aria-live on the text, not the bar: a screen reader wants the outcome,
          not a percentage read out twice a second. */}
      <div
        className="mt-4 h-1 overflow-hidden rounded-full bg-[var(--color-raised)]"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
        aria-label="Engine startup"
      >
        <div
          className="h-full rounded-full transition-[width,background-color] duration-500 ease-[var(--ease-in-out)]"
          style={{
            width: `${progress}%`,
            background: live ? "var(--color-ok)" : "var(--color-accent)",
          }}
        />
      </div>

      <p
        aria-live="polite"
        className="mt-4 max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-muted)]"
      >
        {live
          ? "The engine is up. Loading your configuration…"
          : stalled
            ? "Longer than it should take. The engine may have failed to start — the Studio console window has the reason, and it is worth reading."
            : "This screen reads the engine's configuration, and the engine takes a few seconds longer to start than the app window does. Nothing to do; it will let itself in."}
      </p>

      {stalled && (
        <>
          <p className="mt-4 text-[12px] text-[var(--color-faint)]">
            To see the error directly, run this from the repository root:
          </p>
          <pre className="mono mt-2 overflow-x-auto rounded-[var(--radius-btn)] bg-[var(--color-raised)] p-3 text-[11px] leading-relaxed text-[var(--color-muted)]">
            {windows
              ? `.\\apps\\engine\\.venv\\Scripts\\python apps\\engine\\scripts\\doctor.py
.\\apps\\engine\\.venv\\Scripts\\python -m uvicorn engine.main:app --port 8080`
              : `apps/engine/.venv/bin/python apps/engine/scripts/doctor.py
apps/engine/.venv/bin/python -m uvicorn engine.main:app --port 8080`}
          </pre>
          <p className="mt-3 text-[12px] text-[var(--color-faint)]">
            The doctor names the single next action for whatever is missing.
            Never run the installer?{" "}
            <span className="mono">scripts/setup.sh</span> on macOS and Linux,{" "}
            <span className="mono">.\scripts\setup.ps1</span> on Windows.
          </p>
        </>
      )}
    </Card>
  );
}
