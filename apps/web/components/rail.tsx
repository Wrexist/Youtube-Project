"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const ITEMS = [
  { href: "/", label: "Create", icon: "M12 5v14M5 12h14" },
  { href: "/queue", label: "Queue", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/library", label: "Library", icon: "M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z" },
  { href: "/calendar", label: "Calendar", icon: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4" },
  { href: "/analytics", label: "Analytics", icon: "M4 20V10M10 20V4M16 20v-8M22 20H2" },
];

/** 64px icon rail, expanding to 220px on hover. The command palette carries
 *  everything else, which is what lets each screen stay sparse. */
export function Rail() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

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
