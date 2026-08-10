"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const ITEMS = [
  { href: "/", label: "Create", icon: "M12 5v14M5 12h14" },
  { href: "/queue", label: "Queue", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/series", label: "Series", icon: "M4 7h16M4 12h16M4 17h9M18 15v6M15 18h6" },
  { href: "/new-channel", label: "New channel", icon: "M12 8v8M8 12h8M12 3a9 9 0 100 18 9 9 0 000-18z" },
  // Beside Library because it produces Library entries — it sits with the content
  // screens, not the configuration ones. Scissors: the one glyph that reads as
  // "cut a clip out of something" at 20px.
  { href: "/repurpose", label: "Repurpose", icon: "M6 4l12 12M6 20L18 8M8 6a2 2 0 11-4 0 2 2 0 014 0zM8 18a2 2 0 11-4 0 2 2 0 014 0z" },
  { href: "/library", label: "Library", icon: "M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z" },
  { href: "/calendar", label: "Calendar", icon: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4" },
  { href: "/analytics", label: "Analytics", icon: "M4 20V10M10 20V4M16 20v-8M22 20H2" },
  { href: "/models", label: "Models", icon: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5" },
  // Beside Models, because it answers the same shape of question — Models decides
  // which model writes the video, Style decides what the video sounds and looks
  // like. A sound wave: the one glyph that reads as "voice" at 20px.
  { href: "/style", label: "Style", icon: "M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" },
];

/** Pinned to the bottom, away from the daily-use items. It is the first screen a
 *  new install needs and roughly the last one anybody opens again. */
const SETUP = {
  href: "/setup",
  label: "Setup",
  icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1.08-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1.08 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z",
};

/** 64px icon rail, expanding to 220px on hover. The command palette carries
 *  everything else, which is what lets each screen stay sparse. */
export function Rail() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // The welcome flow is a takeover. Every destination in this rail is a screen
  // that does not work yet on the install being set up, so offering eight of them
  // beside a four-step sequence is an invitation to wander off mid-setup and an
  // eight-fold chance of landing somewhere that says "demo data".
  if (pathname === "/welcome") return null;

  return (
    <nav
      aria-label="Main"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      className="sticky top-0 h-screen shrink-0 border-r border-[var(--color-line)] bg-[var(--color-surface)] transition-[width] duration-200 ease-[var(--ease-in-out)]"
      style={{ width: open ? 220 : 64 }}
    >
      <div className="flex h-16 items-center gap-3 px-5">
        <span className="size-2.5 shrink-0 rounded-full bg-[var(--color-accent)]" />
        <span
          className="text-sm font-semibold whitespace-nowrap transition-opacity duration-150"
          style={{ opacity: open ? 1 : 0 }}
        >
          Studio
        </span>
      </div>

      <ul className="flex flex-col gap-1 px-3">
        {ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                title={item.label}
                className={`flex items-center gap-4 rounded-[var(--radius-btn)] px-2.5 py-2.5 text-sm transition-colors duration-150 ${
                  active
                    ? "bg-[var(--color-raised)] text-[var(--color-ink)]"
                    : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
                }`}
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  className="size-[19px] shrink-0"
                  aria-hidden
                >
                  <path d={item.icon} />
                </svg>
                <span
                  className="whitespace-nowrap transition-opacity duration-150"
                  style={{ opacity: open ? 1 : 0 }}
                >
                  {item.label}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="absolute bottom-4 left-0 w-full px-3">
        <Link
          href={SETUP.href}
          aria-current={pathname === SETUP.href ? "page" : undefined}
          title={SETUP.label}
          className={`mb-1 flex items-center gap-4 rounded-[var(--radius-btn)] px-2.5 py-2.5 text-sm transition-colors duration-150 ${
            pathname === SETUP.href
              ? "bg-[var(--color-raised)] text-[var(--color-ink)]"
              : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            className="size-[19px] shrink-0"
            aria-hidden
          >
            <path d={SETUP.icon} />
          </svg>
          <span
            className="whitespace-nowrap transition-opacity duration-150"
            style={{ opacity: open ? 1 : 0 }}
          >
            {SETUP.label}
          </span>
        </Link>
        <kbd
          className="mono flex items-center gap-2 rounded-[var(--radius-btn)] px-2.5 py-2 text-[11px] text-[var(--color-faint)]"
          style={{ opacity: open ? 1 : 0 }}
        >
          ⌘K to search
        </kbd>
      </div>
    </nav>
  );
}
