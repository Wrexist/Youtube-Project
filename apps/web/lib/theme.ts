/**
 * The theme toggle the command palette exposes.
 *
 * `app/globals.css` already has both halves of this — `:root[data-theme="light"]`
 * and `:root[data-theme="dark"]` override the `prefers-color-scheme` default —
 * nothing before this needed to *set* the attribute, so nothing did. This is that.
 *
 * Persisted in `localStorage` (not `document.cookie`, not server state): theme is
 * a per-browser display preference with no server-side reader — there is no SSR
 * flash to avoid, since the page renders unstyled-by-theme until this runs, same
 * as any client-only preference — and `localStorage` is the ordinary place for
 * exactly that. This is real application code shipped to the user's own browser,
 * not a sandboxed chat artifact, so the storage APIs restricted there do not apply
 * here.
 */

export type Theme = "light" | "dark";

/** Exported so `app/layout.tsx`'s pre-hydration inline script reads the same key
 *  this module writes — two copies of that string is how they end up looking at
 *  different values after one of them gets renamed. */
export const THEME_STORAGE_KEY = "studio-theme";

/** The applied theme right now — the explicit override if one was set, else
 *  whatever `prefers-color-scheme` resolved to. `null` during SSR: there is no
 *  DOM to read, and callers must not assume a theme before mount. */
export function getTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  if (typeof window === "undefined") return;
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Private browsing / storage disabled — the theme still applies for this
    // load, it just will not survive a reload. Not worth surfacing an error for.
  }
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "light" ? "dark" : "light";
  setTheme(next);
  return next;
}

/** Call once, client-side, before paint if possible — restores an explicit
 *  choice from a previous visit. A missing or corrupt value is silently a no-op,
 *  leaving `prefers-color-scheme` in charge, which is the correct default. */
export function restoreTheme(): void {
  if (typeof window === "undefined") return;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") {
      document.documentElement.dataset.theme = stored;
    }
  } catch {
    // Same as above: storage unavailable is not an error, just no persistence.
  }
}
