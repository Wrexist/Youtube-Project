import type { Metadata } from "next";
import "./globals.css";
import { CommandPalette } from "@/components/command-palette";
import { Rail } from "@/components/rail";
import { THEME_STORAGE_KEY } from "@/lib/theme";

export const metadata: Metadata = {
  title: "Studio",
  description: "Idea to published video.",
};

// Runs before React hydrates, so an explicit theme choice from a previous visit
// applies before the first paint rather than flashing the prefers-color-scheme
// default for one frame. Inline rather than a `useEffect` in `lib/theme.ts` for
// exactly that reason — an effect runs after paint, which is the flash. The key
// is interpolated from `lib/theme.ts` rather than retyped, so the reader and the
// writer cannot end up looking at two different `localStorage` keys.
const THEME_RESTORE_SCRIPT = `
try {
  var t = window.localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_RESTORE_SCRIPT }} />
      </head>
      <body className="min-h-screen">
        {/* Left rail + content. No top nav bar, no breadcrumbs — the hierarchy is
            one level deep everywhere, so neither earns its pixels. */}
        <div className="flex min-h-screen">
          <Rail />
          <main className="flex-1 min-w-0">{children}</main>
        </div>
        <CommandPalette />
      </body>
    </html>
  );
}
