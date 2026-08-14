/**
 * The screens, in the one order they're declared — `rail.tsx` renders them as
 * the left rail, `command-palette.tsx` renders them as "open any screen".
 *
 * Shared rather than duplicated: two lists of the same seven routes drift the
 * moment a screen is added or renamed, and the rail is the one place this used
 * to live, so the palette copying it by hand was exactly that risk.
 */
export interface NavItem {
  href: string;
  label: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Create", icon: "M12 5v14M5 12h14" },
  { href: "/queue", label: "Queue", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/series", label: "Series", icon: "M4 7h16M4 12h16M4 17h9M18 15v6M15 18h6" },
  { href: "/new-channel", label: "New channel", icon: "M12 8v8M8 12h8M12 3a9 9 0 100 18 9 9 0 000-18z" },
  // Beside Library because it produces Library entries — it sits with the content
  // screens, not the configuration ones. Scissors: the one glyph that reads as
  // "cut a clip out of something" at 20px.
  {
    href: "/repurpose",
    label: "Repurpose",
    icon: "M6 4l12 12M6 20L18 8M8 6a2 2 0 11-4 0 2 2 0 014 0zM8 18a2 2 0 11-4 0 2 2 0 014 0z",
  },
  { href: "/library", label: "Library", icon: "M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z" },
  { href: "/calendar", label: "Calendar", icon: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4" },
  { href: "/analytics", label: "Analytics", icon: "M4 20V10M10 20V4M16 20v-8M22 20H2" },
  {
    href: "/models",
    label: "Models",
    icon: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  },
  // Beside Models, because it answers the same shape of question — Models decides
  // which model writes the video, Style decides what the video sounds and looks
  // like. A sound wave: the one glyph that reads as "voice" at 20px.
  { href: "/style", label: "Style", icon: "M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" },
];

/** Pinned to the bottom, away from the daily-use items. It is the first screen a
 *  new install needs and roughly the last one anybody opens again. */
export const SETUP_ITEM: NavItem = {
  href: "/setup",
  label: "Setup",
  icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1.08-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1.08 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z",
};
