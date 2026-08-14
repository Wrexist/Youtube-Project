"use client";

/**
 * ⌘K. `docs/UI-DESIGN.md` leans on this to keep every screen sparse — "everything
 * else is secondary or hidden behind ⌘K" — and the rail has said "⌘K to search"
 * since the day it was built, pointing at a control that did not exist yet.
 *
 * Four kinds of thing live in one flat, filterable list rather than four separate
 * UIs, because the point of a command palette is that you type and land, not that
 * you first pick which of four palettes you meant:
 *
 *   - **Actions** — new video, toggle theme.
 *   - **Screens** — every route in the rail, plus Setup. Shares `lib/nav-items.ts`
 *     with `rail.tsx` so the two cannot drift into listing different screens.
 *   - **Videos** — jump to any rendered video by topic, live from `GET /v1/jobs`
 *     with the same demo-data fallback every other screen uses when the engine is
 *     unreachable, so the palette works the same whether or not `npm start` also
 *     started the engine.
 *
 * What this deliberately does not have: a "run a series" command. The design
 * brief names one, but there is no series to run — `docs/UI-DESIGN.md`'s Series
 * screen is demo-only with no backing table (KNOWN-ISSUES.md §5.5), and this
 * codebase's own rule, stated in `queue/page.tsx`, is that a control doing
 * nothing is worse than no control. "Open Series" (a screen, already listed)
 * is the honest version of that command until a series actually exists to run.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { LIBRARY } from "@/lib/demo";
import { getJobs } from "@/lib/engine";
import { NAV_ITEMS, SETUP_ITEM } from "@/lib/nav-items";
import { toggleTheme } from "@/lib/theme";

type Group = "Action" | "Screen" | "Video";

interface Command {
  id: string;
  label: string;
  hint: string;
  group: Group;
  keywords?: string;
  run: () => void;
}

const GROUP_ORDER: Group[] = ["Action", "Screen", "Video"];

export function CommandPalette() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [videos, setVideos] = useState<{ id: string; title: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Reset on close so it reopens fresh rather than mid-search on whatever was
  // last typed — a command palette that remembers your last query is a maze.
  // Done in the handler that closes it, not an effect keyed on `open`: this *is*
  // the event, so responding to it synchronously here is correct — a
  // set-state-in-effect is the smell for reacting to a change after the fact,
  // which this specifically is not. One function for both ways this closes
  // (Escape/overlay-click, which Radix routes through `onOpenChange`, and the
  // ⌘K listener below) — two copies of "and also clear the query" is how one of
  // them stops doing it after the next edit.
  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setQuery("");
      setActiveIndex(0);
    }
  }

  // Read inside the keydown listener below, which is registered once (`[]`
  // deps) so it always sees the render it was created in unless it reads through
  // a ref instead. Synced in an effect, not assigned inline during render —
  // writing to a ref while rendering is a render-purity violation (flagged by
  // `react-hooks/refs`) even though the read side effect is harmless in practice.
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Global trigger. Works from any screen, which is the entire point — it is what
  // lets every screen stay sparse instead of surfacing its own "everything else".
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isK = event.key === "k" || event.key === "K";
      if (isK && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        handleOpenChange(!openRef.current);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Fetched on open, not on mount: this renders on every screen including ones
  // where nobody ever opens the palette, and a job list nobody asked for is a
  // request the engine answers for nothing.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getJobs().then((jobs) => {
      if (cancelled) return;
      setVideos(
        jobs !== null
          ? jobs.filter((j) => j.topic).map((j) => ({ id: j.id, title: j.topic }))
          : LIBRARY.map((v) => ({ id: v.id, title: v.title })),
      );
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const actions: Command[] = [
      {
        id: "action:new-video",
        label: "New video",
        hint: "Start from a blank topic",
        group: "Action",
        run: () => router.push("/"),
      },
      {
        id: "action:toggle-theme",
        label: "Toggle theme",
        hint: "Light / dark",
        group: "Action",
        keywords: "light dark appearance",
        run: () => toggleTheme(),
      },
    ];

    const screens: Command[] = [...NAV_ITEMS, SETUP_ITEM].map((item) => ({
      id: `screen:${item.href}`,
      label: item.label,
      hint: item.href,
      group: "Screen",
      run: () => router.push(item.href),
    }));

    // Capped: a palette that renders 4,000 rows is not faster to search than
    // scrolling the Library, and every one of them is a DOM node built for a list
    // most queries never reach the end of.
    const videoCommands: Command[] = videos.slice(0, 100).map((video) => ({
      id: `video:${video.id}`,
      label: video.title,
      hint: "Open in Create",
      group: "Video",
      run: () => router.push(`/?job=${encodeURIComponent(video.id)}`),
    }));

    return [...actions, ...screens, ...videoCommands];
  }, [router, videos]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      `${c.label} ${c.keywords ?? ""}`.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // `activeIndex` only ever moves by explicit user action (typing, arrowing,
  // hovering) — never synced from a result-count change via an effect. Clamped
  // here instead: a query that shrinks the result set from 6 rows to 3 must not
  // leave the highlight (and Enter) pointing at a row that no longer exists, and
  // computing that from the current render's `filtered.length` is simpler and one
  // render sooner than an effect chasing it after the fact.
  const clampedIndex = filtered.length === 0 ? -1 : Math.min(activeIndex, filtered.length - 1);

  function onQueryChange(next: string) {
    setQuery(next);
    setActiveIndex(0);
  }

  function select(command: Command | undefined) {
    if (!command) return;
    command.run();
    setOpen(false);
  }

  function onInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (filtered.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex(Math.min(clampedIndex + 1, filtered.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex(Math.max(clampedIndex - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      select(filtered[clampedIndex]);
    }
  }

  // The active row is kept in view under keyboard navigation — without this,
  // arrowing past the visible window moves the highlight somewhere the user
  // cannot see it move.
  useEffect(() => {
    const row = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    // Optional call, not just optional chain on `row`: jsdom (the test
    // environment) has no scroll layout engine and does not implement this
    // method at all, on any element.
    row?.scrollIntoView?.({ block: "nearest" });
  }, [clampedIndex]);

  // Same reason the rail hides here (`rail.tsx`): every destination is a screen
  // that does not work yet on the install being set up, so a search box that can
  // navigate away from the one screen the welcome flow insists on is a way to get
  // lost mid-setup.
  if (pathname === "/welcome") return null;

  let rowIndex = -1;

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="palette-overlay fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          className="palette-content fixed top-[18vh] left-1/2 z-50 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-[var(--radius-modal)] border border-[var(--color-line)] bg-[var(--color-surface)]"
          onOpenAutoFocus={(event) => {
            // Radix would otherwise focus the content div; the search input is
            // the only thing worth focusing when this opens.
            event.preventDefault();
            inputRef.current?.focus();
          }}
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">
            Search for a screen, a video, or an action, then press Enter.
          </Dialog.Description>

          <input
            ref={inputRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="Jump to a screen, a video, or an action…"
            aria-label="Command palette search"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={
              filtered[clampedIndex] ? `command-${filtered[clampedIndex].id}` : undefined
            }
            className="w-full border-b border-[var(--color-line)] bg-transparent px-4 py-3.5 text-[14px] text-[var(--color-ink)] outline-none placeholder:text-[var(--color-faint)]"
          />

          <div
            ref={listRef}
            id="command-palette-list"
            role="listbox"
            aria-label="Results"
            className="max-h-[min(60vh,420px)] overflow-y-auto p-2"
          >
            {filtered.length === 0 && (
              <p className="px-2.5 py-6 text-center text-[13px] text-[var(--color-faint)]">
                Nothing matches “{query}”.
              </p>
            )}

            {GROUP_ORDER.map((group) => {
              const rows = filtered.filter((c) => c.group === group);
              if (rows.length === 0) return null;
              return (
                <div key={group} role="group" aria-label={group}>
                  <p className="mono px-2.5 pt-2.5 pb-1 text-[11px] tracking-wide text-[var(--color-faint)] uppercase">
                    {group}
                  </p>
                  {rows.map((command) => {
                    rowIndex += 1;
                    const active = rowIndex === clampedIndex;
                    return (
                      <button
                        key={command.id}
                        id={`command-${command.id}`}
                        role="option"
                        aria-selected={active}
                        data-active={active}
                        type="button"
                        onMouseEnter={() => setActiveIndex(rowIndex)}
                        onClick={() => select(command)}
                        className={`flex w-full items-center justify-between gap-3 rounded-[var(--radius-btn)] px-2.5 py-2 text-left text-[14px] transition-colors duration-100 ${
                          active
                            ? "bg-[var(--color-raised)] text-[var(--color-ink)]"
                            : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
                        }`}
                      >
                        <span className="truncate">{command.label}</span>
                        <span className="mono shrink-0 text-[11px] text-[var(--color-faint)]">
                          {command.hint}
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
